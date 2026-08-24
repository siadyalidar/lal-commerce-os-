"""
hb_review_client.py
---------------------
Hepsiburada ürün yorumlarını (ApprovedUserContents) çeken, resmi/dokümante
OLMAYAN bir API client'ı. external_payout_scraper.py ile AYNI kategori:
DevTools üzerinden tespit edildi, önceden haber vermeden değişebilir/
kaldırılabilir.

⚠️ Bu dosyadaki davranış varsayımları 23.08.2026 tarihli Faz 0 denetiminde
(HB_Review_Faz0_Sonuc_Raporu.md) GERÇEK API'ye karşı doğrulandı. Doğrulanan
ve doğrulanMAYAN noktalar aşağıda CONFIRMED/UNVERIFIED olarak işaretli —
kod bu ayrımı YOK SAYMIYOR, UNVERIFIED noktalarda en güvenli/pahalı
davranışı seçiyor (örn. sort order'a güvenmemek için HER ZAMAN tüm
sayfaları çekiyor).

CONFIRMED (Faz 0):
  - Endpoint: GET .../queryapi/v2/ApprovedUserContents
  - Cookie/session GEREKMİYOR — sadece User-Agent/Referer/Origin/Accept
    header'larıyla, TEST EDİLEN KOŞULLARDA 13/13 istek başarılı oldu. Bu,
    "API kesinlikle authentication istemiyor" şeklinde KESİN bir genel
    iddia DEĞİL — sadece o test oturumunda gözlemlenen davranış.
  - Pagination from/size offset tabanlı, links.next ile takip edilir.
  - Sibling SKU'lar (aynı ürün ailesi) AYNI review havuzunu döndürüyor
    (tek bir ailede 4/4 SKU %100 örtüştü) — ama bu SADECE 1 aile üzerinden
    doğrulandı, farklı kategorilerde test edilmedi.
  - review.id her zaman mevcut ve dolu — dedup/PK için güvenilir.
  - createdAt her zaman mevcut ve dolu, ama SIRALAMA GÜVENİLİR DEĞİL
    (bkz. aşağıdaki UNVERIFIED).
  - review.content sıklıkla NULL (538 review'ın 335'i) — normal davranış,
    hata değil.

UNVERIFIED (bilinçli olarak KOD SEVİYESİNDE risk almadan bırakıldı):
  - API'nin review'ları newest→oldest sıraladığı FALSE çıktı (Faz 0 Test 3).
    Bu yüzden bu client HİÇBİR ZAMAN createdAt veya totalItemCount'a
    dayanarak erken durmuyor — links.next bitene kadar HER SAYFA çekilir.
  - İstek stabilitesi sadece tek, kısa (13 istek/~15sn) bir oturumda
    gözlemlendi. Üretimde (günler/haftalar, yüksek hacim) aynı stabilite
    GARANTİ EDİLMİYOR.
  - Discovery sırasında (barkodun kendi ürün sayfası URL'i henüz bilinmiyorken)
    kullanılan Referer, HB ana sayfası (DEFAULT_REFERER) — bu, Faz 0'da
    TEST EDİLMEDİ (Faz 0'da her zaman gerçek ürün sayfası Referer'ı
    kullanıldı). İlk üretim çalıştırmasında bu varsayımın doğru çıkıp
    çıkmadığı İZLENMELİ (bkz. sync sonucu raporundaki "kalan riskler").
"""

import json
import logging
import time

import requests

from http_client import get_json_with_retry

logger = logging.getLogger("trendyol_satis")

API_URL = "https://user-content-gw-hermes.hepsiburada.com/queryapi/v2/ApprovedUserContents"

# UNVERIFIED: Faz 0'da her istek gerçek bir ürün sayfası Referer'ıyla
# yapıldı. Discovery sırasında henüz bilinmeyen barkodlar için elimizde
# gerçek bir ürün sayfası URL'i yok -- bu yüzden HB ana sayfasına
# düşülüyor. Bu davranış production'da doğrulanana kadar UNVERIFIED kabul
# edilmeli.
DEFAULT_REFERER = "https://www.hepsiburada.com/"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
_HEADERS_TEMPLATE = {
    "User-Agent": _USER_AGENT,
    "Origin": "https://www.hepsiburada.com",
    "Accept": "application/json",
}

_REQUEST_TIMEOUT = 20
_DEFAULT_PAGE_SIZE = 50
_DEFAULT_MAX_PAGES = 50  # güvenlik sınırı -- sonsuz döngüye karşı
_DEFAULT_SLEEP_SECONDS = 1.0  # sayfalar arası, HB'yi gereksiz zorlamamak için


def _fetch_page(sku, referer, from_, size):
    """Tek bir sayfa çeker. Hata durumunda gövde önizlemesiyle birlikte
    RuntimeError fırlatır (external_payout_scraper.py'deki "gövdenin ilk 500
    karakterini hataya ekle" deseniyle tutarlı — debugging için)."""
    headers = dict(_HEADERS_TEMPLATE)
    headers["Referer"] = referer
    params = {
        "sku": sku,
        "from": from_,
        "size": size,
        "includeSiblingVariantContents": "true",
        "includeSummary": "true",
    }
    try:
        return get_json_with_retry(
            API_URL,
            params=params,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
            max_retries=3,
            backoff_mode="header_or_linear",
            backoff_base_seconds=2,
        )
    except requests.exceptions.HTTPError as exc:
        resp = exc.response
        status = resp.status_code if resp is not None else "?"
        body_preview = (resp.text or "")[:500] if resp is not None else ""
        raise RuntimeError(
            f"HB review API sku={sku} from={from_} -> HTTP {status}: {body_preview!r}"
        ) from exc
    except ValueError as exc:
        # requests'in JSON decode hatası ValueError'dan türer.
        raise RuntimeError(
            f"HB review API sku={sku} from={from_} -- JSON parse hatası: {exc}"
        ) from exc


def fetch_all_reviews_for_sku(
    sku,
    referer=None,
    size=_DEFAULT_PAGE_SIZE,
    max_pages=_DEFAULT_MAX_PAGES,
    sleep_seconds=_DEFAULT_SLEEP_SECONDS,
):
    """Verilen sku için TÜM review sayfalarını çeker (links.next bitene
    kadar). createdAt/totalItemCount'a dayanarak HİÇBİR ZAMAN erken durmaz
    (bkz. modül docstring'indeki UNVERIFIED sort-order notu).

    Döner: (all_reviews: list[dict], family_skus: set[str], page_count: int)
      all_reviews: HB'den gelen ham review nesnelerinin tam listesi
      family_skus: response'larda görülen tüm product.sku değerleri (sibling
        ailesinin keşfi için — Faz 0 Bölüm F/discovery mantığı)
      page_count: başarıyla çekilen sayfa sayısı (production logging/
        "total API requests" özeti için — bkz. hb_review_sync_tasks.py
        Aşama 7). Yalnızca BAŞARILI sayfaları sayar; bir sku'nun ilk sayfası
        bile alınamazsa exception fırlatılır ve page_count hiç dönmez —
        bu durumda çağıran taraf o isteği ayrıca 1 olarak saymalı (bkz.
        hb_review_sync_tasks.py'deki not) -- bu yüzden page_count TOPLAM
        API isteği sayısının kesin değil, alt sınır (en az bu kadar) bir
        tahminidir.

    İlk sayfa bile alınamazsa exception YUKARI FIRLATILIR (bu sku için sync
    tamamen başarısız sayılmalı, sessizce boş liste dönülmez). Sonraki bir
    sayfa (2. veya sonrası) başarısız olursa, o ana kadar toplanan veri
    KAYBEDİLMEDEN döndürülür (kısmi başarı) ve durum loglanır."""
    all_reviews = []
    family_skus = set()
    from_ = 0
    page_count = 0

    for page_num in range(max_pages):
        try:
            data = _fetch_page(sku, referer or DEFAULT_REFERER, from_, size)
        except Exception:
            if page_num == 0:
                raise
            logger.warning(
                f"[HB Review] sku={sku} sayfa {page_num} (from={from_}) alınamadı -- "
                f"buraya kadar toplanan {len(all_reviews)} review korunuyor"
            )
            break

        page_count += 1
        review_list = (
            (data.get("data") or {})
            .get("approvedUserContent", {})
            .get("approvedUserContentList", [])
        )
        all_reviews.extend(review_list)
        for r in review_list:
            sku_val = (r.get("product") or {}).get("sku")
            if sku_val:
                family_skus.add(sku_val)

        links_next = (data.get("links") or {}).get("next")
        if not links_next or not review_list:
            break

        from_ += size
        if sleep_seconds:
            time.sleep(sleep_seconds)

    return all_reviews, family_skus, page_count


def normalize_review(raw, marketplace="hepsiburada"):
    """Ham HB review nesnesini review_contents şemasına çevirir.
    external_review_id (id) eksikse None döner -- çağıran taraf bu review'ı
    SKIP etmeli (bkz. Bölüm H hata tablosu: "API şeması değişti/id kayboldu
    -> o review skip edilir")."""
    review_id = raw.get("id")
    if not review_id:
        return None

    product = raw.get("product") or {}
    review = raw.get("review") or {}
    order = raw.get("order") or {}
    is_verified = raw.get("isPurchaseVerified")

    return {
        "external_review_id": review_id,
        "marketplace": marketplace,
        "product_sku": product.get("sku"),
        "product_url": product.get("url"),
        "star": raw.get("star"),
        "content": review.get("content"),
        "created_at": raw.get("createdAt"),
        "merchant_id": order.get("merchantId"),
        "merchant_name": order.get("merchantName"),
        "is_purchase_verified": (
            1 if is_verified is True else (0 if is_verified is False else None)
        ),
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }
