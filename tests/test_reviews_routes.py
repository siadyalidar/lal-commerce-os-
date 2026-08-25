"""
tests/test_reviews_routes.py
--------------------------------
Yorumlar sayfası ve /api/reviews/overview uç noktası için testler.
test_reports_routes.py ile aynı desen (auth kontrolü + payload şekli).
"""

from tests.conftest import auth_headers


def test_reviews_page_requires_auth(client):
    resp = client.get("/yorumlar")
    assert resp.status_code == 401


def test_reviews_page_available_with_auth(client):
    resp = client.get("/yorumlar", headers=auth_headers())
    assert resp.status_code == 200


def test_reviews_overview_requires_auth(client):
    resp = client.get("/api/reviews/overview")
    assert resp.status_code == 401


def test_reviews_overview_shape_when_empty(client, db):
    resp = client.get("/api/reviews/overview", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert "stats" in data
    assert "todayReviews" in data
    assert "reviews" in data
    assert data["stats"]["totalCount"] == 0
    assert data["stats"]["avgStar"] is None
    assert data["todayReviews"] == []
    assert data["reviews"] == []


def test_reviews_overview_returns_seeded_reviews(client, db):
    db.upsert_review_contents([{
        "external_review_id": "rev-1", "marketplace": "hepsiburada",
        "product_sku": "SKU-A", "product_url": "https://example.com/a",
        "star": 5, "content": "Çok iyi ürün", "created_at": "2026-01-01T10:00:00+00:00",
        "merchant_id": "m-1", "merchant_name": "TEST", "is_purchase_verified": 1,
        "raw_json": "{}",
    }])
    resp = client.get("/api/reviews/overview", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["stats"]["totalCount"] == 1
    assert data["stats"]["avgStar"] == 5.0
    assert len(data["reviews"]) == 1
    assert data["reviews"][0]["external_review_id"] == "rev-1"
    # bu review test sırasında upsert edildiği için "bugün" listesine de girmeli
    assert len(data["todayReviews"]) == 1
