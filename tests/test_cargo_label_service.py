"""
tests/test_cargo_label_service.py
--------------------------------------
cargo_label_service.get_labels_for_orders() -- idempotency (önbellek +
Trendyol'un "önce get, gerekirse create" akışı), statü kontrolü, ve
tekli/toplu (1/10/50 sipariş) aynı fonksiyonla çalışma davranışı.
Trendyol/HB API'leri (cargo_label_client fonksiyonları) her testte
mock'lanır -- gerçek ağ çağrısı yapılmaz.
"""

import base64
from unittest.mock import patch

from cargo_label_client import HepsiburadaLabelError, TrendyolLabelError
from cargo_label_service import get_labels_for_orders
from database import get_cached_cargo_label, upsert_orders


def _hb_raw_response(*zpl_strings):
    """CONFIRMED (canlı test, 13.09.2026): HB /labels şu şemayı döner:
    {"format": "base64zpl", "data": [<base64-encoded ZPL>, ...],
     "description": "Mutual barcode successful", "code": "100",
     "hasMerchantMutualBarcode": true}"""
    return {
        "format": "base64zpl",
        "data": [base64.b64encode(z.encode("utf-8")).decode("ascii") for z in zpl_strings],
        "description": "Mutual barcode successful",
        "code": "100",
        "hasMerchantMutualBarcode": True,
    }


def _make_trendyol_order(shipment_package_id, status, cargo_tracking_number="1112223334445"):
    upsert_orders([{
        "shipment_package_id": shipment_package_id, "marketplace": "trendyol",
        "order_number": f"TY-{shipment_package_id}", "order_date": 1788000000000,
        "status": status, "customer": "Test", "cargo_provider": "Aras Kargo",
        "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
        "cargo_tracking_number": cargo_tracking_number,
    }])


# ------------------------------------------------------------------
# Statü kontrolü (Sidar'ın 12.09.2026 onayı: sistem otomatik kontrol etsin)
# ------------------------------------------------------------------

def test_trendyol_order_not_ready_status_returns_error_without_calling_api(db):
    _make_trendyol_order(1001, status="Created")
    with patch("cargo_label_service.trendyol_get_common_label") as mock_get:
        results = get_labels_for_orders([("trendyol", 1001)])
    mock_get.assert_not_called()
    assert results[0]["status"] == "error"
    assert "status_not_ready" in results[0]["reason"]


def test_trendyol_order_missing_tracking_number_returns_error(db):
    _make_trendyol_order(1002, status="Picking", cargo_tracking_number=None)
    results = get_labels_for_orders([("trendyol", 1002)])
    assert results[0]["status"] == "error"
    assert "missing_cargo_tracking_number" in results[0]["reason"]


def test_trendyol_order_not_found_returns_error(db):
    results = get_labels_for_orders([("trendyol", 999999)])
    assert results[0]["status"] == "error"
    assert results[0]["reason"] == "order_not_found"


# ------------------------------------------------------------------
# İdempotency -- Sidar'ın açık talebi
# ------------------------------------------------------------------

@patch("cargo_label_service.trendyol_create_common_label")
@patch("cargo_label_service.trendyol_get_common_label")
def test_trendyol_prefers_existing_label_without_calling_create(mock_get, mock_create, db):
    """Etiket Trendyol tarafında zaten create edilmişse (ör. panelden),
    getCommonLabel tek başına yeterli -- createCommonLabel'a HİÇ gidilmemeli."""
    _make_trendyol_order(1003, status="Picking")
    mock_get.return_value = [{"label": "^XA...^XZ", "format": "ZPL"}]

    results = get_labels_for_orders([("trendyol", 1003)])

    mock_create.assert_not_called()
    assert results[0]["status"] == "ok"
    assert results[0]["fromCache"] is False


@patch("cargo_label_service.trendyol_create_common_label")
@patch("cargo_label_service.trendyol_get_common_label")
def test_trendyol_calls_create_only_when_get_is_empty(mock_get, mock_create, db):
    """getCommonLabel ilk denemede boşsa (henüz create edilmemiş),
    SADECE O ZAMAN createCommonLabel çağrılır, sonra tekrar get denenir."""
    _make_trendyol_order(1004, status="Invoiced")
    mock_get.side_effect = [
        TrendyolLabelError("boş"),
        [{"label": "^XA...^XZ", "format": "ZPL"}],
    ]

    results = get_labels_for_orders([("trendyol", 1004)])

    mock_create.assert_called_once()
    assert mock_get.call_count == 2
    assert results[0]["status"] == "ok"


@patch("cargo_label_service.trendyol_create_common_label")
@patch("cargo_label_service.trendyol_get_common_label")
def test_trendyol_second_request_uses_cache_not_api(mock_get, mock_create, db):
    """Bir etiket bir kez başarıyla alındıktan sonra, aynı sipariş için
    TEKRAR istenirse (force_refresh=False, varsayılan) API'ye HİÇ gidilmez --
    bu, createCommonLabel'ın tekrar tekrar çağrılıp yan etki yaratmasını
    engelleyen ana mekanizma."""
    _make_trendyol_order(1005, status="Picking")
    mock_get.return_value = [{"label": "^XA...^XZ", "format": "ZPL"}]

    first = get_labels_for_orders([("trendyol", 1005)])
    assert first[0]["fromCache"] is False
    assert mock_get.call_count == 1

    second = get_labels_for_orders([("trendyol", 1005)])
    assert second[0]["fromCache"] is True
    assert mock_get.call_count == 1  # tekrar artmadı -- önbellekten döndü
    mock_create.assert_not_called()

    assert get_cached_cargo_label("trendyol", 1005) is not None


@patch("cargo_label_service.trendyol_create_common_label")
@patch("cargo_label_service.trendyol_get_common_label")
def test_trendyol_force_refresh_bypasses_cache(mock_get, mock_create, db):
    _make_trendyol_order(1006, status="Picking")
    mock_get.return_value = [{"label": "^XA...v1^XZ", "format": "ZPL"}]
    get_labels_for_orders([("trendyol", 1006)])

    mock_get.return_value = [{"label": "^XA...v2^XZ", "format": "ZPL"}]
    result = get_labels_for_orders([("trendyol", 1006)], force_refresh=True)

    assert result[0]["fromCache"] is False
    assert mock_get.call_count == 2


# ------------------------------------------------------------------
# Hepsiburada
# ------------------------------------------------------------------

@patch("cargo_label_service.hb_fetch_package_labels")
def test_hb_label_success_is_cached(mock_hb, db):
    mock_hb.return_value = _hb_raw_response("XlhBXk1NVF5DSTI4XkZT")
    first = get_labels_for_orders([("hepsiburada", 2001)])
    assert first[0]["status"] == "ok"
    assert first[0]["fromCache"] is False

    second = get_labels_for_orders([("hepsiburada", 2001)])
    assert second[0]["fromCache"] is True
    assert mock_hb.call_count == 1


@patch("cargo_label_service.hb_fetch_package_labels")
def test_hb_api_error_is_reported_without_crashing(mock_hb, db):
    mock_hb.side_effect = HepsiburadaLabelError("boş")
    results = get_labels_for_orders([("hepsiburada", 2002)])
    assert results[0]["status"] == "error"
    assert "hb_api_error" in results[0]["reason"]


def test_hb_placeholder_shipment_package_id_returns_error_without_calling_api(db):
    """sync_core.py'de HB henüz packageNumber atamadığı siparişler için
    negatif bir placeholder_id (-abs(order_number)) kullanılıyor (bkz.
    sync_core.py satır ~333, status genelde 'AwaitingPackage'). Bu durumda
    gerçek bir paket/etiket YOKTUR -- API'ye hiç gidilmeden anlamlı bir
    hata dönmeli (Trendyol'daki status_not_ready kontrolüne paralel).
    Canlı testte (12.09.2026) bu senaryo gerçek bir 404 ile doğrulandı."""
    with patch("cargo_label_service.hb_fetch_package_labels") as mock_hb:
        results = get_labels_for_orders([("hepsiburada", -4366080818)])
    mock_hb.assert_not_called()
    assert results[0]["status"] == "error"
    assert "package_not_yet_created" in results[0]["reason"]


@patch("cargo_label_service.hb_fetch_package_labels")
def test_hb_response_is_decoded_from_base64_zpl_to_zpl(mock_hb, db):
    """CONFIRMED (canlı test, 13.09.2026): HB base64-kodlanmış ZPL döner.
    cargo_label_service bunu Trendyol ile AYNI iç formata normalize etmeli
    ([{"label": "^XA...", "format": "ZPL"}, ...]) -- böylece Faz 8'deki
    ZPL->görüntü render katmanı pazaryerinden bağımsız çalışabilir."""
    mock_hb.return_value = _hb_raw_response("^XA^FO10,10^FDTEST^FS^XZ")

    result = get_labels_for_orders([("hepsiburada", 2003)])[0]

    assert result["status"] == "ok"
    assert result["labelFormat"] == "ZPL"
    import json
    parsed = json.loads(result["labelData"])
    assert parsed == [{"label": "^XA^FO10,10^FDTEST^FS^XZ", "format": "ZPL"}]


@patch("cargo_label_service.hb_fetch_package_labels")
def test_hb_response_multiple_parcels_all_decoded(mock_hb, db):
    mock_hb.return_value = _hb_raw_response("^XA...1^XZ", "^XA...2^XZ")
    result = get_labels_for_orders([("hepsiburada", 2004)])[0]
    import json
    parsed = json.loads(result["labelData"])
    assert len(parsed) == 2
    assert parsed[0]["label"] == "^XA...1^XZ"
    assert parsed[1]["label"] == "^XA...2^XZ"


@patch("cargo_label_service.hb_fetch_package_labels")
def test_hb_response_with_unexpected_code_is_treated_as_error(mock_hb, db):
    """code='100' dışındaki bir yanıt (örn. barkod oluşturulamadıysa)
    başarı gibi ele alınıp önbelleğe yazılmamalı."""
    mock_hb.return_value = {
        "format": "base64zpl", "data": [], "description": "Failed",
        "code": "500", "hasMerchantMutualBarcode": False,
    }
    result = get_labels_for_orders([("hepsiburada", 2005)])[0]
    assert result["status"] == "error"
    assert get_cached_cargo_label("hepsiburada", 2005) is None


# ------------------------------------------------------------------
# Toplu (bulk) işlem -- 1/10/50 fark etmeksizin AYNI fonksiyon,
# bir siparişteki hata diğerlerini etkilemez.
# ------------------------------------------------------------------

@patch("cargo_label_service.hb_fetch_package_labels")
@patch("cargo_label_service.trendyol_create_common_label")
@patch("cargo_label_service.trendyol_get_common_label")
def test_bulk_request_mixed_marketplaces_and_partial_failure(mock_ty_get, mock_ty_create, mock_hb, db):
    """10 siparişlik bir toplu istekte bazıları başarılı, biri statü uygun
    değil, biri de HB tarafında API hatası versin -- sonuç listesi girdiyle
    aynı sırada ve aynı uzunlukta olmalı, tek bir hata TÜMÜNÜ düşürmemeli."""
    _make_trendyol_order(3001, status="Picking")
    _make_trendyol_order(3002, status="Created")  # statü uygun değil
    mock_ty_get.return_value = [{"label": "^XA...^XZ", "format": "ZPL"}]
    mock_hb.side_effect = [
        _hb_raw_response("^XA...^XZ"),
        HepsiburadaLabelError("boş"),
    ]

    order_keys = [
        ("trendyol", 3001),
        ("trendyol", 3002),
        ("hepsiburada", 4001),
        ("hepsiburada", 4002),
    ]
    results = get_labels_for_orders(order_keys)

    assert len(results) == 4
    assert results[0]["status"] == "ok" and results[0]["marketplace"] == "trendyol"
    assert results[1]["status"] == "error" and "status_not_ready" in results[1]["reason"]
    assert results[2]["status"] == "ok" and results[2]["marketplace"] == "hepsiburada"
    assert results[3]["status"] == "error" and "hb_api_error" in results[3]["reason"]


@patch("cargo_label_service.hb_fetch_package_labels")
def test_bulk_request_scales_to_fifty_orders(mock_hb, db):
    """Mimarinin 1/10/50 sipariş için AYNI yol olduğunu doğrulayan basit
    ölçek testi -- 50 farklı HB paketi tek çağrıda işlenebilmeli."""
    mock_hb.return_value = _hb_raw_response("^XA...^XZ")
    order_keys = [("hepsiburada", 5000 + i) for i in range(50)]

    results = get_labels_for_orders(order_keys)

    assert len(results) == 50
    assert all(r["status"] == "ok" for r in results)
    assert mock_hb.call_count == 50
