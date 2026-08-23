"""
hb_review_sync_tasks.py
-------------------------
Hepsiburada ürün yorumlarını (review_contents) senkronize eden Celery task'ı.
payout_scrape_tasks.py ile AYNI desen: orchestration mantığı doğrudan task
fonksiyonunun içinde, client (hb_review_client.py) ve DB (database.py)
katmanları saf/atomik fonksiyonlar olarak kalıyor.

STRATEJİ — "Faz 1 keşif + Faz 2 optimize" (23.08.2026 onaylanan mimari
karar, bkz. HB_Review_Faz0_Sonuc_Raporu.md Bölüm 8):

  1) order_lines.barcode (marketplace='hepsiburada') listesinden, henüz
     hb_review_family_map'te KAYITLI OLMAYAN her barkod için TEK SEFERLİK
     bir keşif+sync sorgusu yapılır. Bu sorgunun kendisi zaten o ailenin
     TÜM review'larını döndürür (sibling paylaşımı CONFIRMED) -- ayrıca bir
     "şimdi de representative'i sorgula" adımına gerek yok, aynı çağrı hem
     keşif hem ilk sync'tir.
  2) Zaten bilinen (önceki bir çalıştırmada keşfedilmiş) barkodlar için,
     sadece HER AİLENİN representative_sku'su bir kez sorgulanır -- aynı
     aileye ait diğer barkodlar TEKRAR sorgulanmaz.
  3) review.id (external_review_id) PK olduğu için tüm upsert'ler
     idempotent -- aynı review birden fazla kez gelse de (sibling paylaşımı
     nedeniyle kaçınılmaz) DB'de ikinci satır oluşmaz.

⚠️ Bu task henüz Beat'e (celery_app.py beat_schedule) OTOMATİK EKLENMEDİ --
tıpkı tasks.py'deki backfill_hb_quantities deseninde olduğu gibi, canlı
ortamda küçük bir örneklemle (örn. tek barkod) manuel doğrulama yapılmadan
periyodik/otomatik çalıştırmak risklidir (özellikle DEFAULT_REFERER'ın
UNVERIFIED olması nedeniyle -- bkz. hb_review_client.py docstring'i).
Doğrulandıktan sonra celery_app.py'de örnek satırın yorumunu kaldırın.
"""

import logging

import database
from celery_app import celery_app
from hb_review_client import DEFAULT_REFERER, fetch_all_reviews_for_sku, normalize_review

logger = logging.getLogger("trendyol_satis")


def _resolve_referer(sku):
    """DEFAULT_REFERER (genel HB ana sayfası) fallback'ine düşmeden önce,
    mevcut DB'de ZATEN BULUNAN gerçek bir HB ürün URL'ini kullanmayı dener
    (23.08.2026 DEFAULT_REFERER risk çözümü). Yeni bir API keşfi YAPMAZ --
    sadece bu sync sisteminin kendi topladığı review_contents verisine bakar.

    Öncelik sırası:
      1) Bu sku için özel olarak bilinen product_url (get_known_product_url)
      2) DB'de herhangi bir sku için bilinen gerçek bir HB ürün URL'i
         (Faz 0: farklı sibling sorgularında AYNI Referer başarıyla
         çalıştı -- tam eşleşme şart değil, gerçek bir HB ürün sayfası olması
         yeterli görünüyor)
      3) DEFAULT_REFERER (genel ana sayfa) -- FALLBACK, açıkça loglanır

    NOT: Taze/boş bir review_contents tablosunda (ilk hiç sync) 1 ve 2 boş
    döner, kaçınılmaz olarak 3'e (fallback) düşülür -- bu beklenen bir
    durumdur, sonraki senkronlarda 1/2 devreye girecektir."""
    known = database.get_known_product_url(sku)
    if known:
        return known, "sku-specific"

    any_known = database.get_any_known_hb_product_url()
    if any_known:
        return any_known, "any-known"

    logger.warning(
        f"[HB Review Sync] sku={sku}: DB'de bilinen hiçbir gerçek HB ürün URL'i yok -- "
        f"FALLBACK Referer kullanılıyor ({DEFAULT_REFERER}). Bu UNVERIFIED bir varsayımdır, "
        f"sonucun başarılı olup olmadığı izlenmelidir."
    )
    return DEFAULT_REFERER, "fallback"


def _sync_one_sku(sku, stats):
    """Bir sku (barkod veya representative) için tüm sayfaları çeker,
    review_contents'e upsert eder. Başarısız olursa stats["failed_skus"]'a
    eklenir, exception YUTULMAZ ama task'ın diğer sku'ları işlemesine engel
    olmaz (bkz. Bölüm H: "partial failure isolation")."""
    referer, referer_source = _resolve_referer(sku)
    try:
        reviews, family_skus = fetch_all_reviews_for_sku(sku, referer=referer)
    except Exception as exc:
        logger.warning(
            f"[HB Review Sync] sku={sku} tamamen başarısız (referer_source={referer_source}): {exc}"
        )
        stats["failed_skus"].append({"sku": sku, "error": str(exc), "referer_source": referer_source})
        return None

    normalized_rows = []
    skipped_missing_id = 0
    for raw in reviews:
        normalized = normalize_review(raw)
        if normalized is None:
            skipped_missing_id += 1
            continue
        normalized_rows.append(normalized)

    if normalized_rows:
        database.upsert_review_contents(normalized_rows)

    stats["reviews_upserted"] += len(normalized_rows)
    if skipped_missing_id:
        stats["reviews_skipped_missing_id"] += skipped_missing_id
        logger.warning(
            f"[HB Review Sync] sku={sku}: {skipped_missing_id} review 'id' alanı "
            f"eksik olduğu için atlandı"
        )

    return family_skus


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300, name="hb_review_sync_tasks.sync_hepsiburada_reviews")
def sync_hepsiburada_reviews(self):
    """Hepsiburada review senkronizasyonu -- discover-then-optimize.
    Dönen dict, task sonucunun ve varsa başarısızlıkların özetidir; sync'in
    "sessizce başarılı görünmesi" istenmiyor -- failed_skus doluysa sonuç
    açıkça bunu yansıtır (bkz. Bölüm H "hata durumunda sync sessizce
    başarılı görünmemeli")."""
    stats = {
        "new_families_discovered": 0,
        "known_representatives_synced": 0,
        "reviews_upserted": 0,
        "reviews_skipped_missing_id": 0,
        "failed_skus": [],
    }

    all_barcodes = database.list_hb_review_barcodes()
    known_map = database.get_review_family_map()

    new_barcodes = [b for b in all_barcodes if b not in known_map]
    already_known_barcodes = [b for b in all_barcodes if b in known_map]

    # --- Faz 1: yeni barkodlar -- keşif + ilk sync tek çağrıda ---
    for barcode in new_barcodes:
        family_skus = _sync_one_sku(barcode, stats)
        if family_skus is None:
            # _sync_one_sku zaten failed_skus'a ekledi; bu barkod için
            # family_map yazılmaz, bir sonraki çalıştırmada tekrar denenir.
            continue

        # family_skus boş olabilir (0 review'lı bir ürün) -- bu durumda
        # temsilci olarak barkodun kendisini kullan.
        family = family_skus if family_skus else {barcode}
        representative = min(family)

        family_rows = [
            {
                "marketplace": "hepsiburada",
                "barcode": member_sku,
                "representative_sku": representative,
                "family_skus": ",".join(sorted(family)),
            }
            for member_sku in family
        ]
        # Sorguladığımız barkodun kendisi family_skus içinde olmayabilir
        # (örn. bu barkodun review'ları başka sibling sku'lar altında
        # görünmüş olabilir) -- güvenlik için ayrıca ekleniyor.
        if barcode not in family:
            family_rows.append({
                "marketplace": "hepsiburada",
                "barcode": barcode,
                "representative_sku": representative,
                "family_skus": ",".join(sorted(family)),
            })

        database.upsert_review_family_map(family_rows)
        stats["new_families_discovered"] += 1

    # --- Faz 2: zaten bilinen barkodlar -- sadece ailenin representative'i ---
    known_map_after_discovery = database.get_review_family_map()
    representatives_to_sync = {
        known_map_after_discovery[b]
        for b in already_known_barcodes
        if b in known_map_after_discovery
    }

    for representative in representatives_to_sync:
        family_skus = _sync_one_sku(representative, stats)
        if family_skus is not None:
            stats["known_representatives_synced"] += 1

    logger.info(
        f"[HB Review Sync] tamamlandı: {stats['new_families_discovered']} yeni aile keşfedildi, "
        f"{stats['known_representatives_synced']} bilinen representative senkronize edildi, "
        f"{stats['reviews_upserted']} review upsert edildi, "
        f"{len(stats['failed_skus'])} sku başarısız oldu"
    )
    return stats
