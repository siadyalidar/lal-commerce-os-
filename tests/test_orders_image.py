"""
tests/test_orders_image.py
----------------------------
/api/orders yanıtındaki her sipariş satırına eklenen imageUrl alanını kapsar.
Görsel kaynağı SADECE Trendyol'dan çekiliyor (product_images tablosu,
sku bazlı, marketplace ayrımı yapmadan) — ama sku eşleşiyorsa Hepsiburada
siparişlerinde de aynı görsel gösterilmeli (bkz. list_product_images
docstring'i: "aynı SKU birden fazla pazaryerinde satılıyorsa herhangi
birinin görseli yeterli").
"""

from datetime import datetime, timedelta

from database import upsert_order_lines, upsert_orders, upsert_product_images
from tests.conftest import auth_headers


def _seed_order(marketplace, shipment_package_id, order_number, barcode, merchant_sku, order_date=None):
    order_date = order_date or datetime.now()
    upsert_orders([{
        "shipment_package_id": shipment_package_id,
        "marketplace": marketplace,
        "order_number": order_number,
        "order_date": int(order_date.timestamp() * 1000),
        "status": "Delivered" if marketplace == "trendyol" else "AwaitingPackage",
        "customer": "Test Müşteri",
        "cargo_provider": "Test Kargo",
        "gross_amount": 100.0,
        "discount_amount": 0.0,
        "net_amount": 100.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": shipment_package_id,
        "marketplace": marketplace,
        "barcode": barcode,
        "merchant_sku": merchant_sku,
        "product_name": "Test Ürün",
        "quantity": 1,
        "line_unit_price": 100.0,
        "commission_rate": 10.0,
    }])


def test_order_line_includes_trendyol_image_url(client, db):
    upsert_product_images([
        {"marketplace": "trendyol", "sku": "SKU-001", "image_url": "https://cdn.trendyol.com/skus/001.jpg"},
    ])
    _seed_order("trendyol", 1001, "TY-ORD-1", "BC-001", "SKU-001")

    resp = client.get("/api/orders?full_history=true", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()

    order = next(o for o in data["orders"] if o["orderNumber"] == "TY-ORD-1")
    assert order["lines"][0]["imageUrl"] == "https://cdn.trendyol.com/skus/001.jpg"


def test_hepsiburada_order_line_reuses_trendyol_image_when_sku_matches(client, db):
    # Görsel sadece Trendyol'dan çekiliyor ama aynı SKU Hepsiburada'da da
    # satılıyorsa aynı görsel kullanılmalı (Hepsiburada için ayrı bir
    # görsel kaynağı yok).
    upsert_product_images([
        {"marketplace": "trendyol", "sku": "SKU-SHARED", "image_url": "https://cdn.trendyol.com/skus/shared.jpg"},
    ])
    _seed_order("hepsiburada", -2001, "HB-ORD-1", "BC-SHARED", "SKU-SHARED")

    resp = client.get("/api/orders?full_history=true", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()

    order = next(o for o in data["orders"] if o["orderNumber"] == "HB-ORD-1")
    assert order["lines"][0]["imageUrl"] == "https://cdn.trendyol.com/skus/shared.jpg"


def test_order_line_falls_back_to_barcode_when_merchant_sku_has_no_image(client, db):
    # Bazı ürünlerde stockCode boş gelip barcode'a düşülmüş olabilir
    # (bkz. fetch_trendyol_product_images docstring'i) — o durumda
    # image_map anahtarı barcode olur, merchant_sku değil.
    upsert_product_images([
        {"marketplace": "trendyol", "sku": "BC-002", "image_url": "https://cdn.trendyol.com/skus/by-barcode.jpg"},
    ])
    _seed_order("trendyol", 1002, "TY-ORD-2", "BC-002", "SKU-002")

    resp = client.get("/api/orders?full_history=true", headers=auth_headers())
    data = resp.get_json()

    order = next(o for o in data["orders"] if o["orderNumber"] == "TY-ORD-2")
    assert order["lines"][0]["imageUrl"] == "https://cdn.trendyol.com/skus/by-barcode.jpg"


def test_order_line_image_url_is_none_when_no_image_synced(client, db):
    # Hiç görsel senkronu yapılmamışsa (product_images tablosu boş ya da
    # bu SKU hiç görülmemiş) sessizce eski/yanlış bir şey uydurulmamalı —
    # açıkça None dönmeli, frontend bunu placeholder ile göstermeli.
    _seed_order("trendyol", 1003, "TY-ORD-3", "BC-003", "SKU-003")

    resp = client.get("/api/orders?full_history=true", headers=auth_headers())
    data = resp.get_json()

    order = next(o for o in data["orders"] if o["orderNumber"] == "TY-ORD-3")
    assert order["lines"][0]["imageUrl"] is None
