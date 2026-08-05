"""
blueprints/stock_routes.py
----------------------------
Ürün Ayarları sayfası: Trendyol + Hepsiburada'dan canlı stok senkronu ve
düşük stok uyarısı için SKU başına min eşik yönetimi.

cost_routes.py ile aynı desen: bağımsız, sync kilitlerine/thread state'ine
dokunmuyor — app.py'de kayıt: app.register_blueprint(stock_routes.bp)
"""

from flask import Blueprint, jsonify, request

from database import (
    list_product_stock,
    upsert_product_stock_quantities,
    upsert_product_stock_threshold,
)
from stock_client import fetch_hepsiburada_stock, fetch_trendyol_stock

bp = Blueprint("stock_routes", __name__)


@bp.route("/api/stock-sync/<marketplace>", methods=["POST"])
def sync_stock(marketplace):
    """Trendyol veya Hepsiburada'dan canlı stok çekip product_stock'a yazar.
    Senkron kısa sürdüğü için (tek istek zinciri, arka plan job'u değil)
    burada senkron/blocking çağrılıyor — büyük katalog (>10k SKU) için
    ileride tasks.py'ye taşınıp _run_locked ile korunması önerilir."""
    if marketplace not in ("trendyol", "hepsiburada"):
        return jsonify({"error": "marketplace 'trendyol' veya 'hepsiburada' olmalı."}), 400

    try:
        rows = fetch_trendyol_stock() if marketplace == "trendyol" else fetch_hepsiburada_stock()
    except Exception as e:
        return jsonify({"error": f"{marketplace} stok senkron hatası: {e}"}), 502

    rows = [r for r in rows if r.get("sku")]
    upsert_product_stock_quantities(rows)
    return jsonify({"ok": True, "marketplace": marketplace, "synced": len(rows)})


@bp.route("/api/product-stock")
def api_product_stock():
    """Ürün Ayarları tablosu için: marketplace filtresi opsiyonel
    (?marketplace=trendyol|hepsiburada), verilmezse ikisi birden döner."""
    marketplace = request.args.get("marketplace")
    if marketplace and marketplace not in ("trendyol", "hepsiburada"):
        return jsonify({"error": "marketplace 'trendyol' veya 'hepsiburada' olmalı."}), 400

    items = list_product_stock(marketplace)
    for it in items:
        qty = it.get("quantity")
        threshold = it.get("min_stock_threshold")
        it["lowStock"] = qty is not None and threshold is not None and qty <= threshold
    return jsonify({"items": items})


@bp.route("/api/product-stock-threshold", methods=["POST"])
def set_stock_threshold():
    """Beklenen JSON: {marketplace: 'trendyol'|'hepsiburada', sku, min_stock_threshold}"""
    data = request.get_json(silent=True) or {}
    marketplace = (data.get("marketplace") or "").strip()
    sku = (data.get("sku") or "").strip()
    if marketplace not in ("trendyol", "hepsiburada") or not sku:
        return jsonify({"error": "'marketplace' ('trendyol'/'hepsiburada') ve 'sku' zorunlu."}), 400

    try:
        threshold = int(data.get("min_stock_threshold"))
    except (TypeError, ValueError):
        return jsonify({"error": "'min_stock_threshold' sayısal olmalı."}), 400

    upsert_product_stock_threshold(marketplace, sku, threshold)
    return jsonify({"ok": True})
