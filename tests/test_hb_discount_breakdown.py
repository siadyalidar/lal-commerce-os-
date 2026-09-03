"""
tests/test_hb_discount_breakdown.py
-------------------------------------
B3 (Faz 0 audit): sync_core.py, HB paket senkronunda totalHBDiscount
(platformun karşıladığı indirim) ile totalMerchantDiscount (satıcının
kendi cebinden karşıladığı indirim) tek bir discount_amount'ta toplayıp
ekonomik ayrımı kaybediyordu. _hb_compute_order_totals() artık bu ikisini
AYRI alanlar olarak da döndürür; discount_amount toplam olarak kalmaya
devam eder (geriye dönük uyumluluk, gross-net hesabı bozulmaz).
"""

import sync_core
from database import get_connection, upsert_orders


def test_upsert_orders_persists_hb_discount_breakdown(db):
    """Uçtan uca doğrulama: upsert_orders() -> DB round-trip, hb_discount_amount/
    merchant_discount_amount değerleri kayboluyor mu diye."""
    upsert_orders([{
        "shipment_package_id": 999001, "marketplace": "hepsiburada",
        "order_number": "HB-DISC-TEST", "order_date": 1788000000000,
        "status": "Delivered", "customer": "Test", "cargo_provider": "hepsiJET",
        "gross_amount": 100.0, "discount_amount": 15.0, "net_amount": 85.0,
        "hb_discount_amount": 10.0, "merchant_discount_amount": 5.0,
    }])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT hb_discount_amount, merchant_discount_amount, discount_amount "
            "FROM orders WHERE marketplace='hepsiburada' AND shipment_package_id=999001"
        ).fetchone()
    assert row["hb_discount_amount"] == 10.0
    assert row["merchant_discount_amount"] == 5.0
    assert row["discount_amount"] == 15.0


def test_upsert_orders_leaves_hb_discount_null_for_trendyol(db):
    """Trendyol çağrılarında bu alanlar hiç verilmez -- NULL kalmalı, 0 gibi
    UYDURULMAMALI (Trendyol'da böyle bir ayrım/API alanı yok)."""
    upsert_orders([{
        "shipment_package_id": 999002, "marketplace": "trendyol",
        "order_number": "TY-NODISC-TEST", "order_date": 1788000000000,
        "status": "Delivered", "customer": "Test", "cargo_provider": "Aras",
        "gross_amount": 100.0, "discount_amount": 10.0, "net_amount": 90.0,
    }])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT hb_discount_amount, merchant_discount_amount "
            "FROM orders WHERE marketplace='trendyol' AND shipment_package_id=999002"
        ).fetchone()
    assert row["hb_discount_amount"] is None
    assert row["merchant_discount_amount"] is None


def test_hb_compute_order_totals_preserves_hb_vs_merchant_discount_split():
    source_lines = [
        {"price": 100.0, "quantity": 2, "totalHBDiscount": 10.0, "totalMerchantDiscount": 5.0},
        {"price": 50.0, "quantity": 1, "totalHBDiscount": 3.0, "totalMerchantDiscount": 0.0},
    ]
    totals = sync_core._hb_compute_order_totals(source_lines, package_total_price=None)

    assert totals["hb_discount_amount"] == 13.0
    assert totals["merchant_discount_amount"] == 5.0
    # Toplam discount_amount, ikisinin toplamıyla AYNI kalmalı (geriye dönük uyumluluk)
    assert totals["discount_amount"] == 18.0
    assert totals["gross_amount"] == 250.0  # 100*2 + 50*1
    assert totals["net_amount"] == 232.0    # 250 - 18


def test_hb_compute_order_totals_uses_package_total_price_when_present():
    """Paket seviyesinde totalPrice varsa (gerçek API cevabında genelde
    olduğu gibi) gross_amount ondan gelir, satır fiyatlarından yeniden
    HESAPLANMAZ -- ama indirim ayrımı yine de satır seviyesinden
    toplanmaya devam eder."""
    source_lines = [
        {"price": 100.0, "quantity": 1, "totalHBDiscount": 10.0, "totalMerchantDiscount": 5.0},
    ]
    totals = sync_core._hb_compute_order_totals(source_lines, package_total_price=100.0)

    assert totals["gross_amount"] == 100.0
    assert totals["hb_discount_amount"] == 10.0
    assert totals["merchant_discount_amount"] == 5.0
    assert totals["discount_amount"] == 15.0
    assert totals["net_amount"] == 85.0


def test_hb_compute_order_totals_handles_missing_discount_fields_as_zero():
    """totalHBDiscount/totalMerchantDiscount alanları API cevabında hiç
    yoksa (None), 0 olarak sayılmalı -- None ile toplama hata vermemeli."""
    source_lines = [
        {"price": 200.0, "quantity": 1, "totalHBDiscount": None, "totalMerchantDiscount": None},
    ]
    totals = sync_core._hb_compute_order_totals(source_lines, package_total_price=None)

    assert totals["hb_discount_amount"] == 0.0
    assert totals["merchant_discount_amount"] == 0.0
    assert totals["discount_amount"] == 0.0
    assert totals["net_amount"] == 200.0
