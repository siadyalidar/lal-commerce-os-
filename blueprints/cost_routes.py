"""
blueprints/cost_routes.py
--------------------------
Ürün maliyeti yönetimi route'ları (Excel içe aktarma + tekil SKU ekleme/silme).

app.py'den buraya taşındı çünkü bu route'lar senkronizasyon kilitlerine,
thread'lere veya Trendyol/Hepsiburada API state'ine dokunmuyor — bağımsız
test edilebilir ve okunabilirlik için ayrı bir modülde durmaları daha doğru.

app.py'de kayıt: app.register_blueprint(cost_routes.bp)
"""

import os

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from cost_import import import_product_costs
from database import get_connection, upsert_product_costs

bp = Blueprint("cost_routes", __name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@bp.route("/api/cost-settings", methods=["GET", "POST"])
def cost_settings():
    if request.method == "GET":
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM product_costs").fetchone()["c"]
        return jsonify({"product_cost_count": count})

    if "file" not in request.files:
        return jsonify({"error": "Dosya bulunamadı ('file' alanı boş)."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Dosya seçilmedi."}), 400

    filename = secure_filename(f.filename)
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    f.save(save_path)

    sheet = request.form.get("sheet") or "🧮 Ürün Maliyet"
    try:
        imported, skipped = import_product_costs(save_path, sheet_name=sheet)
    except Exception as e:
        return jsonify({"error": f"İçe aktarma hatası: {e}"}), 400

    return jsonify({"imported": imported, "skipped": skipped})


@bp.route("/api/product-cost", methods=["POST"])
def upsert_manual_product_cost():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip()
    if not sku:
        return jsonify({"error": "'sku' alanı zorunlu."}), 400

    try:
        cost_incl = float(data["cost_incl_vat"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "'cost_incl_vat' zorunlu ve sayısal olmalı."}), 400

    def _opt_float(key):
        v = data.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    row = {
        "sku": sku,
        "product_name": data.get("product_name") or sku,
        "cost_incl_vat": cost_incl,
        "cost_excl_vat": _opt_float("cost_excl_vat"),
        "sale_price_incl_vat": _opt_float("sale_price_incl_vat"),
        "sale_price_excl_vat": _opt_float("sale_price_excl_vat"),
    }
    upsert_product_costs([row])
    return jsonify({"ok": True, "sku": sku})


@bp.route("/api/product-costs")
def list_product_costs():
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT sku, product_name, cost_incl_vat, cost_excl_vat,
                      sale_price_incl_vat, sale_price_excl_vat, updated_at
               FROM product_costs ORDER BY updated_at DESC"""
        ).fetchall()
    return jsonify({"items": [dict(r) for r in rows]})


@bp.route("/api/product-cost/<path:sku>", methods=["DELETE"])
def delete_product_cost(sku):
    with get_connection() as conn:
        conn.execute("DELETE FROM product_costs WHERE sku = ?", (sku,))
    return jsonify({"ok": True, "sku": sku})
