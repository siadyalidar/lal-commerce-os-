"""
test_hb_review_sync_tasks.py
-------------------------------
sync_hepsiburada_reviews() orchestration testleri. hb_review_client.
fetch_all_reviews_for_sku() mock'lanıyor (gerçek ağ çağrısı YOK), gerçek DB
işlemleri izole `db` fixture'ı üzerinden çalışıyor. sync_lock.py (Redis
tabanlı) her testte mock'lanıyor -- test ortamında gerçek Redis'e
bağlanılmıyor.

Celery task'ını .run() ile senkron çağırıyoruz (task'ı worker'a göndermeden
doğrudan fonksiyon gövdesini test etmek için — .delay()/.apply_async()
broker gerektirir, bu testlerde gerekmiyor).
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def mock_sync_lock():
    """Tüm testlerde gerçek Redis'e bağlanmadan kilit alınmış gibi davran.
    Kilit-özel testler (lock zaten tutuluyorsa ne olur) bunu KENDİ İÇİNDE
    ayrıca override eder."""
    with patch("hb_review_sync_tasks.acquire_sync_lock", return_value="test-token") as mock_acquire, \
         patch("hb_review_sync_tasks.release_sync_lock") as mock_release:
        yield mock_acquire, mock_release


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


# ---------------------------------------------------------------
# 1. Beat/task registration
# ---------------------------------------------------------------

def test_task_is_registered_with_expected_name(db):
    import hb_review_sync_tasks
    assert hb_review_sync_tasks.sync_hepsiburada_reviews.name == "hb_review_sync_tasks.sync_hepsiburada_reviews"


def test_beat_schedule_does_not_auto_activate_review_sync(db):
    """Beat'e KOD OLARAK bağlanabilir ama şu an için "hb-review-sync" AKTİF
    bir schedule girdisi olarak KAYITLI OLMAMALI (kontrollü rollout onayı
    bekleniyor, bkz. Aşama 10)."""
    from celery_app import celery_app
    assert "hb-review-sync" not in celery_app.conf.beat_schedule


# ---------------------------------------------------------------
# 2-4. HB barcode discovery / family grouping / representative seçimi
# ---------------------------------------------------------------

def test_new_family_discovery_maps_all_sibling_barcodes_to_one_representative(db):
    """1 family -> 1 representative SKU: 3 sibling barkodu tek bir aile
    olarak keşfedilip AYNI representative_sku'ya bağlanmalı."""
    for bc in ["BC-B", "BC-A", "BC-C"]:
        _seed_hb_order_line(db, bc)

    def fake_fetch(sku, **kwargs):
        reviews = [_raw_review(f"rev-{sku}-1", "BC-A"), _raw_review(f"rev-{sku}-2", "BC-B")]
        return reviews, {"BC-A", "BC-B", "BC-C"}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    fmap = db.get_review_family_map()
    assert fmap == {"BC-A": "BC-A", "BC-B": "BC-A", "BC-C": "BC-A"}
    assert result["new_families_discovered"] == 3
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
        return [_raw_review("rev-1", "BC-A")], {"BC-A", "BC-B", "BC-C"}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert called_skus == ["BC-A"]
    assert result["known_representatives_synced"] == 1
    assert result["new_families_discovered"] == 0


# ---------------------------------------------------------------
# 6. Idempotent sync (+ inserted/updated ayrımı)
# ---------------------------------------------------------------

def test_repeated_sync_run_is_idempotent_no_duplicate_reviews(db):
    """Aynı sync işlemi iki kez art arda çalıştırılırsa DB'de duplicate
    review OLUŞMAMALI."""
    _seed_hb_order_line(db, "BC-A")

    def fake_fetch(sku, **kwargs):
        return [_raw_review("rev-1", "BC-A"), _raw_review("rev-2", "BC-A")], {"BC-A"}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result1 = hb_review_sync_tasks.sync_hepsiburada_reviews.run()
        result2 = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 2  # duplicate yok

    # 1. çalıştırma: keşif (discovery) olarak 2 review INSERT edildi
    assert result1["reviews_inserted"] == 2
    assert result1["reviews_updated"] == 0

    # 2. çalıştırma: barkod artık "bilinen" -- representative olarak
    # sorgulanıp aynı 2 review UPDATE edildi, yeni INSERT olmamalı
    assert result2["reviews_inserted"] == 0
    assert result2["reviews_updated"] == 2


# ---------------------------------------------------------------
# 7. Failed family isolation
# ---------------------------------------------------------------

def test_partial_failure_one_sku_does_not_block_others(db):
    """Bir barkod başarısız olsa da diğerleri işlenmeye devam etmeli."""
    for bc in ["BC-OK", "BC-FAIL"]:
        _seed_hb_order_line(db, bc)

    def fake_fetch(sku, **kwargs):
        if sku == "BC-FAIL":
            raise RuntimeError("HB review API BC-FAIL -> HTTP 403: 'guvenlik'")
        return [_raw_review("rev-ok-1", "BC-OK")], {"BC-OK"}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert len(result["failed_skus"]) == 1
    assert result["failed_skus"][0]["sku"] == "BC-FAIL"
    assert result["failed_skus"][0]["phase"] == "discovery"
    assert result["new_families_discovered"] == 1  # BC-OK yine de işlendi
    fmap = db.get_review_family_map()
    assert "BC-FAIL" not in fmap
    assert "BC-OK" in fmap


# ---------------------------------------------------------------
# 10. Trendyol isolation
# ---------------------------------------------------------------

def test_marketplace_trendyol_rows_unaffected(db):
    db.upsert_review_contents([{
        "external_review_id": "ty-rev-1", "marketplace": "trendyol",
        "product_sku": "TY-SKU", "product_url": None, "star": 4,
        "content": "trendyol yorumu", "created_at": None, "merchant_id": None,
        "merchant_name": None, "is_purchase_verified": None, "raw_json": "{}",
    }])
    _seed_hb_order_line(db, "BC-A")

    def fake_fetch(sku, **kwargs):
        return [_raw_review("rev-1", "BC-A")], {"BC-A"}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    ty_rows = db.list_reviews("trendyol")
    assert len(ty_rows) == 1
    assert ty_rows[0]["external_review_id"] == "ty-rev-1"


def test_no_hb_barcodes_returns_zero_stats(db):
    import hb_review_sync_tasks
    result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()
    assert result["new_families_discovered"] == 0
    assert result["known_representatives_synced"] == 0
    assert result["reviews_inserted"] == 0
    assert result["reviews_updated"] == 0
    assert result["failed_skus"] == []
    assert result["total_barcodes"] == 0


# ---------------------------------------------------------------
# 8. Logging / summary alanları
# ---------------------------------------------------------------

def test_summary_stats_contain_all_required_fields(db):
    """Aşama 7'nin istediği tüm özet alanları stats dict'inde bulunmalı."""
    _seed_hb_order_line(db, "BC-A")

    def fake_fetch(sku, **kwargs):
        return [_raw_review("rev-1", "BC-A")], {"BC-A"}, 2  # 2 sayfa simüle

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    for field in [
        "total_barcodes", "new_families_discovered", "known_representatives_synced",
        "reviews_inserted", "reviews_updated", "reviews_skipped_missing_id",
        "failed_skus", "api_requests", "skipped_due_to_limit", "duration_seconds",
    ]:
        assert field in result, f"eksik alan: {field}"

    assert result["total_barcodes"] == 1
    assert result["api_requests"] == 2  # fake_fetch'in page_count'u
    assert result["duration_seconds"] >= 0


# ---------------------------------------------------------------
# 9. limit / kontrollü rollout davranışı
# ---------------------------------------------------------------

def test_limit_restricts_number_of_skus_processed(db):
    """limit=2 verilirse, 5 yeni barkoddan sadece 2'si işlenmeli, geri
    kalanı skipped_due_to_limit olarak sayılmalı."""
    barcodes = ["BC-1", "BC-2", "BC-3", "BC-4", "BC-5"]
    for bc in barcodes:
        _seed_hb_order_line(db, bc)

    processed = []

    def fake_fetch(sku, **kwargs):
        processed.append(sku)
        return [_raw_review(f"rev-{sku}", sku)], {sku}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run(limit=2)

    assert len(processed) == 2
    assert result["new_families_discovered"] == 2
    assert result["skipped_due_to_limit"] == 3


def test_limit_none_processes_all_barcodes(db):
    """limit=None (varsayılan) TÜM barkodları işlemeli -- sınırsız."""
    barcodes = ["BC-1", "BC-2", "BC-3"]
    for bc in barcodes:
        _seed_hb_order_line(db, bc)

    def fake_fetch(sku, **kwargs):
        return [_raw_review(f"rev-{sku}", sku)], {sku}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert result["new_families_discovered"] == 3
    assert result["skipped_due_to_limit"] == 0


def test_limit_shared_between_discovery_and_representative_phases(db):
    """limit, keşif (Faz 1) ve representative (Faz 2) arasında PAYLAŞILAN
    tek bir bütçedir -- ikisi toplamda limit'i aşmamalı."""
    _seed_hb_order_line(db, "BC-NEW")
    _seed_hb_order_line(db, "BC-KNOWN")
    db.upsert_review_family_map([
        {"marketplace": "hepsiburada", "barcode": "BC-KNOWN", "representative_sku": "BC-KNOWN", "family_skus": "BC-KNOWN"},
    ])

    processed = []

    def fake_fetch(sku, **kwargs):
        processed.append(sku)
        return [_raw_review(f"rev-{sku}", sku)], {sku}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run(limit=1)

    # Sadece 1 sku işlenmeli (ilk sırada Faz 1 -- BC-NEW), Faz 2'ye bütçe kalmamalı
    assert len(processed) == 1
    assert result["skipped_due_to_limit"] == 1


# ---------------------------------------------------------------
# Kilit (paralel çalışmayı önleme)
# ---------------------------------------------------------------

def test_sync_skipped_when_lock_already_held(db):
    """Kilit zaten tutuluyorsa (paralel bir sync devam ediyorsa), task
    hemen 'skipped' sonucu dönmeli, HİÇBİR barkod işlenmemeli."""
    _seed_hb_order_line(db, "BC-A")

    fetch_called = MagicMock()

    with patch("hb_review_sync_tasks.acquire_sync_lock", return_value=None), \
         patch("hb_review_sync_tasks.release_sync_lock") as mock_release, \
         patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", fetch_called):
        import hb_review_sync_tasks
        result = hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert result.get("skipped") is True
    fetch_called.assert_not_called()
    mock_release.assert_not_called()  # kilit hiç alınmadığı için release de çağrılmamalı


def test_lock_released_even_when_sync_raises_unexpectedly(db):
    """Beklenmeyen bir hata task'ı patlatsa bile kilit MUTLAKA
    serbest bırakılmalı (finally bloğu) -- yoksa sonraki sync'ler sonsuza
    kadar bloklanır."""
    _seed_hb_order_line(db, "BC-A")

    with patch("hb_review_sync_tasks.database.list_hb_review_barcodes", side_effect=RuntimeError("beklenmeyen DB hatası")):
        import hb_review_sync_tasks
        with pytest.raises(RuntimeError):
            hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    # autouse mock_sync_lock fixture'daki release_sync_lock mock'u çağrıldı mı kontrol edelim
    import hb_review_sync_tasks as hrst
    hrst.release_sync_lock.assert_called_once()


# ---------------------------------------------------------------
# DEFAULT_REFERER risk çözümü (23.08.2026, 24.08.2026 canlıda doğrulandı)
# ---------------------------------------------------------------

def test_resolve_referer_falls_back_to_default_when_db_empty(db):
    import hb_review_sync_tasks
    from hb_review_client import DEFAULT_REFERER

    referer, source = hb_review_sync_tasks._resolve_referer("SKU-NEW")
    assert referer == DEFAULT_REFERER
    assert source == "fallback"


def test_resolve_referer_prefers_sku_specific_known_url(db):
    import hb_review_sync_tasks

    db.upsert_review_contents([{
        "external_review_id": "rev-1", "marketplace": "hepsiburada",
        "product_sku": "SKU-A", "product_url": "https://www.hepsiburada.com/gercek-urun-a",
        "star": 5, "content": None, "created_at": None,
        "merchant_id": None, "merchant_name": None,
        "is_purchase_verified": None, "raw_json": "{}",
    }])
    referer, source = hb_review_sync_tasks._resolve_referer("SKU-A")
    assert referer == "https://www.hepsiburada.com/gercek-urun-a"
    assert source == "sku-specific"


def test_resolve_referer_falls_back_to_any_known_url_for_different_sku(db):
    import hb_review_sync_tasks

    db.upsert_review_contents([{
        "external_review_id": "rev-1", "marketplace": "hepsiburada",
        "product_sku": "SKU-OTHER", "product_url": "https://www.hepsiburada.com/baska-urun",
        "star": 5, "content": None, "created_at": None,
        "merchant_id": None, "merchant_name": None,
        "is_purchase_verified": None, "raw_json": "{}",
    }])
    referer, source = hb_review_sync_tasks._resolve_referer("SKU-NEW-BARCODE")
    assert referer == "https://www.hepsiburada.com/baska-urun"
    assert source == "any-known"


def test_sync_uses_resolved_referer_not_hardcoded(db):
    _seed_hb_order_line(db, "BC-A")
    db.upsert_review_contents([{
        "external_review_id": "rev-old", "marketplace": "hepsiburada",
        "product_sku": "SKU-OLD", "product_url": "https://www.hepsiburada.com/onceden-bilinen",
        "star": 5, "content": None, "created_at": None,
        "merchant_id": None, "merchant_name": None,
        "is_purchase_verified": None, "raw_json": "{}",
    }])

    captured_referers = []

    def fake_fetch(sku, referer=None, **kwargs):
        captured_referers.append(referer)
        return [_raw_review("rev-1", "BC-A")], {"BC-A"}, 1

    with patch("hb_review_sync_tasks.fetch_all_reviews_for_sku", side_effect=fake_fetch):
        import hb_review_sync_tasks
        hb_review_sync_tasks.sync_hepsiburada_reviews.run()

    assert captured_referers == ["https://www.hepsiburada.com/onceden-bilinen"]
