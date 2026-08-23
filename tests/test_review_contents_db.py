"""
test_review_contents_db.py
-----------------------------
review_contents / hb_review_family_map için DB katmanı testleri.
conftest.py'deki `db` fixture'ı ile izole geçici SQLite üzerinde çalışır,
gerçek trendyol_data.db'ye asla dokunmaz.
"""


def test_upsert_review_contents_inserts_new_row(db):
    db.upsert_review_contents([{
        "external_review_id": "rev-1",
        "marketplace": "hepsiburada",
        "product_sku": "SKU-A",
        "product_url": "https://example.com/a",
        "star": 5,
        "content": "Çok güzel ürün",
        "created_at": "2026-01-01T10:00:00+00:00",
        "merchant_id": "m-1",
        "merchant_name": "TEST MERCHANT",
        "is_purchase_verified": 1,
        "raw_json": "{}",
    }])
    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 1
    assert rows[0]["external_review_id"] == "rev-1"
    assert rows[0]["star"] == 5


def test_upsert_review_contents_is_idempotent_on_same_id(db):
    """Aynı review.id iki kez upsert edilirse İKİNCİ SATIR OLUŞMAMALI —
    Sidar'ın açıkça istediği kritik test."""
    base = {
        "external_review_id": "rev-dup",
        "marketplace": "hepsiburada",
        "product_sku": "SKU-A",
        "product_url": "https://example.com/a",
        "star": 4,
        "content": "İlk hali",
        "created_at": "2026-01-01T10:00:00+00:00",
        "merchant_id": "m-1",
        "merchant_name": "TEST MERCHANT",
        "is_purchase_verified": 1,
        "raw_json": "{}",
    }
    db.upsert_review_contents([base])

    updated = dict(base)
    updated["content"] = "Güncellenmiş hali"
    updated["star"] = 5
    db.upsert_review_contents([updated])

    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 1  # ikinci satır OLUŞMADI
    assert rows[0]["content"] == "Güncellenmiş hali"
    assert rows[0]["star"] == 5


def test_upsert_review_contents_same_review_from_different_sibling_sku(db):
    """Aynı review.id, farklı bir sorguda farklı product_sku ile tekrar
    gelirse (sibling paylaşımı senaryosu) yine TEK satır kalmalı."""
    db.upsert_review_contents([{
        "external_review_id": "rev-sibling",
        "marketplace": "hepsiburada",
        "product_sku": "SKU-A",
        "product_url": "https://example.com/a",
        "star": 5,
        "content": None,
        "created_at": "2026-01-01T10:00:00+00:00",
        "merchant_id": "m-1",
        "merchant_name": "TEST MERCHANT",
        "is_purchase_verified": 1,
        "raw_json": "{}",
    }])
    db.upsert_review_contents([{
        "external_review_id": "rev-sibling",
        "marketplace": "hepsiburada",
        "product_sku": "SKU-B",  # farklı sibling sku
        "product_url": "https://example.com/b",
        "star": 5,
        "content": None,
        "created_at": "2026-01-01T10:00:00+00:00",
        "merchant_id": "m-1",
        "merchant_name": "TEST MERCHANT",
        "is_purchase_verified": 1,
        "raw_json": "{}",
    }])
    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 1


def test_upsert_review_contents_accepts_null_content(db):
    """Faz 0: 538 review'ın 335'i content=NULL. Kolon NULL kabul etmeli,
    hata vermemeli, boş string'e çevrilmemeli."""
    db.upsert_review_contents([{
        "external_review_id": "rev-null-content",
        "marketplace": "hepsiburada",
        "product_sku": "SKU-A",
        "product_url": "https://example.com/a",
        "star": 5,
        "content": None,
        "created_at": "2026-01-01T10:00:00+00:00",
        "merchant_id": "m-1",
        "merchant_name": "TEST MERCHANT",
        "is_purchase_verified": 0,
        "raw_json": "{}",
    }])
    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 1
    assert rows[0]["content"] is None


def test_review_contents_marketplace_separation(db):
    """marketplace='trendyol' ve 'hepsiburada' kayıtları birbirinden
    tamamen ayrı kalmalı — aynı external_review_id farklı marketplace'te
    çakışmamalı (PK (marketplace, external_review_id))."""
    db.upsert_review_contents([{
        "external_review_id": "rev-x",
        "marketplace": "hepsiburada",
        "product_sku": "SKU-A", "product_url": None, "star": 5,
        "content": None, "created_at": None, "merchant_id": None,
        "merchant_name": None, "is_purchase_verified": None, "raw_json": "{}",
    }])
    db.upsert_review_contents([{
        "external_review_id": "rev-x",
        "marketplace": "trendyol",
        "product_sku": "SKU-Y", "product_url": None, "star": 3,
        "content": None, "created_at": None, "merchant_id": None,
        "merchant_name": None, "is_purchase_verified": None, "raw_json": "{}",
    }])
    hb_rows = db.list_reviews("hepsiburada")
    ty_rows = db.list_reviews("trendyol")
    assert len(hb_rows) == 1 and hb_rows[0]["product_sku"] == "SKU-A"
    assert len(ty_rows) == 1 and ty_rows[0]["product_sku"] == "SKU-Y"


def test_list_hb_review_barcodes_returns_distinct_hepsiburada_barcodes(db):
    db.upsert_orders([
        {"shipment_package_id": 1, "marketplace": "hepsiburada", "order_number": "HB1",
         "order_date": 0, "status": "Delivered", "customer": "C", "cargo_provider": None,
         "gross_amount": 0, "discount_amount": 0, "net_amount": 0},
        {"shipment_package_id": 2, "marketplace": "trendyol", "order_number": "TY1",
         "order_date": 0, "status": "Delivered", "customer": "C", "cargo_provider": None,
         "gross_amount": 0, "discount_amount": 0, "net_amount": 0},
    ])
    db.upsert_order_lines([
        {"shipment_package_id": 1, "marketplace": "hepsiburada", "barcode": "BC-1",
         "merchant_sku": "MS-1", "product_name": "P1", "quantity": 1,
         "line_unit_price": 10, "commission_rate": 0},
        {"shipment_package_id": 1, "marketplace": "hepsiburada", "barcode": "BC-1",
         "merchant_sku": "MS-1", "product_name": "P1", "quantity": 1,
         "line_unit_price": 10, "commission_rate": 0},
        {"shipment_package_id": 2, "marketplace": "trendyol", "barcode": "BC-TY",
         "merchant_sku": "MS-TY", "product_name": "PTY", "quantity": 1,
         "line_unit_price": 10, "commission_rate": 0},
    ])
    barcodes = db.list_hb_review_barcodes()
    assert barcodes == ["BC-1"]  # trendyol dışlandı, duplicate tekilleşti


def test_review_family_map_upsert_and_get(db):
    db.upsert_review_family_map([
        {"marketplace": "hepsiburada", "barcode": "BC-A", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B,BC-C"},
        {"marketplace": "hepsiburada", "barcode": "BC-B", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B,BC-C"},
        {"marketplace": "hepsiburada", "barcode": "BC-C", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B,BC-C"},
    ])
    fmap = db.get_review_family_map()
    assert fmap == {"BC-A": "BC-A", "BC-B": "BC-A", "BC-C": "BC-A"}


def test_review_family_map_upsert_updates_existing_barcode(db):
    db.upsert_review_family_map([
        {"marketplace": "hepsiburada", "barcode": "BC-A", "representative_sku": "BC-A", "family_skus": "BC-A"},
    ])
    db.upsert_review_family_map([
        {"marketplace": "hepsiburada", "barcode": "BC-A", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B"},
    ])
    fmap = db.get_review_family_map()
    assert fmap == {"BC-A": "BC-A"}  # ikinci satır oluşmadı, güncellendi


def test_get_known_product_url_returns_none_when_absent(db):
    assert db.get_known_product_url("SKU-X") is None


def test_get_known_product_url_returns_url_for_matching_sku(db):
    db.upsert_review_contents([{
        "external_review_id": "r1", "marketplace": "hepsiburada",
        "product_sku": "SKU-A", "product_url": "https://www.hepsiburada.com/urun-a",
        "star": 5, "content": None, "created_at": None,
        "merchant_id": None, "merchant_name": None,
        "is_purchase_verified": None, "raw_json": "{}",
    }])
    assert db.get_known_product_url("SKU-A") == "https://www.hepsiburada.com/urun-a"
    assert db.get_known_product_url("SKU-B") is None  # farklı sku, eşleşmemeli


def test_get_any_known_hb_product_url_returns_any_real_url(db):
    assert db.get_any_known_hb_product_url() is None
    db.upsert_review_contents([{
        "external_review_id": "r1", "marketplace": "hepsiburada",
        "product_sku": "SKU-Z", "product_url": "https://www.hepsiburada.com/urun-z",
        "star": 5, "content": None, "created_at": None,
        "merchant_id": None, "merchant_name": None,
        "is_purchase_verified": None, "raw_json": "{}",
    }])
    assert db.get_any_known_hb_product_url() == "https://www.hepsiburada.com/urun-z"
