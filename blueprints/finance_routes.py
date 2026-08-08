"""
blueprints/finance_routes.py
------------------------------
Senkronizasyon tetikleme/durum, aylık kâr, sabit giderler ve Hepsiburada
miktar backfill route'ları. Kod app.py'den BİREBİR taşındı (davranış/URL
değişmedi); asıl senkron orkestrasyonu (_run_full_sync/_run_hb_sync/
_run_sync_in_thread) ve HB backfill iş mantığı sync_core.py'de kalıyor.

cost_routes.py / stock_routes.py / payout_routes.py ile aynı desen.
app.py'de kayıt: app.register_blueprint(finance_routes.bp)
"""

from datetime import datetime

from flask import Blueprint, jsonify, request

from database import (
    delete_fixed_expense,
    get_sync_progress,
    list_fixed_expenses,
    upsert_fixed_expense,
)
from sync_lock import sync_lock_status
from sync_core import (
    DATA_START_DATE,
    HB_ENV,
    HB_MERCHANT_ID,
    _check_credentials,
    _check_hb_credentials,
    _is_incremental_eligible,
    _monthly_profit_for_marketplace,
    _resolve_sync_range,
    _run_full_sync,
    _run_hb_sync,
    _run_sync_in_thread,
    backfill_hb_settlement_only_quantities,
    find_hb_settlement_only_packages,
)

bp = Blueprint("finance_routes", __name__)


@bp.route("/api/sync-finance", methods=["POST"])
def sync_finance():
    """Siparişleri + Finans API verisini Flask sürecinin arka plan
    thread'inde çeker (Celery worker gerektirmez, bkz. _run_sync_in_thread
    notu). Hemen döner; ilerleme için /api/sync-status'ü yoklayın (polling).
    Hepsiburada bilgileri .env'de tanımlıysa, aynı tetiklemeyle Hepsiburada
    senkronizasyonu da (ayrı kilit, ayrı thread'de, paralel) başlatılır."""
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400

    if sync_lock_status("trendyol"):
        return jsonify({"error": "Zaten devam eden bir senkronizasyon var."}), 409

    start_dt, end_dt = _resolve_sync_range(request.args)
    incremental_ok = _is_incremental_eligible(request.args)

    _run_sync_in_thread("trendyol", _run_full_sync, start_dt, end_dt, incremental_ok=incremental_ok)

    hb_started = False
    if _check_hb_credentials() is None and not sync_lock_status("hepsiburada"):
        _run_sync_in_thread("hepsiburada", _run_hb_sync, start_dt, end_dt, incremental_ok=incremental_ok)
        hb_started = True

    return jsonify({
        "started": True,
        "hepsiburada_started": hb_started,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    })

@bp.route("/api/sync-status")
def sync_status():
    marketplace = request.args.get("marketplace", "trendyol")
    return jsonify(get_sync_progress(marketplace=marketplace))

@bp.route("/api/sync-hepsiburada", methods=["POST"])
def sync_hepsiburada():
    """Hepsiburada siparişlerini Flask sürecinin arka plan thread'inde çeker
    (Celery worker gerektirmez, bkz. _run_sync_in_thread notu).
    Parametreler: days=N | start_date=YYYY-MM-DD | full_history=true
    (Trendyol ile aynı mantık)."""
    error = _check_hb_credentials()
    if error:
        return jsonify({"error": error}), 400

    if sync_lock_status("hepsiburada"):
        return jsonify({"error": "Zaten devam eden bir Hepsiburada senkronizasyonu var."}), 409

    start_dt, end_dt = _resolve_sync_range(request.args)
    incremental_ok = _is_incremental_eligible(request.args)

    _run_sync_in_thread("hepsiburada", _run_hb_sync, start_dt, end_dt, incremental_ok=incremental_ok)

    return jsonify({
        "started": True,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    })

@bp.route("/api/hb-config-status")
def hb_config_status():
    error = _check_hb_credentials()
    return jsonify({
        "configured": error is None,
        "message": error,
        "env": HB_ENV,
        "merchant_id": HB_MERCHANT_ID if HB_MERCHANT_ID else None,
    })

@bp.route("/api/backfill-hb-quantities", methods=["POST"])
def api_backfill_hb_quantities():
    """Faz 3 backfill'i küçük bir parti (varsayılan 50 sipariş) için tetikler.
    Kuyruk boşalana kadar tekrar tekrar çağırılması amaçlanmıştır (örn. panelden
    'Eksik miktarları tamamla' butonu, ya da cron ile günde birkaç kez)."""
    cred_error = _check_hb_credentials()
    if cred_error:
        return jsonify({"error": cred_error}), 400

    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, 500))
    try:
        result = backfill_hb_settlement_only_quantities(limit=limit)
    except Exception as e:
        return jsonify({"error": f"Backfill hatası: {e}"}), 500
    return jsonify(result)

@bp.route("/api/backfill-hb-quantities/status")
def api_backfill_hb_quantities_status():
    """Kuyrukta kaç paket kaldığını (henüz gerçek quantity'si çekilmemiş) döner."""
    try:
        remaining = len(find_hb_settlement_only_packages())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"remaining": remaining})

@bp.route("/api/monthly-profit")
def api_monthly_profit():
    args = request.args
    end_dt = datetime.now()
    if args.get("start_date"):
        try:
            start_dt = datetime.strptime(args["start_date"], "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "start_date formatı YYYY-MM-DD olmalı."}), 400
    else:
        start_dt = DATA_START_DATE

    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    if marketplace not in ("all", "trendyol", "hepsiburada"):
        marketplace = "all"

    try:
        months = _monthly_profit_for_marketplace(start_dt, end_dt, marketplace)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500

    first_active = next(
        (i for i, m in enumerate(months) if (m.get("revenue") or 0) != 0
         or (m.get("grossProfit") or 0) != 0 or (m.get("netProfit") or 0) != 0),
        0,
    )
    months = months[first_active:]

    return jsonify({"months": months})

@bp.route("/api/fixed-expenses", methods=["GET", "POST"])
def api_fixed_expenses():
    """GET: tüm aylık sabit gider kalemlerini döner (opsiyonel ?month=YYYY-MM
    ile tek aya filtrelenebilir). POST: yeni kalem ekler/günceller (body'de
    'id' verilirse günceller). Beklenen JSON: {month, label, amount, note?}
    (month formatı 'YYYY-MM')."""
    if request.method == "GET":
        month = (request.args.get("month") or "").strip() or None
        items = list_fixed_expenses(month=month)
        return jsonify({"items": items})

    data = request.get_json(silent=True) or {}
    month = (data.get("month") or "").strip()
    label = (data.get("label") or "").strip()

    if not month:
        return jsonify({"error": "'month' alanı zorunlu (YYYY-MM formatında)."}), 400
    if not label:
        return jsonify({"error": "'label' alanı zorunlu."}), 400
    try:
        amount = float(data["amount"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "'amount' zorunlu ve sayısal olmalı."}), 400

    row = {
        "id": data.get("id"),
        "month": month,
        "label": label,
        "amount": amount,
        "note": data.get("note") or None,
    }
    try:
        saved = upsert_fixed_expense(row)
    except Exception as e:
        return jsonify({"error": f"Kaydetme hatası: {e}"}), 500

    return jsonify({"ok": True, "item": saved})

@bp.route("/api/fixed-expenses/<int:expense_id>", methods=["DELETE"])
def api_delete_fixed_expense(expense_id):
    try:
        delete_fixed_expense(expense_id)
    except Exception as e:
        return jsonify({"error": f"Silme hatası: {e}"}), 500
    return jsonify({"ok": True, "id": expense_id})
