"""
tests/test_cargo_label_client.py
------------------------------------
Kargo etiketi (barkod) entegrasyonu -- cargo_label_client.py'nin saf HTTP
wrapper mantığını kapsar. Bu modül BİLEREK DB'ye dokunmaz (caching/idempotency
ve toplu işlem orkestrasyonu ayrı bir katmanda -- cargo_label_service.py).

CONFIRMED (12.09.2026 Faz 0, resmi dokümantasyondan):
  Trendyol -- SADECE "Trendyol Öder" (TEX/Aras Kargo ortak etiket) modelinde:
    POST https://apigw.trendyol.com/integration/sellers/{sellerId}/common-label/{cargoTrackingNumber}
         Body: {"format": "ZPL", "boxQuantity": N, "volumetricHeight": X}
         200 -> BAŞARILI ama gövde YOK. 400/401 -> hata.
    GET  aynı URL -> {"data": [{"label": "^XA...^XZ", "format": "ZPL"}]}
    https://developers.trendyol.com/v2.0/docs/common-label-barcode-request-createcommonlabel
    https://developers.trendyol.com/v2.0/docs/common-label-getting-created-common-label-getcommonlabel

  Hepsiburada -- HepsiJet ortak barkod:
    GET https://oms-external(-sit).hepsiburada.com/packages/merchantid/{merchantId}/packagenumber/{packageNumber}/labels
    Response şeması dokümanda örneklenmemiş -- UNVERIFIED, canlı ilk çağrıda
    doğrulanacak (bkz. hb_fetch_package_labels docstring'i).
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from cargo_label_client import (
    HepsiburadaLabelError,
    TrendyolLabelError,
    hb_fetch_package_labels,
    trendyol_create_common_label,
    trendyol_get_common_label,
)


@patch("cargo_label_client.trendyol_post")
def test_trendyol_create_common_label_sends_correct_body(mock_post):
    mock_post.return_value = None  # 200, no body -- beklenen başarı davranışı
    trendyol_create_common_label("1234567890123", box_quantity=2, volumetric_height=3.5)

    args, kwargs = mock_post.call_args
    assert "/common-label/1234567890123" in args[0]
    body = kwargs.get("json_body") or args[1]
    assert body["format"] == "ZPL"
    assert body["boxQuantity"] == 2
    assert body["volumetricHeight"] == 3.5


@patch("cargo_label_client.trendyol_get")
def test_trendyol_get_common_label_returns_label_list(mock_get):
    mock_get.return_value = {"data": [{"label": "^XA...^XZ", "format": "ZPL"}]}
    labels = trendyol_get_common_label("1234567890123")
    assert labels == [{"label": "^XA...^XZ", "format": "ZPL"}]


@patch("cargo_label_client.trendyol_get")
def test_trendyol_get_common_label_multiple_parcels(mock_get):
    """Trendyol dokümantasyonu: çoklu koli durumunda birden fazla etiket döner."""
    mock_get.return_value = {"data": [
        {"label": "^XA...1^XZ", "format": "ZPL"},
        {"label": "^XA...2^XZ", "format": "ZPL"},
    ]}
    labels = trendyol_get_common_label("1234567890123")
    assert len(labels) == 2


@patch("cargo_label_client.trendyol_get")
def test_trendyol_get_common_label_raises_on_empty_data(mock_get):
    """Etiket henüz oluşturulmamışsa (create çağrılmadan get çağrılırsa)
    'data' boş dönebilir -- bu sessizce [] döndürülmemeli, çağıran katmanın
    (service) ayırt edebilmesi için açık bir hata fırlatılmalı."""
    mock_get.return_value = {"data": []}
    with pytest.raises(TrendyolLabelError):
        trendyol_get_common_label("1234567890123")


@patch("cargo_label_client.trendyol_get")
def test_trendyol_get_common_label_translates_http_400_to_domain_error(mock_get):
    """CONFIRMED (canlı test, 14.09.2026): etiket henüz create edilmemişse
    Trendyol boş 'data' DEĞİL, doğrudan HTTP 400 döner. Bu, cargo_label_service
    'ın "get boşsa create çağır" akışını tetiklemesi için TrendyolLabelError'a
    çevrilmeli (HB'deki 404->HepsiburadaLabelError çevirisiyle aynı desen)."""
    fake_response = MagicMock(status_code=400)
    mock_get.side_effect = requests.exceptions.HTTPError("400 Client Error", response=fake_response)
    with pytest.raises(TrendyolLabelError):
        trendyol_get_common_label("1234567890123")


@patch("cargo_label_client.trendyol_get")
def test_trendyol_get_common_label_reraises_other_http_errors(mock_get):
    """400 dışındaki hatalar (ör. 401 kimlik hatası) 'etiket henüz yok'
    anlamına gelmez -- bunlar gizlenmeden olduğu gibi yükselmeli, aksi
    halde gerçek bir kimlik/izin sorunu sessizce create döngüsüne
    sokulmuş olur."""
    fake_response = MagicMock(status_code=401)
    mock_get.side_effect = requests.exceptions.HTTPError("401 Unauthorized", response=fake_response)
    with pytest.raises(requests.exceptions.HTTPError):
        trendyol_get_common_label("1234567890123")


# ------------------------------------------------------------------
# Hepsiburada -- /packages/merchantid/{id}/packagenumber/{packageNumber}/labels
# ŞEMA UNVERIFIED (dokümanda örnek response yok) -- bu yüzden
# hb_fetch_package_labels() içeride bir şema VARSAYMAZ, ham response'u
# olduğu gibi döner. Testler de sadece "doğru path'e gidiyor mu" ve
# "boş/None cevapta hata fırlatıyor mu" doğruluyor -- iç yapıyı değil.
# ------------------------------------------------------------------

@patch("cargo_label_client.hepsiburada_get")
def test_hb_fetch_package_labels_calls_correct_path(mock_get, monkeypatch):
    import sync_core
    monkeypatch.setattr(sync_core, "HB_MERCHANT_ID", "TESTMERCHANT")
    mock_get.return_value = {"anything": "raw-response-sema-dogrulanmadi"}

    hb_fetch_package_labels("PKG123")

    called_path = mock_get.call_args[0][0]
    assert called_path == "/packages/merchantid/TESTMERCHANT/packagenumber/PKG123/labels"


@patch("cargo_label_client.hepsiburada_get")
def test_hb_fetch_package_labels_returns_raw_response(mock_get, monkeypatch):
    """Şema henüz doğrulanmadığı için ham response'u olduğu gibi döner --
    parse etmeye/varsaymaya çalışmaz (UNVERIFIED alan uydurulmaz)."""
    import sync_core
    monkeypatch.setattr(sync_core, "HB_MERCHANT_ID", "TESTMERCHANT")
    raw = {"whatever": "the-real-shape-turns-out-to-be"}
    mock_get.return_value = raw

    result = hb_fetch_package_labels("PKG123")
    assert result == raw


@patch("cargo_label_client.hepsiburada_get")
def test_hb_fetch_package_labels_raises_on_empty_response(mock_get, monkeypatch):
    import sync_core
    monkeypatch.setattr(sync_core, "HB_MERCHANT_ID", "TESTMERCHANT")
    mock_get.return_value = None

    with pytest.raises(HepsiburadaLabelError):
        hb_fetch_package_labels("PKG123")


@patch("cargo_label_client.hepsiburada_get")
def test_hb_fetch_package_labels_translates_http_404_to_domain_error(mock_get, monkeypatch):
    """Canlı testte (12.09.2026) görüldü: paket henüz oluşturulmamışsa HB
    404 döner. requests.exceptions.HTTPError ham haliyle sızmamalı --
    çağıran katmanın (service) diğer hatalarla aynı şekilde ele alabilmesi
    için HepsiburadaLabelError'a çevrilmeli."""
    import sync_core
    monkeypatch.setattr(sync_core, "HB_MERCHANT_ID", "TESTMERCHANT")
    fake_response = MagicMock(status_code=404)
    mock_get.side_effect = requests.exceptions.HTTPError("404 Client Error", response=fake_response)

    with pytest.raises(HepsiburadaLabelError):
        hb_fetch_package_labels("PKG123")
