"""
faz0_hb_review_verify.py
--------------------------
FAZ 0 DOGRULAMA -- SADECE TANI/TEST SCRIPTI, PRODUCTION KODU DEGIL.
database.py / hb_review_client.py / celery_app.py gibi hicbir proje
dosyasina dokunmuyor. Sadece GET istegi atip sonuclari raporluyor.
"""

import time
from datetime import datetime

import requests

API_URL = "https://user-content-gw-hermes.hepsiburada.com/queryapi/v2/ApprovedUserContents"

HEADERS_TEMPLATE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.hepsiburada.com",
    "Accept": "application/json",
}

SLEEP_BETWEEN_REQUESTS = 1.5
PAGE_SIZE = 50

ALL_RAW_REVIEWS = []
REQUEST_LOG = []


def fetch_page(sku, referer, from_=0, size=PAGE_SIZE):
    headers = dict(HEADERS_TEMPLATE)
    headers["Referer"] = referer
    params = {
        "sku": sku,
        "from": from_,
        "size": size,
        "includeSiblingVariantContents": "true",
        "includeSummary": "true",
    }
    t0 = time.time()
    resp = requests.get(API_URL, params=params, headers=headers, timeout=15)
    elapsed = time.time() - t0
    REQUEST_LOG.append({
        "sku": sku, "from": from_, "size": size,
        "status_code": resp.status_code, "elapsed_s": round(elapsed, 3),
    })
    return resp


def fetch_all_pages(sku, referer, size=PAGE_SIZE, max_pages=20, sleep_s=SLEEP_BETWEEN_REQUESTS):
    pages = []
    from_ = 0
    seen_ids_order = []

    for page_num in range(max_pages):
        resp = fetch_page(sku, referer, from_=from_, size=size)
        if resp.status_code != 200:
            print(f"    [SAYFA {page_num}] HTTP {resp.status_code} -- durduruluyor")
            print(f"    Body preview: {resp.text[:300]!r}")
            break
        try:
            data = resp.json()
        except Exception as e:
            print(f"    [SAYFA {page_num}] JSON parse hatasi: {e}")
            print(f"    Body preview: {resp.text[:300]!r}")
            break

        review_list = (
            (data.get("data") or {})
            .get("approvedUserContent", {})
            .get("approvedUserContentList", [])
        )
        ALL_RAW_REVIEWS.extend(review_list)

        ids = [r.get("id") for r in review_list]
        created_ats = [r.get("createdAt") for r in review_list]
        product_skus = {(r.get("product") or {}).get("sku") for r in review_list}

        page_info = {
            "page_num": page_num,
            "from": from_,
            "size": size,
            "returned_count": len(review_list),
            "total_item_count": data.get("totalItemCount"),
            "first_id": ids[0] if ids else None,
            "last_id": ids[-1] if ids else None,
            "first_createdAt": created_ats[0] if created_ats else None,
            "last_createdAt": created_ats[-1] if created_ats else None,
            "ids": ids,
            "createdAts": created_ats,
            "product_skus": product_skus,
            "links_next": (data.get("links") or {}).get("next"),
        }
        pages.append(page_info)
        seen_ids_order.extend(ids)

        print(
            f"    [SAYFA {page_num}] from={from_} size={size} -> {len(review_list)} review | "
            f"totalItemCount={data.get('totalItemCount')} | "
            f"ilk_created={page_info['first_createdAt']} | son_created={page_info['last_createdAt']} | "
            f"links.next={'VAR' if page_info['links_next'] else 'YOK'}"
        )

        if not page_info["links_next"] or len(review_list) == 0:
            print(f"    [SAYFA {page_num}] pagination sona erdi")
            break

        from_ += size
        time.sleep(sleep_s)

    return pages, seen_ids_order


def analyze_family(label, sku, referer, size=PAGE_SIZE, sleep_s=SLEEP_BETWEEN_REQUESTS):
    print(f"\n{'='*72}\nAILE: {label}  (sorgu sku={sku})\n{'='*72}")
    pages, seen_ids_order = fetch_all_pages(sku, referer, size=size, sleep_s=sleep_s)

    if not pages:
        print("  HIC SAYFA CEKILEMEDI.")
        return None

    total_item_count = pages[0]["total_item_count"]
    all_ids = seen_ids_order
    unique_ids = set(all_ids)
    duplicate_count = len(all_ids) - len(unique_ids)

    all_created = [c for p in pages for c in p["createdAts"] if c]
    is_sorted_desc = all(all_created[i] >= all_created[i + 1] for i in range(len(all_created) - 1))
    is_sorted_asc = all(all_created[i] <= all_created[i + 1] for i in range(len(all_created) - 1))

    all_product_skus = set()
    for p in pages:
        all_product_skus |= p["product_skus"]

    print(f"\n  --- OZET: {label} ---")
    print(f"  totalItemCount (API bildirimi):        {total_item_count}")
    print(f"  Toplam cekilen review sayisi:            {len(all_ids)}")
    print(f"  Unique review ID sayisi:                  {len(unique_ids)}")
    print(f"  Duplicate ID sayisi:                      {duplicate_count}")
    print(f"  createdAt NEWEST->OLDEST (kesin azalan):  {is_sorted_desc}")
    print(f"  createdAt OLDEST->NEWEST (kesin artan):   {is_sorted_asc}")
    print(f"  Sayfa sayisi:                              {len(pages)}")
    print(f"  Bu sorguda gorulen product.sku degerleri: {sorted(s for s in all_product_skus if s)}")

    return {
        "label": label, "sku": sku,
        "total_item_count": total_item_count,
        "fetched_count": len(all_ids),
        "unique_ids": unique_ids,
        "duplicate_count": duplicate_count,
        "sorted_desc": is_sorted_desc,
        "sorted_asc": is_sorted_asc,
        "pages": pages,
        "product_skus_seen": all_product_skus,
    }


def check_field(reviews, path):
    present_count = 0
    non_null_count = 0
    total = len(reviews)
    for r in reviews:
        cur = r
        found = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                found = False
                break
        if found:
            present_count += 1
            if cur is not None and cur != "":
                non_null_count += 1
    return present_count, non_null_count, total


def schema_report():
    print(f"\n{'='*72}\nTEST 5 -- SCHEMA ALAN VARLIGI (toplam {len(ALL_RAW_REVIEWS)} review uzerinden)\n{'='*72}")
    fields_to_check = [
        (["id"], "id"),
        (["product", "sku"], "product.sku"),
        (["product", "url"], "product.url"),
        (["star"], "star"),
        (["review", "content"], "review.content"),
        (["createdAt"], "createdAt"),
        (["order", "merchantId"], "order.merchantId"),
        (["order", "merchantName"], "order.merchantName"),
        (["isPurchaseVerified"], "isPurchaseVerified"),
    ]
    for path, name in fields_to_check:
        present, non_null, total = check_field(ALL_RAW_REVIEWS, path)
        if total == 0:
            status = "VERI YOK"
        elif present == total and non_null == total:
            status = "ALWAYS PRESENT (hic null degil)"
        elif present == total and non_null < total:
            status = f"ALWAYS PRESENT (anahtar var ama {total - non_null}/{total} null)"
        elif present > 0:
            status = f"SOMETIMES PRESENT ({present}/{total} objede anahtar var)"
        else:
            status = "NEVER GORULDU (bu ornekte)"
        print(f"  {name:28s} -> {status}")


def sibling_comparison(sibling_results, family_label):
    print(f"\n{'='*72}\nTEST 2 -- SIBLING ID-SET KARSILASTIRMASI ({family_label})\n{'='*72}")
    id_sets = {}
    for sku, r in sibling_results.items():
        if r:
            id_sets[sku] = r["unique_ids"]
            print(f"  {sku}: {len(r['unique_ids'])} unique id | totalItemCount={r['total_item_count']}")

    if len(id_sets) < 2:
        print("  YETERSIZ VERI -- en az 2 sibling SKU sonucu gerekli.")
        return

    skus_list = list(id_sets.keys())
    base_sku = skus_list[0]
    base_set = id_sets[base_sku]
    all_identical = True
    for sku in skus_list[1:]:
        other = id_sets[sku]
        intersection = base_set & other
        union = base_set | other
        exact_match = base_set == other
        print(
            f"  {base_sku} vs {sku}: intersection={len(intersection)} | "
            f"union={len(union)} | TAM ESLESME={exact_match}"
        )
        if not exact_match:
            all_identical = False

    verdict = "CONFIRMED" if all_identical else "UNVERIFIED / CELISKI"
    print(f"\n  SONUC ({family_label}): {verdict} -- tum sorgulanan sibling SKU'lar "
          f"{'AYNI review havuzunu donuyor' if all_identical else 'AYNI review havuzunu DONMUYOR, farkli sonuclar var'}")
    return all_identical


def request_stability_report():
    print(f"\n{'='*72}\nTEST 6/7 -- ISTEK STABILITESI (agresif rate-limit testi DEGIL)\n{'='*72}")
    total = len(REQUEST_LOG)
    ok_count = sum(1 for r in REQUEST_LOG if r["status_code"] == 200)
    non_200 = [r for r in REQUEST_LOG if r["status_code"] != 200]
    avg_elapsed = sum(r["elapsed_s"] for r in REQUEST_LOG) / total if total else 0

    print(f"  Bu oturumda atilan toplam istek sayisi: {total}")
    print(f"  HTTP 200 donen istek sayisi:              {ok_count}/{total}")
    print(f"  Ortalama yanit suresi:                     {avg_elapsed:.3f} sn")
    if non_200:
        print("  200 DISI YANITLAR:")
        for r in non_200:
            print(f"    sku={r['sku']} from={r['from']} -> HTTP {r['status_code']}")
    else:
        print("  Bu oturumda hicbir istek 200 disi yanit vermedi.")

    print(
        "\n  NOT (Test 7 ifade duzeltmesi): Bu script boyunca hicbir cookie/session/"
        "credential kullanilmadan (sadece User-Agent/Referer/Origin/Accept header'lariyla) "
        f"{ok_count}/{total} istek basarili (HTTP 200) sonuc verdi. Bu, 'API kesinlikle "
        "authentication istemiyor' seklinde KESIN bir iddia DEGIL -- sadece test edilen "
        "kosullarda, bu oturumda gozlemlenen davranistir."
    )


if __name__ == "__main__":
    print("HEPSIBURADA REVIEW API -- FAZ 0 DOGRULAMA")
    print(f"Baslangic: {datetime.now().isoformat()}\n")

    bikini_referer = (
        "https://www.hepsiburada.com/angelsin-siyah-yuksel-bel-bikini-takim-renkli-"
        "detaylarla-38-beden-80-polyamid-20-likra-p-HBV00000ONEUV-yorumlari"
    )
    bikini_siblings = ["HBV00000ONEUV", "HBV00000ONEUW", "HBV00000ONEUX", "HBV00000ONEUU"]

    print(f"\n{'#'*72}\n# TEST 1 + 3 + 4 -- AILE A (cok varyantli, 133 review)\n{'#'*72}")
    sibling_results_a = {}
    for sku in bikini_siblings:
        sibling_results_a[sku] = analyze_family(f"Angelsin Bikini [{sku}]", sku, bikini_referer)

    softhydra_referer = (
        "https://www.hepsiburada.com/softhydra-10-inc-standart-5-li-su-aritma-filtre-seti-"
        "80-gpd-membran-dahil-full-bakim-paketi-nsf-sertifikali-tum-acik-kasa-cihazlara-"
        "uyumlu-hediye-filtre-degisim-seti-ve-montaj-ekipmanli-p-HBCV0000CWG5QQ-yorumlari"
    )
    print(f"\n{'#'*72}\n# TEST 1 + 3 + 4 -- AILE B (tek/az varyantli, 6 review)\n{'#'*72}")
    softhydra_result = analyze_family("SoftHydra Filtre [HBCV0000CWG5QQ]", "HBCV0000CWG5QQ", softhydra_referer)

    sibling_comparison(sibling_results_a, "Angelsin Bikini Ailesi")
    schema_report()
    request_stability_report()

    print(f"\n{'='*72}\nTAMAMLANDI: {datetime.now().isoformat()}\n{'='*72}")
