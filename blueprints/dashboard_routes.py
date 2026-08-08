"""
blueprints/dashboard_routes.py
--------------------------------
Panel sayfa route'ları (HTML render) ve gösterge paneli özet API'leri.
Kod app.py'den BİREBİR taşındı (davranış/URL değişmedi) — iş mantığı
(kredensiyel kontrolü, senkron aralığı çözümleme, kâr özeti hesaplama)
sync_core.py'de kalıyor, burada sadece route handler'lar var.

cost_routes.py / stock_routes.py / payout_routes.py ile aynı desen.
app.py'de kayıt: app.register_blueprint(dashboard_routes.bp)
"""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request

from database import get_connection
from finance_engine import compute_profit_summary
from finance_engine import best_sellers as compute_best_sellers
from sync_core import (
    DATA_START_DATE,
    ENV,
    SUPPLIER_ID,
    _check_credentials,
    _dashboard_summary_for_marketplace,
    _resolve_sync_range,
)

bp = Blueprint("dashboard_routes", __name__)


@bp.route("/")
def ai_genel_bakis_page():
    return render_template("pages/ai-genel-bakis.html", active_page="ai-genel-bakis")

@bp.route("/gosterge-paneli")
def gosterge_paneli_page():
    return render_template("pages/gosterge-paneli.html", active_page="gosterge-paneli")

@bp.route("/finans")
def finans_page():
    return render_template("pages/finans.html", active_page="finans")

@bp.route("/siparisler")
def siparisler_page():
    return render_template("pages/siparisler.html", active_page="siparisler")

@bp.route("/urunler")
def urunler_page():
    return render_template("pages/urunler.html", active_page="urunler")

@bp.route("/stok")
def stok_page():
    return render_template("pages/stok.html", active_page="stok")

@bp.route("/ayarlar")
def ayarlar_page():
    return render_template("pages/ayarlar.html", active_page="ayarlar")

# Faz 5 öncesi eski linkler için geriye dönük uyumluluk (yer imleri, tarayıcı geçmişi vb.)
@bp.route("/dashboard")
def dashboard():
    return redirect("/gosterge-paneli")

@bp.route("/orders")
def orders_page():
    return redirect("/siparisler")

@bp.route("/api/config-status")
def config_status():
    error = _check_credentials()
    return jsonify({
        "configured": error is None,
        "message": error,
        "env": ENV,
        "supplier_id": SUPPLIER_ID if SUPPLIER_ID else None,
        "data_start_date": DATA_START_DATE.strftime("%Y-%m-%d"),
    })

@bp.route("/api/dashboard-summary")
def dashboard_summary():
    start_dt, end_dt = _resolve_sync_range(request.args)
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    if marketplace not in ("all", "trendyol", "hepsiburada"):
        marketplace = "all"
    try:
        summary = _dashboard_summary_for_marketplace(start_dt, end_dt, marketplace)
    except Exception as e:
        return jsonify({"error": f"Kâr hesaplama hatası: {e}"}), 500
    if "lines" in summary:
        # KPI toplamları (ciro/kâr/marj) 'lines' listesinden BAĞIMSIZ olarak
        # zaten tam aralık üzerinden hesaplanmış durumda (yukarıda) — burada
        # sadece detay tablosu sayfalanıyor, toplamlar etkilenmiyor.
        page = max(1, request.args.get("lines_page", default=1, type=int) or 1)
        page_size = max(1, min(request.args.get("lines_page_size", default=1000, type=int) or 1000, 5000))
        total_lines = len(summary["lines"])
        offset = (page - 1) * page_size
        summary["lines"] = summary["lines"][offset:offset + page_size]
        summary["lines_total"] = total_lines
        summary["lines_page"] = page
        summary["lines_page_size"] = page_size
    return jsonify(summary)

@bp.route("/api/best-sellers")
def api_best_sellers():
    start_dt, end_dt = _resolve_sync_range(request.args)
    limit = request.args.get("limit", default=10, type=int)
    limit = max(1, min(limit, 50))
    # DÜZELTME (28.07.2026): Frontend zaten 'marketplace' parametresini
    # gönderiyordu (rangeQueryParam(), templates/app.html) ama bu route onu
    # hiç okumuyordu — sonuç: pazaryeri sekmesi değiştirilse bile en çok
    # satanlar listesi her zaman TÜM pazaryerlerinin karışık verisini
    # gösteriyordu. Artık okunuyor ve motora iletiliyor.
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    mp_filter = None if marketplace not in ("trendyol", "hepsiburada") else marketplace
    try:
        result = compute_best_sellers(start_dt=start_dt, end_dt=end_dt, limit=limit, marketplace_filter=mp_filter)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500
    return jsonify({"items": result})

@bp.route("/api/today-order-count")
def today_order_count():
    """Bugünün (yerel/sunucu tarihine göre) toplam sipariş sayısı — Trendyol +
    Hepsiburada birlikte, marketplace filtresi yok. Kasıtlı olarak sayfadaki
    tarih aralığı filtresinden (range-select) bağımsız çalışır: kullanıcı
    geçmiş bir aralık seçmiş olsa bile her zaman 'bugün'ü gösterir."""
    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)
    start_ms = int(start_of_day.timestamp() * 1000)
    end_ms = int(end_of_day.timestamp() * 1000)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE order_date >= ? AND order_date < ?",
            (start_ms, end_ms),
        ).fetchone()

    return jsonify({"count": row["c"] if row else 0})

@bp.route("/api/today-net-profit")
def today_net_profit():
    """AI Genel Bakış hero kartındaki 'Bugünkü Net Kazanç' için: bugünün
    (yerel/sunucu tarihine göre) TAM net kâr rakamı — finance_engine'deki
    netProfit ile birebir aynı formül (komisyon + hizmet bedeli + kargo +
    stopaj + iade + platform bedeli dahil), marketplace filtresi yok.
    today_order_count ile aynı gerekçeyle sayfadaki tarih aralığı
    filtresinden bağımsız, her zaman 'bugün'ü hesaplar.

    Bugün satılan satırlardan herhangi birinin ürün maliyeti (SKU) hiç
    tanımlı değilse netProfit BİLİNÇLİ OLARAK None döner (yarı-doğru bir
    rakam göstermek yerine) — frontend bu durumda '-' gösterir, çünkü o
    satırın kâr/zarar katkısı gerçekte bilinmiyor, netProfit toplamına
    dahil edilmemiş oluyor ve rakam yanıltıcı olurdu.

    06.08.2026 düzeltmesi: include_settlement_only=False geçiliyor. Aksi
    halde compute_profit_summary, bugün settlement kaydı düşen ama SİPARİŞ
    TARİHİ geçmiş bir güne ait satırlar için de sentetik satır ekliyordu —
    bu da 'Bugünkü Net Kazanç'ın, sadece bugün sipariş edilenleri kapsayan
    'Bugünkü Satış' (hero-today-sales / /api/daily-sales) rakamından daha
    büyük çıkmasına (matematiksel olarak imkânsız görünmesine) yol açıyordu.
    Artık ikisi de aynı tarih eksenini (order_date) kullanıyor."""
    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)

    try:
        summary = compute_profit_summary(start_dt=start_of_day, end_dt=end_of_day, marketplace_filter=None,
                                          include_settlement_only=False)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500

    missing_skus = summary["data_quality"]["skus_missing_cost"]
    net_profit = summary["totals"]["netProfit"] if not missing_skus else None

    return jsonify({
        "netProfit": net_profit,
        "hasMissingCost": bool(missing_skus),
        "missingCostCount": len(missing_skus),
    })
