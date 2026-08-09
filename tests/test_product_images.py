"""
tests/test_product_images.py
------------------------------
09.08.2026'da eklenen product_images tablosu (Ürünler sayfası kart
tasarımı için Trendyol ürün görseli) — upsert/list davranışını kapsar.
"""

from database import list_product_images, upsert_product_images


def test_upsert_and_list_product_images(db):
    upsert_product_images([
        {"marketplace": "trendyol", "sku": "SH-8IN1-METER", "image_url": "https://cdn.example/a.jpg"},
        {"marketplace": "trendyol", "sku": "SH-5FILT-10IN", "image_url": "https://cdn.example/b.jpg"},
    ])

    result = list_product_images()

    assert result == {
        "SH-8IN1-METER": "https://cdn.example/a.jpg",
        "SH-5FILT-10IN": "https://cdn.example/b.jpg",
    }


def test_upsert_overwrites_existing_image(db):
    upsert_product_images([{"marketplace": "trendyol", "sku": "SH-8IN1-METER", "image_url": "https://cdn.example/old.jpg"}])
    upsert_product_images([{"marketplace": "trendyol", "sku": "SH-8IN1-METER", "image_url": "https://cdn.example/new.jpg"}])

    result = list_product_images()

    assert result == {"SH-8IN1-METER": "https://cdn.example/new.jpg"}


def test_null_image_url_excluded_from_list(db):
    # Bir üründe artık görsel bulunamazsa (kaldırıldı/API'de eksik) None
    # yazılabilir olmalı ama list_product_images() bunu dönmemeli —
    # frontend'in eski/geçersiz bir görseli göstermeye çalışmaması için.
    upsert_product_images([{"marketplace": "trendyol", "sku": "SH-PHM-001", "image_url": None}])

    result = list_product_images()

    assert result == {}


def test_upsert_product_images_empty_list_is_noop(db):
    upsert_product_images([])

    assert list_product_images() == {}
