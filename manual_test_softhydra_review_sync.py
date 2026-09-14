"""
manual_test_softhydra_review_sync.py
---------------------------------------
FAZ 1 SONRASI MANUEL DOGRULAMA -- TEK BARKOD (SoftHydra, HBCV0000CWG5QQ).

Bu script hb_review_client.py / hb_review_sync_tasks.py / database.py
fonksiyonlarini DOGRUDAN kullanir (hicbir mantik yeniden yazilmadi).
sync_hepsiburada_reviews.run() (TUM order_lines barkodlarini tarayan
gorev) CAGRILMAZ -- bunun yerine, o gorevin "yeni barkod" kod yolu
(_resolve_referer + _sync_one_sku + upsert_review_family_map) TEK bir
sku icin, elle tetiklenir. Boylece hem gercek production kod yolu test
edilir hem de kapsam tek barkodla sinirli kalir (tum katalog rollout'u
ayri, sonraki bir asama).

Gercek trendyol_data.db uzerinde calisir (database.DB_PATH override
EDILMEZ) -- bu, gercek bir urunun gercek review'larini production DB'ye
yazar.
"""

import json

import database
import hb_review_sync_tasks

TEST_SKU = "HBCV0000CWG5QQ"


def run_single_sku_sync(sku):
    stats = {
        "new_families_discovered": 0,
        "known_representatives_synced": 0,
        "reviews_upserted": 0,
        "reviews_skipped_missing_id": 0,
        "failed_skus": [],
    }

    before_rows = database.list_reviews("hepsiburada")
    before_count = len(before_rows)
    before_ids = {r["external_review_id"] for r in before_rows}
    before_synced_at = {r["external_review_id"]: r["synced_at"] for r in before_rows}

    referer, referer_source = hb_review_sync_tasks._resolve_referer(sku)
    print(f"Kullanilan Referer: {referer}")
    print(f"Referer kaynagi:    {referer_source}")

    family_skus = hb_review_sync_tasks._sync_one_sku(sku, stats)

    representative = None
    if family_skus is not None:
        family = family_skus if family_skus else {sku}
        representative = min(family)
        family_rows = [
            {
                "marketplace": "hepsiburada",
                "barcode": member,
                "representative_sku": representative,
                "family_skus": ",".join(sorted(family)),
            }
            for member in family
        ]
        if sku not in family:
            family_rows.append({
                "marketplace": "hepsiburada",
                "barcode": sku,
                "representative_sku": representative,
                "family_skus": ",".join(sorted(family)),
            })
        database.upsert_review_family_map(family_rows)

    after_rows = database.list_reviews("hepsiburada")
    after_count = len(after_rows)
    after_ids = {r["external_review_id"] for r in after_rows}
    after_synced_at = {r["external_review_id"]: r["synced_at"] for r in after_rows}

    inserted_ids = after_ids - before_ids
    updated_ids = {
        rid for rid in (before_ids & after_ids)
        if before_synced_at.get(rid) != after_synced_at.get(rid)
    }

    print(f"\nfamily_skus (kesif): {sorted(family_skus) if family_skus else family_skus}")
    print(f"representative_sku:  {representative}")
    print(f"\nDB satir sayisi (once):  {before_count}")
    print(f"DB satir sayisi (sonra): {after_count}")
    print(f"Yeni eklenen review id sayisi: {len(inserted_ids)}")
    print(f"Guncellenen (senkron tekrar calisip synced_at degisen) review id sayisi: {len(updated_ids)}")
    print(f"stats: {json.dumps(stats, ensure_ascii=False, indent=2)}")

    return {
        "stats": stats,
        "before_count": before_count,
        "after_count": after_count,
        "inserted_count": len(inserted_ids),
        "updated_count": len(updated_ids),
        "final_unique_ids": len(after_ids),
        "family_skus": family_skus,
        "representative": representative,
    }


if __name__ == "__main__":
    database.init_db()

    print("=" * 70)
    print("BIRINCI CALISTIRMA")
    print("=" * 70)
    result1 = run_single_sku_sync(TEST_SKU)

    print("\n" + "=" * 70)
    print("IKINCI CALISTIRMA (idempotency testi)")
    print("=" * 70)
    result2 = run_single_sku_sync(TEST_SKU)

    print("\n" + "=" * 70)
    print("OZET")
    print("=" * 70)
    print(f"1. calistirma -> eklenen: {result1['inserted_count']}, guncellenen: {result1['updated_count']}, "
          f"DB satir: {result1['before_count']} -> {result1['after_count']}")
    print(f"2. calistirma -> eklenen: {result2['inserted_count']}, guncellenen: {result2['updated_count']}, "
          f"DB satir: {result2['before_count']} -> {result2['after_count']}")
    print(f"2. calistirmada YENI eklenen review sayisi (0 olmali): {result2['inserted_count']}")
    print(f"Final benzersiz review_id sayisi: {result2['final_unique_ids']}")

    fmap = database.get_review_family_map()
    relevant = {k: v for k, v in fmap.items() if k == TEST_SKU or v == result1['representative']}
    print(f"\nFamily map (ilgili kayitlar): {relevant}")
