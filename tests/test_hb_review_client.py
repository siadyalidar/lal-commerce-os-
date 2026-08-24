"""
test_hb_review_client.py
---------------------------
hb_review_client.py için testler. test_http_client.py deseniyle AYNI:
requests.get mock'lanıyor, gerçek ağ çağrısı YAPILMIYOR.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from hb_review_client import fetch_all_reviews_for_sku, normalize_review


def _resp(status_code, json_data=None, text=""):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    m.text = text
    if status_code == 200:
        m.raise_for_status.return_value = None
    else:
        err = requests.exceptions.HTTPError(f"{status_code} error")
        err.response = m
        m.raise_for_status.side_effect = err
    return m


def _page(review_ids, next_link=None, total=None):
    """Basit bir sayfa response'u üretir. Her review'a farklı bir
    product.sku atanır ki family discovery testleri anlamlı olsun."""
    reviews = []
    for i, rid in enumerate(review_ids):
        reviews.append({
            "id": rid,
            "product": {"sku": f"SIB-{i % 3}", "url": f"https://example.com/{i % 3}"},
            "order": {"merchantId": "m-1", "merchantName": "TEST"},
            "review": {"content": f"content-{rid}" if i % 2 == 0 else None},
            "star": 5,
            "createdAt": "2026-01-01T10:00:00+00:00",
            "isPurchaseVerified": True,
        })
    return {
        "totalItemCount": total if total is not None else len(review_ids),
        "data": {"approvedUserContent": {"approvedUserContentList": reviews}},
        "links": {"next": next_link},
    }


# ---------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------

@patch("http_client.time.sleep", return_value=None)
@patch("hb_review_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_single_page_no_next_link(mock_get, mock_sleep_client, mock_sleep_http):
    mock_get.return_value = _resp(200, _page(["r1", "r2"], next_link=None))
    reviews, family_skus, page_count = fetch_all_reviews_for_sku("SKU-1", referer="https://x/")
    assert len(reviews) == 2
    assert mock_get.call_count == 1
    assert page_count == 1


@patch("http_client.time.sleep", return_value=None)
@patch("hb_review_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_multi_page_follows_links_next_until_none(mock_get, mock_sleep_client, mock_sleep_http):
    mock_get.side_effect = [
        _resp(200, _page(["r1", "r2"], next_link="/next?from=2")),
        _resp(200, _page(["r3", "r4"], next_link="/next?from=4")),
        _resp(200, _page(["r5"], next_link=None)),
    ]
    reviews, family_skus, page_count = fetch_all_reviews_for_sku("SKU-1", referer="https://x/", size=2)
    assert len(reviews) == 5
    assert mock_get.call_count == 3
    assert page_count == 3
    ids = [r["id"] for r in reviews]
    assert ids == ["r1", "r2", "r3", "r4", "r5"]


@patch("http_client.time.sleep", return_value=None)
@patch("hb_review_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_empty_page_stops_pagination(mock_get, mock_sleep_client, mock_sleep_http):
    mock_get.side_effect = [
        _resp(200, _page([], next_link="/next?from=0")),
    ]
    reviews, family_skus, page_count = fetch_all_reviews_for_sku("SKU-1", referer="https://x/")
    assert reviews == []
    assert mock_get.call_count == 1


# ---------------------------------------------------------------
# Sibling / family discovery
# ---------------------------------------------------------------

@patch("http_client.time.sleep", return_value=None)
@patch("hb_review_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_family_skus_discovered_from_response(mock_get, mock_sleep_client, mock_sleep_http):
    """Response'taki product.sku'lardan aile üyeleri çıkarılmalı."""
    mock_get.return_value = _resp(200, _page(["r1", "r2", "r3"], next_link=None))
    reviews, family_skus, page_count = fetch_all_reviews_for_sku("SKU-1", referer="https://x/")
    # _page() her review'a farklı SIB-0/SIB-1/SIB-2 atıyor
    assert family_skus == {"SIB-0", "SIB-1", "SIB-2"}


# ---------------------------------------------------------------
# Hata yönetimi
# ---------------------------------------------------------------

@patch("http_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_first_page_failure_raises(mock_get, mock_sleep_http):
    """İlk sayfa bile alınamazsa exception YUKARI FIRLATILMALI (sync bu
    sku için tamamen başarısız sayılmalı, sessizce boş dönmemeli)."""
    mock_get.return_value = _resp(403, text="Hepsiburada | Güvenlik")
    with pytest.raises(RuntimeError, match="HTTP 403"):
        fetch_all_reviews_for_sku("SKU-1", referer="https://x/")


@patch("http_client.time.sleep", return_value=None)
@patch("hb_review_client.time.sleep", return_value=None)
@patch("http_client.requests.get")
def test_later_page_failure_returns_partial_results(mock_get, mock_sleep_client, mock_sleep_http):
    """2. sayfa başarısız olursa, 1. sayfada toplanan veri KAYBEDİLMEMELİ
    (Bölüm H: kısmi pagination hatası -> o ana kadar toplananı kaydet)."""
    mock_get.side_effect = [
        _resp(200, _page(["r1", "r2"], next_link="/next?from=2")),
        _resp(500, text="Internal Server Error"),
    ]
    reviews, family_skus, page_count = fetch_all_reviews_for_sku("SKU-1", referer="https://x/", size=2)
    assert len(reviews) == 2  # ilk sayfa korunmuş
    assert page_count == 1  # sadece basarili sayfa sayildi
    ids = [r["id"] for r in reviews]
    assert ids == ["r1", "r2"]


# ---------------------------------------------------------------
# normalize_review
# ---------------------------------------------------------------

def test_normalize_review_maps_all_fields():
    raw = {
        "id": "rev-1",
        "product": {"sku": "SKU-A", "url": "https://example.com/a"},
        "order": {"merchantId": "m-1", "merchantName": "TEST MERCHANT"},
        "review": {"content": "Güzel ürün"},
        "star": 5,
        "createdAt": "2026-01-01T10:00:00+00:00",
        "isPurchaseVerified": True,
    }
    normalized = normalize_review(raw)
    assert normalized["external_review_id"] == "rev-1"
    assert normalized["marketplace"] == "hepsiburada"
    assert normalized["product_sku"] == "SKU-A"
    assert normalized["product_url"] == "https://example.com/a"
    assert normalized["star"] == 5
    assert normalized["content"] == "Güzel ürün"
    assert normalized["created_at"] == "2026-01-01T10:00:00+00:00"
    assert normalized["merchant_id"] == "m-1"
    assert normalized["merchant_name"] == "TEST MERCHANT"
    assert normalized["is_purchase_verified"] == 1
    assert normalized["raw_json"]  # dolu bir JSON string


def test_normalize_review_accepts_null_content():
    raw = {
        "id": "rev-2",
        "product": {"sku": "SKU-A", "url": None},
        "order": {"merchantId": "m-1", "merchantName": "TEST"},
        "review": {"content": None},
        "star": 4,
        "createdAt": "2026-01-01T10:00:00+00:00",
        "isPurchaseVerified": False,
    }
    normalized = normalize_review(raw)
    assert normalized["content"] is None
    assert normalized["is_purchase_verified"] == 0


def test_normalize_review_returns_none_when_id_missing():
    """Bölüm H: 'id' alanı yoksa review SKIP edilmeli -- normalize_review
    None döner, çağıran taraf bunu filtrelemeli."""
    raw = {
        "product": {"sku": "SKU-A"},
        "star": 5,
        "review": {"content": "içerik var ama id yok"},
    }
    assert normalize_review(raw) is None
