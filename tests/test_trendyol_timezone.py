"""
tests/test_trendyol_timezone.py
--------------------------------
08.08.2026'da bulunan timezone bug'ının regresyon testleri.

Kök neden: Trendyol API'sinin orderDate/transactionDate/paymentDate/claimDate
alanları GERÇEK UTC epoch değil -- Europe/Istanbul yerel saatini doğrudan UTC
epoch gibi kodluyor. sync_core.normalize_trendyol_epoch_ms() bunu gerçek UTC'ye
çevirir (HB tarafı zaten _hb_iso_to_epoch_ms ile doğru çeviriyor, dokunulmadı).

Bu testteki epoch değerleri gerçek prod verisinden alınmıştır (08.08.2026,
Trendyol Satıcı Paneli'nde doğrulanmış sipariş saatleriyle):
  - 1786182470249 -> Trendyol panelinde "08.08.2026 09:47" olarak gösterilen sipariş
  - 1786191406556 -> Trendyol panelinde "08.08.2026 12:19" olarak gösterilen sipariş
"""
from datetime import datetime, timezone, timedelta

from sync_core import normalize_trendyol_epoch_ms

ISTANBUL = timezone(timedelta(hours=3))


def test_none_is_safe():
    assert normalize_trendyol_epoch_ms(None) is None


def test_normalizes_to_real_utc_matching_seller_panel_time():
    """Gerçek prod verisi: Trendyol panelinde 09:47 olarak gösterilen sipariş."""
    raw_ms = 1786182470249  # panelde: 08.08.2026 09:47
    normalized = normalize_trendyol_epoch_ms(raw_ms)

    real_utc = datetime.fromtimestamp(normalized / 1000, tz=timezone.utc)
    istanbul_local = real_utc.astimezone(ISTANBUL)

    assert istanbul_local.hour == 9
    assert istanbul_local.minute == 47


def test_normalizes_second_real_order():
    """Gerçek prod verisi: Trendyol panelinde 12:19 olarak gösterilen sipariş
    (bu, düzeltmeden önce sistemden sessizce kaybolan siparişti -- normalize
    edilmiş hali artık 'gelecekte' görünmüyor)."""
    raw_ms = 1786191406556  # panelde: 08.08.2026 12:19 (Meryem Mutlu siparişi)
    normalized = normalize_trendyol_epoch_ms(raw_ms)

    real_utc = datetime.fromtimestamp(normalized / 1000, tz=timezone.utc)
    istanbul_local = real_utc.astimezone(ISTANBUL)

    assert istanbul_local.hour == 12
    assert istanbul_local.minute in (16, 17)  # saniye/ms yuvarlama payı


def test_normalized_epoch_is_exactly_3_hours_less():
    raw_ms = 1786182470249
    normalized = normalize_trendyol_epoch_ms(raw_ms)
    assert raw_ms - normalized == 3 * 60 * 60 * 1000


def test_order_ingestion_applies_normalization(monkeypatch):
    """sync_orders_to_db'nin order_date alanına normalize_trendyol_epoch_ms
    uygulandığını, ham orderDate'in DB'ye asla direkt yazılmadığını doğrular."""
    import sync_core

    raw_order = {
        "shipmentPackageId": 999999,
        "orderNumber": 11486468857,
        "orderDate": 1786191406556,
        "status": "Shipped",
        "customerFirstName": "Test",
        "customerLastName": "User",
        "cargoProviderName": "Test Kargo",
        "packageGrossAmount": 100.0,
        "packageTotalDiscount": 0.0,
        "packageTotalPrice": 100.0,
    }

    monkeypatch.setattr(sync_core, "fetch_all_orders", lambda *a, **kw: [raw_order])
    monkeypatch.setattr(
        sync_core, "_date_chunks",
        lambda start, end: [(start, end)],
    )

    captured = {}

    def fake_upsert_orders(order_rows):
        captured["order_rows"] = order_rows

    monkeypatch.setattr(sync_core, "upsert_orders", fake_upsert_orders)
    monkeypatch.setattr(sync_core, "upsert_order_lines", lambda line_rows: None)

    sync_core.sync_orders_to_db(datetime(2026, 8, 8), datetime(2026, 8, 8, 23, 59))

    stored_order_date = captured["order_rows"][0]["order_date"]
    assert stored_order_date == sync_core.normalize_trendyol_epoch_ms(1786191406556)
    assert stored_order_date != 1786191406556  # ham değer asla direkt yazılmamalı
