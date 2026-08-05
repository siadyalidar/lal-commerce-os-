"""
blueprints/payout_routes.py
----------------------------
Hakediş Takvimi: Trendyol + Hepsiburada'dan settlements.payment_date'e göre
günlük net hakediş (hesaba geçecek/geçmiş para) toplamlarını döner.

stock_routes.py / cost_routes.py ile aynı desen: bağımsız blueprint, sync
kilitlerine/thread state'ine dokunmuyor.
app.py'de kayıt: app.register_blueprint(payout_routes.bp)
"""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from finance_engine import payout_calendar

bp = Blueprint("payout_routes", __name__)

# ⚠️ NOT: Aşağıdaki /api/payout-external-* route'ları hassas bir çerez
# (session cookie) DEĞERİNİ kaydediyor. Bu panel şu an tek kullanıcılı/
# yerel bir araç olduğu için ekstra bir auth katmanı eklenmedi — eğer bu
# paneli internete/başka kullanıcılara açacaksanız bu route'ların önüne
# mutlaka bir kimlik doğrulama katmanı koyun.


@bp.route("/api/payout-calendar", methods=["GET"])
def get_payout_calendar():
    """Query params:
      marketplace: 'trendyol' | 'hepsiburada' | 'all' (varsayılan: hepsi)
      start, end : 'YYYY-MM-DD' (ikisi de verilirse bu aralıkla sınırlanır;
                   verilmezse DB'deki tüm bilinen payment_date aralığı döner)
    """
    marketplace = request.args.get("marketplace") or None
    if marketplace == "all":
        marketplace = None

    start_str = request.args.get("start")
    end_str = request.args.get("end")
    start_dt = end_dt = None
    if start_str and end_str:
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
            # Bitiş gününü DAHİL etmek için bir sonraki günün başlangıcından
            # 1 ms öncesine kadar al.
            end_dt = datetime.strptime(end_str, "%Y-%m-%d") + timedelta(days=1) - timedelta(milliseconds=1)
        except ValueError:
            return jsonify({"error": "start/end 'YYYY-MM-DD' formatında olmalı."}), 400

    try:
        result = payout_calendar(start_dt=start_dt, end_dt=end_dt, marketplace_filter=marketplace)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


@bp.route("/api/payout-external-status", methods=["GET"])
def get_payout_external_status():
    """Her pazaryeri için: kimlik bilgisi tanımlı mı, ne zaman girildi, son
    başarılı/başarısız çekim ne zamandı. Panelde 'süresi dolmuş' banner'ı
    için kullanılır."""
    try:
        from external_payout_db import credential_status, get_scrape_status
    except Exception as e:
        return jsonify({"error": f"external_payout_db yüklenemedi: {e}"}), 500

    status = get_scrape_status()
    result = {}
    for mp in ("trendyol", "hepsiburada"):
        info = dict(credential_status(mp))
        s = status.get(mp)
        info["lastError"] = s.get("error") if (s and not s.get("ok")) else None
        info["lastSuccessAt"] = s.get("last_success_at") if s else None
        result[mp] = info
    return jsonify(result)


@bp.route("/api/payout-external-credential", methods=["POST"])
def set_payout_external_credential():
    """Body: {"marketplace": "hepsiburada"|"trendyol", "value": "<header değeri>"}

    Hepsiburada için Cookie header'ının TAMAMI, Trendyol için Authorization
    header'ının TAMAMI ("Bearer eyJ..." dahil). Süresi dolduğunda DevTools'tan
    tekrar 'Copy as cURL' yapıp ilgili header değerini buraya (panel
    arayüzündeki forma) yapıştırarak yenilemek için. Değer DB'ye yazılır —
    Flask/Celery restart gerekmez.

    ⚠️ Trendyol'daki Authorization token'ı bir JWT ve muhtemelen kısa
    ömürlü — bkz. external_payout_scraper.py başındaki not.
    """
    data = request.get_json(silent=True) or {}
    marketplace = data.get("marketplace")
    value = data.get("value")
    if marketplace not in ("trendyol", "hepsiburada"):
        return jsonify({"error": "marketplace 'trendyol' veya 'hepsiburada' olmalı."}), 400
    if not value or not value.strip():
        return jsonify({"error": "value boş olamaz."}), 400

    try:
        from external_payout_db import set_credential
    except Exception as e:
        return jsonify({"error": f"external_payout_db yüklenemedi: {e}"}), 500

    try:
        set_credential(marketplace, value.strip())
    except Exception as e:
        return jsonify({"error": f"Kaydetme hatası: {e}"}), 500
    return jsonify({"ok": True})


@bp.route("/api/payout-external-sync", methods=["POST"])
def trigger_payout_external_sync():
    """Panelden 'Şimdi Çek' butonuyla manuel tetikleme.

    ⚠️ BİLEREK .delay() KULLANILMIYOR: .delay() sadece Celery broker'a
    mesajı bırakır, worker ayakta olmasa/task'ı tanımasa bile hata
    fırlatmadan 'başarılı' döner — kullanıcı butona basıp sonucu anında
    beklerken bu, sessizce hiçbir şey olmamasına yol açıyordu (task hiç
    çalışmadığı için external_scrape_status hiç güncellenmiyordu).
    Manuel tetikleme her zaman senkron çalıştırılır; periyodik/arka plan
    çekim istenirse (bkz. payout_scrape_tasks.py NOT 2) Celery Beat ayrı
    bir konudur.
    """
    try:
        from payout_scrape_tasks import sync_external_payout_estimates
    except Exception as e:
        return jsonify({"error": f"payout_scrape_tasks yüklenemedi: {e}"}), 500

    try:
        results = sync_external_payout_estimates()
    except Exception as e:
        return jsonify({"error": f"Senkronizasyon çalıştırılamadı: {e}"}), 500

    return jsonify({"ok": True, "mode": "sync", "results": results})
