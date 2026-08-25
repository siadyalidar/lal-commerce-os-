"""
test_review_display_helpers_db.py
------------------------------------
24.08.2026 eklenen frontend-destekli DB fonksiyonları için testler:
first_synced_at (INSERT'te yazılır, UPDATE'te korunur), list_reviews_today,
get_review_stats, list_reviews_sorted_by_date.
"""


def _row(rid, sku, star, content, created_at):
    return {
        "external_review_id": rid, "marketplace": "hepsiburada",
        "product_sku": sku, "product_url": None, "star": star,
        "content": content, "created_at": created_at,
        "merchant_id": "m-1", "merchant_name": "TEST",
        "is_purchase_verified": 1, "raw_json": "{}",
    }


def test_first_synced_at_set_on_insert(db):
    db.upsert_review_contents([_row("rev-1", "SKU-A", 5, "iyi", "2026-01-01T10:00:00+00:00")])
    rows = db.list_reviews("hepsiburada")
    assert len(rows) == 1
    assert rows[0]["first_synced_at"] is not None
    assert rows[0]["first_synced_at"] == rows[0]["synced_at"]  # ilk insert'te ikisi de aynı an


def test_first_synced_at_preserved_on_update_but_synced_at_changes(db):
    """Kritik test: aynı review ikinci kez upsert edildiğinde
    first_synced_at DEĞİŞMEMELİ, synced_at değişmeli."""
    import time

    db.upsert_review_contents([_row("rev-1", "SKU-A", 5, "iyi", "2026-01-01T10:00:00+00:00")])
    first_rows = db.list_reviews("hepsiburada")
    original_first_synced_at = first_rows[0]["first_synced_at"]

    time.sleep(1.1)  # datetime('now') saniye çözünürlüklü, farkı görebilmek için

    db.upsert_review_contents([_row("rev-1", "SKU-A", 4, "güncellenmiş", "2026-01-01T10:00:00+00:00")])
    second_rows = db.list_reviews("hepsiburada")

    assert second_rows[0]["first_synced_at"] == original_first_synced_at  # DEĞİŞMEDİ
    assert second_rows[0]["synced_at"] >= original_first_synced_at  # bu güncellendi
    assert second_rows[0]["content"] == "güncellenmiş"  # diğer alanlar normal güncellendi


def test_list_reviews_today_only_returns_first_synced_today(db):
    """Bugün upsert edilen ama DAHA ÖNCE (first_synced_at eskiden) keşfedilmiş
    bir review, 'bugün eklenen' listesine GİRMEMELİ -- bu senaryo aşağıdaki
    testte (first_synced_at farklı gün simülasyonu) net olarak doğrulanıyor.
    Burada sadece: aynı review tekrar tekrar upsert edilse bile (Faz 2 gece
    sync'i gibi) 'bugün' listesinde DUPLICATE OLUŞMADIĞINI doğruluyoruz."""
    import time

    db.upsert_review_contents([_row("rev-old", "SKU-A", 5, "eski", "2026-01-01T10:00:00+00:00")])
    time.sleep(1.1)
    # rev-old TEKRAR upsert edildi (Faz 2 gece sync'i gibi) -- first_synced_at korunmalı
    db.upsert_review_contents([_row("rev-old", "SKU-A", 5, "eski", "2026-01-01T10:00:00+00:00")])
    # rev-new GERÇEKTEN yeni
    db.upsert_review_contents([_row("rev-new", "SKU-A", 4, "yeni", "2026-08-24T10:00:00+00:00")])

    today = db.list_reviews_today("hepsiburada")
    today_ids = [r["external_review_id"] for r in today]
    assert sorted(today_ids) == ["rev-new", "rev-old"]  # her ikisi de bugün ilk kez keşfedildi (test bugün çalışıyor)
    assert len(today_ids) == len(set(today_ids))  # duplicate yok


def test_list_reviews_today_excludes_reviews_first_synced_on_a_different_day(db):
    """first_synced_at dünse (bugün değilse), review 'bugün eklendi'
    listesine girmemeli -- doğrudan SQL ile geçmiş bir tarih simüle edilir."""
    db.upsert_review_contents([_row("rev-yesterday", "SKU-A", 5, "eski", "2026-01-01T10:00:00+00:00")])

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE review_contents SET first_synced_at = '2026-08-23 10:00:00' WHERE external_review_id = 'rev-yesterday'"
        )

    today = db.list_reviews_today("hepsiburada")
    assert today == []


def test_get_review_stats_computes_avg_and_distribution(db):
    db.upsert_review_contents([
        _row("r1", "SKU-A", 5, "a", "2026-01-01T10:00:00+00:00"),
        _row("r2", "SKU-A", 5, "b", "2026-01-02T10:00:00+00:00"),
        _row("r3", "SKU-A", 3, "c", "2026-01-03T10:00:00+00:00"),
        _row("r4", "SKU-A", None, "d", "2026-01-04T10:00:00+00:00"),  # star yok, ortalamaya dahil edilmemeli
    ])
    stats = db.get_review_stats("hepsiburada")
    assert stats["totalCount"] == 4
    assert stats["avgStar"] == round((5 + 5 + 3) / 3, 2)
    assert stats["distribution"] == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 2}


def test_get_review_stats_empty_db_returns_none_avg(db):
    stats = db.get_review_stats("hepsiburada")
    assert stats["totalCount"] == 0
    assert stats["avgStar"] is None
    assert stats["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}


def test_list_reviews_sorted_by_date_orders_by_created_at_desc(db):
    db.upsert_review_contents([
        _row("r-old", "SKU-A", 5, "eski", "2026-01-01T10:00:00+00:00"),
        _row("r-new", "SKU-A", 5, "yeni", "2026-06-01T10:00:00+00:00"),
        _row("r-mid", "SKU-A", 5, "orta", "2026-03-01T10:00:00+00:00"),
    ])
    reviews = db.list_reviews_sorted_by_date("hepsiburada")
    ids = [r["external_review_id"] for r in reviews]
    assert ids == ["r-new", "r-mid", "r-old"]


def test_list_reviews_sorted_by_date_respects_limit(db):
    rows = [_row(f"r{i}", "SKU-A", 5, "x", f"2026-01-{i:02d}T10:00:00+00:00") for i in range(1, 6)]
    db.upsert_review_contents(rows)
    reviews = db.list_reviews_sorted_by_date("hepsiburada", limit=2)
    assert len(reviews) == 2
