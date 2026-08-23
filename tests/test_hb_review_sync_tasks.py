"""
test_hb_review_sync_tasks.py
-------------------------------
sync_hepsiburada_reviews() orchestration testleri. hb_review_client.
fetch_all_reviews_for_sku() mock'lanıyor (gerçek ağ çağrısı YOK), gerçek DB
işlemleri izole `db` fixture'ı üzerinden çalışıyor.

Celery task'ını .run() ile senkron çağırıyoruz (task'ı worker'a göndermeden
doğrudan fonksiyon gövdesini test etmek için — .delay()/.apply_async()
broker gerektirir, bu testlerde gerekmiyor).
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.usefixtures("db")


def _seed_hb_order_line(db, barcode):
    db.upsert_orders([{
        "shipment_package_id": abs(hash(barcode)) % 100000,
        "marketplace": "hepsiburada", "order_number": f"HB-{barcode}",
        "order_date": 0, "status": "Delivered", "customer": "C",
        "cargo_provider": None, "gross_amount": 0, "discount_amount": 0, "net_amount": 0,
    }])
    db.upsert_order_lines([{
        "shipment_package_id": abs(hash(barcode)) % 100000,
        "marketplace": "hepsiburada", "barcode": barcode,
        "merchant_sku": barcode, "product_name": "P", "quantity": 1,
        "line_unit_price": 10, "commission_rate": 0,
    }])


def _raw_review(rid, sku, content="ok"):
    return {
        "id": rid,
        "product": {"sku": sku, "url": f"https://example.com/{sku}"},
        "order": {"merchantId": "m-1", "merchantName": "TEST"},
        "review": {"content": content},
        "star": 5,
        "createdAt": "2026-01-01T10:00:00+00:00",
        "isPurchaseVerified": True,
    }


def test_new_family_discovery_maps_all_sibling_barcodes_to_one_representative(db):
    """1 family -> 1 representative SKU: 3 sibling barkodu tek bir aile
    olarak keşfedilip AYNI representative_sku'ya bağlanmalı."""
    for bc in ["BC-B", "BC-A", "BC-C"]:
        _seed_hb_order_line(db, bc)

    def fake_fetch(sku, **kwargs):
        # Hangi barkod sorgulanırsa sorgulansın, aynı aile döner (Faz 0'daki
        # sibling paylaşımı davranışını simüle ediyor).
        reviews = [_raw_review(f"rev-{sku}-1", "BC-A"), _raw_review(f"rev-{sku}-2", "BC-B")]
        return reviews, {"BC-A", "BC-B", "BC-C"}

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    fmap = db.get_review_family_map()
    # Deterministik representative: alfabetik en küçük -> "BC-A"
    assert fmap == {"BC-A": "BC-A", "BC-B": "BC-A", "BC-C": "BC-A"}
    assert result["new_families_discovered"] == 3  # 3 barkod da "yeni" olarak işlendi
    assert result["failed_skus"] == []


def test_known_family_only_queries_representative_once(db):
    """Zaten keşfedilmiş bir ailede, representative DIŞINDAKİ barkodlar
    TEKRAR sorgulanmamalı (Faz 2 -- sadece representative sorgulanır)."""
    for bc in ["BC-A", "BC-B", "BC-C"]:
        _seed_hb_order_line(db, bc)
    db.upsert_review_family_map([
        {"marketplace": "hepsiburada", "barcode": "BC-A", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B,BC-C"},
        {"marketplace": "hepsiburada", "barcode": "BC-B", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B,BC-C"},
        {"marketplace": "hepsiburada", "barcode": "BC-C", "representative_sku": "BC-A", "family_skus": "BC-A,BC-B,BC-C"},
    ])

    called_skus = []

    def fake_fetch(sku, **kwargs):
        called_skus.append(sku)
        return [_raw_review("rev-1", "BC-A")], {"BC-A", "BC-B", "BC-C"}

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert called_skus == ["BC-A"]  # SADECE representative sorgulandı
    assert result["known_representatives_synced"] == 1
    assert result["new_families_discovered"] == 0


def test_partial_failure_one_sku_does_not_block_others(db):
    """Bir barkod başarısız olsa da diğerleri işlenmeye devam etmeli
    (Bölüm H: partial failure isolation)."""
    for bc in ["BC-OK", "BC-FAIL"]:
        _seed_hb_order_line(db, bc)

    def fake_fetch(sku, **kwargs):
        if sku == "BC-FAIL":
            raise RuntimeError("HB review API BC-FAIL -> HTTP 403: 'guvenlik'")
        return [_raw_review("rev-ok-1", "BC-OK")], {"BC-OK"}

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert len(result["failed_skus"]) == 1
    assert result["failed_skus"][0]["sku"] == "BC-FAIL"
    assert result["new_families_discovered"] == 1  # BC-OK yine de işlendi
    fmap = db.get_review_family_map()
    assert "BC-FAIL" not in fmap  # başarısız barkod family_map'e YAZILMADI
    assert "BC-OK" in fmap


def test_repeated_sync_run_is_idempotent_no_duplicate_reviews(db):
    """Aynı sync işlemi iki kez art arda çalıştırılırsa DB'de duplicate
    review OLUŞMAMALI (Sidar'ın açıkça istediği kritik test)."""
    _seed_hb_order_line(db, "BC-A")

    def fake_fetch(sku, **kwargs):
        return [_raw_review("rev-1", "BC-A"), _raw_review("rev-2", "BC-A")], {"BC-A"}

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        hb_review_sync_tasks.sync_hepsiburada_reviews.run()
        hb_review_sync_tasks.sync_hepsiburada_reviews.run()  # ikinci çalıştırma

    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 2  # 2 review, duplicate yok


def test_marketplace_trendyol_rows_unaffected(db):
    """Sync sadece marketplace='hepsiburada' üzerinde çalışmalı; önceden
    var olan trendyol review kayıtları (varsa) etkilenmemeli."""
    db.upsert_review_contents([{
        "external_review_id": "ty-rev-1", "marketplace": "trendyol",
        "product_sku": "TY-SKU", "product_url": None, "star": 4,
        "content": "trendyol yorumu", "created_at": None, "merchant_id": None,
        "merchant_name": None, "is_purchase_verified": None, "raw_json": "{}",
    }])
    _seed_hb_order_line(db, "BC-A")

    def fake_fetch(sku, **kwargs):
        return [_raw_review("rev-1", "BC-A")], {"BC-A"}

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    ty_rows = db.list_reviews("trendyol")
    assert len(ty_rows) == 1
    assert ty_rows[0]["external_review_id"] == "ty-rev-1"


def test_no_hb_barcodes_returns_zero_stats(db):
    """Hiç HB order_lines yoksa sync hatasız 'boş' bir sonuç dönmeli."""
    import hb_review_sync_tasks
    result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()
    assert result["new_families_discovered"] == 0
    assert result["known_representatives_synced"] == 0
    assert result["reviews_upserted"] == 0
    assert result["failed_skus"] == []
