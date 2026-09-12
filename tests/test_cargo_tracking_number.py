"""
tests/test_cargo_tracking_number.py
--------------------------------------
Kargo etiketi entegrasyonu, Faz 2: Trendyol'un createCommonLabel/
getCommonLabel servisleri shipmentPackageId değil cargoTrackingNumber ile
çalışıyor. Bu alan getShipmentPackages ham yanıtında geliyor ama şimdiye
kadar hiç saklanmıyordu -- bu testler hem DB round-trip'i (upsert_orders)
hem de sync_core.sync_orders_to_db()'nin bu alanı ham yanıttan doğru
çıkarıp geçirdiğini doğrular.
"""

from datetime import datetime

from database import get_connection, upsert_orders


def test_upsert_orders_persists_cargo_tracking_number(db):
    upsert_orders([{
        "shipment_package_id": 999101, "marketplace": "trendyol",
        "order_number": "TY-CARGO-TEST", "order_date": 1788000000000,
        "status": "Picking", "customer": "Test", "cargo_provider": "Aras Kargo",
        "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
        "cargo_tracking_number": "1234567890123",
    }])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT cargo_tracking_number FROM orders "
            "WHERE marketplace='trendyol' AND shipment_package_id=999101"
        ).fetchone()
    assert row["cargo_tracking_number"] == "1234567890123"


def test_upsert_orders_leaves_cargo_tracking_number_null_when_absent():
    """Alan verilmezse (ör. HB satırları, ya da henüz kargoya verilmemiş
    Trendyol siparişleri) NULL kalmalı -- uydurulmamalı."""
    upsert_orders([{
        "shipment_package_id": 999102, "marketplace": "hepsiburada",
        "order_number": "HB-NOCARGO-TEST", "order_date": 1788000000000,
        "status": "Open", "customer": "Test", "cargo_provider": "hepsiJET",
        "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
    }])
    with get_connection() as conn:
        row = conn.execute(
            "SELECT cargo_tracking_number FROM orders "
            "WHERE marketplace='hepsiburada' AND shipment_package_id=999102"
        ).fetchone()
    assert row["cargo_tracking_number"] is None


def test_sync_orders_to_db_extracts_cargo_tracking_number_from_raw_response(monkeypatch, db):
    """Faz 0 CONFIRMED bulgusu: raw_order (getShipmentPackages yanıtı)
    cargoTrackingNumber alanını içeriyor ama sync_orders_to_db() bugüne
    kadar bunu order_rows'a hiç aktarmıyordu."""
    import sync_core

    raw_order = {
        "shipmentPackageId": 999103,
        "orderNumber": 11486468900,
        "orderDate": 1786191406556,
        "status": "Picking",
        "customerFirstName": "Test",
        "customerLastName": "User",
        "cargoProviderName": "Aras Kargo",
        "cargoTrackingNumber": "9999888877776",
        "packageGrossAmount": 100.0,
        "packageTotalDiscount": 0.0,
        "packageTotalPrice": 100.0,
    }

    monkeypatch.setattr(sync_core, "fetch_all_orders", lambda *a, **kw: [raw_order])
    monkeypatch.setattr(sync_core, "_date_chunks", lambda start, end: [(start, end)])

    captured = {}
    monkeypatch.setattr(sync_core, "upsert_orders", lambda rows: captured.setdefault("order_rows", rows))
    monkeypatch.setattr(sync_core, "upsert_order_lines", lambda rows: None)

    sync_core.sync_orders_to_db(datetime(2026, 8, 8), datetime(2026, 8, 8, 23, 59))

    assert captured["order_rows"][0]["cargo_tracking_number"] == "9999888877776"


def test_sync_orders_to_db_leaves_cargo_tracking_number_none_when_missing(monkeypatch, db):
    """Henüz kargo firmasına verilmemiş (cargoTrackingNumber henüz atanmamış)
    siparişlerde alan None olarak geçirilmeli -- boş string ya da 0 UYDURULMAZ."""
    import sync_core

    raw_order = {
        "shipmentPackageId": 999104,
        "orderNumber": 11486468901,
        "orderDate": 1786191406556,
        "status": "Created",
        "customerFirstName": "Test",
        "customerLastName": "User",
        "cargoProviderName": "Aras Kargo",
        "packageGrossAmount": 50.0,
        "packageTotalDiscount": 0.0,
        "packageTotalPrice": 50.0,
    }

    monkeypatch.setattr(sync_core, "fetch_all_orders", lambda *a, **kw: [raw_order])
    monkeypatch.setattr(sync_core, "_date_chunks", lambda start, end: [(start, end)])

    captured = {}
    monkeypatch.setattr(sync_core, "upsert_orders", lambda rows: captured.setdefault("order_rows", rows))
    monkeypatch.setattr(sync_core, "upsert_order_lines", lambda rows: None)

    sync_core.sync_orders_to_db(datetime(2026, 8, 8), datetime(2026, 8, 8, 23, 59))

    assert captured["order_rows"][0]["cargo_tracking_number"] is None
