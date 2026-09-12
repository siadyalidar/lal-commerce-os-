"""
tests/test_cargo_label_db.py
--------------------------------
Kargo etiketi entegrasyonu, Faz 6: DB yardımcı fonksiyonları --
- get_order_for_label: cargo_label_service'in statü + cargoTrackingNumber
  lookup'u için (tekli VE toplu -- her ikisi de aynı fonksiyonu kullanır).
- get_cached_cargo_label / save_cargo_label: idempotency önbelleği
  (createCommonLabel'ın gereksiz yere tekrar tekrar çağrılmasını önlemek
  için, Sidar'ın açık talebi).
"""

from database import (
    get_cached_cargo_label,
    get_order_for_label,
    save_cargo_label,
    upsert_orders,
)


def test_get_order_for_label_returns_status_and_tracking_number(db):
    upsert_orders([{
        "shipment_package_id": 999201, "marketplace": "trendyol",
        "order_number": "TY-LBL-TEST", "order_date": 1788000000000,
        "status": "Picking", "customer": "Test", "cargo_provider": "Aras Kargo",
        "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
        "cargo_tracking_number": "1112223334445",
    }])
    row = get_order_for_label("trendyol", 999201)
    assert row["status"] == "Picking"
    assert row["cargo_tracking_number"] == "1112223334445"


def test_get_order_for_label_returns_none_when_not_found(db):
    assert get_order_for_label("trendyol", 424242) is None


def test_cargo_label_cache_round_trip(db):
    assert get_cached_cargo_label("trendyol", 999201) is None

    save_cargo_label(
        marketplace="trendyol", shipment_package_id=999201,
        cargo_tracking_number="1112223334445",
        label_format="ZPL", label_data='[{"label": "^XA...^XZ", "format": "ZPL"}]',
    )

    cached = get_cached_cargo_label("trendyol", 999201)
    assert cached["label_format"] == "ZPL"
    assert cached["label_data"] == '[{"label": "^XA...^XZ", "format": "ZPL"}]'
    assert cached["cargo_tracking_number"] == "1112223334445"


def test_cargo_label_cache_upsert_overwrites_previous_value(db):
    """force_refresh ile yeniden etiket alındığında önbellek güncellenmeli
    (eski etiketle karışmamalı)."""
    save_cargo_label(
        marketplace="hepsiburada", shipment_package_id=555001,
        cargo_tracking_number=None, label_format="RAW_JSON", label_data='{"v": 1}',
    )
    save_cargo_label(
        marketplace="hepsiburada", shipment_package_id=555001,
        cargo_tracking_number=None, label_format="RAW_JSON", label_data='{"v": 2}',
    )
    cached = get_cached_cargo_label("hepsiburada", 555001)
    assert cached["label_data"] == '{"v": 2}'
