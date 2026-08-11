"""
blueprints/supplier_routes.py
------------------------------
Toptancı borcu yönetimi: tedarikçi ekleme/silme, SKU->tedarikçi ataması
(bkz. cost_routes.py'deki /api/product-cost/<sku>/tedarikci), ödeme girişi
ve bakiye görüntüleme.

Satış kaynaklı borç hareketleri OTOMATİK eklenir (database.py ->
upsert_order_lines -> _sync_supplier_debt); burada elle işlem yapılmaz.

app.py'de kayıt: app.register_blueprint(supplier_routes_bp)
"""

from flask import Blueprint, jsonify, request

from database import (
    add_supplier_payment,
    backfill_supplier_debt,
    create_supplier,
    delete_supplier,
    get_total_supplier_debt,
    list_supplier_ledger,
    list_suppliers,
)

bp = Blueprint("supplier_routes", __name__)


@bp.route("/api/tedarikciler", methods=["GET"])
def get_suppliers():
    return jsonify({"items": list_suppliers(), "toplam_borc": get_total_supplier_debt()})


@bp.route("/api/tedarikciler", methods=["POST"])
def add_supplier():
    data = request.get_json(silent=True) or {}
    ad = (data.get("ad") or "").strip()
    if not ad:
        return jsonify({"error": "'ad' alanı zorunlu."}), 400
    new_id = create_supplier(ad)
    return jsonify({"ok": True, "id": new_id, "ad": ad})


@bp.route("/api/tedarikciler/<int:tedarikci_id>", methods=["DELETE"])
def remove_supplier(tedarikci_id):
    delete_supplier(tedarikci_id)
    return jsonify({"ok": True})


@bp.route("/api/tedarikciler/<int:tedarikci_id>/odeme", methods=["POST"])
def pay_supplier(tedarikci_id):
    data = request.get_json(silent=True) or {}
    try:
        tutar = float(data["tutar"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "'tutar' zorunlu ve sayısal olmalı."}), 400
    aciklama = data.get("aciklama", "")
    add_supplier_payment(tedarikci_id, tutar, aciklama)
    return jsonify({"ok": True})


@bp.route("/api/tedarikciler/<int:tedarikci_id>/hareketler", methods=["GET"])
def supplier_ledger(tedarikci_id):
    return jsonify({"items": list_supplier_ledger(tedarikci_id)})


@bp.route("/api/tedarikciler/backfill", methods=["POST"])
def backfill():
    """Bir SKU'ya yeni tedarikçi atadıktan sonra, o SKU'nun atamadan ÖNCE
    satılmış geçmiş order_lines satırları için de borç yazılsın istersen
    bunu çağır. Tüm order_lines'ı tarar, idempotenttir."""
    taranan = backfill_supplier_debt()
    return jsonify({"ok": True, "taranan_satir": taranan, "items": list_suppliers()})
