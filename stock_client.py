"""
stock_client.py
----------------
Trendyol ve Hepsiburada'dan GÜNCEL SATILABİLİR STOK bilgisini çeker.
Sadece OKUMA yapar (stok güncellemesi YAZMAZ) — Ürün Ayarları sayfasındaki
"Düşük Stok Uyarısı" özelliği için kullanılır.

Trendyol: Product V2 - "Product Filtering - Approved Products Stock and Price"
  GET /integration/product/sellers/{sellerId}/products/approved/inventory-and-price
  ÖNEMLİ: Product V1 servisleri 10 Ağustos 2026'da devre dışı kalıyor, bu
  yüzden burada V2 kullanılıyor (mevcut app.py'deki sipariş servisleri
  Order V1/V2 farklı bir konu, onlara dokunulmadı).
  Kaynak: https://developers.trendyol.com/v2.0/docs/product-filtering-approved-products-stock-and-price

Hepsiburada: "Listing Bilgilerini Sorgulama"
  GET https://listing-external(-sit).hepsiburada.com/listings/merchantid/{merchantId}
  DİKKAT: Bu host (listing-external), app.py'deki sipariş senkronunda
  kullanılan oms-external host'undan FARKLI bir servis ailesi.
  Kaynak: https://developers.hepsiburada.com/hepsiburada/reference/listing-bilgilerini-sorgulama

  NOT (dürüst uyarı): HB dokümantasyon sayfası JS ile render edildiği için
  offset/limit parametre adları ve sayfalama davranışı bu oturumda tam
  doğrulanamadı — örnek response'ta "totalCount" ve "limit" alanları
  görüldü (bkz. changelog örneği), "offset" parametresi yaygın HB API
  konvansiyonuna dayanarak varsayıldı. İlk çalıştırmada STAGE ortamında
  (HEPSIBURADA_ENV=SIT) test edip, ürün sayısı totalCount'u geçmiyorsa
  (sonsuz döngü olmadan) doğru çalıştığını teyit edin.
"""

import os
import time

from dotenv import load_dotenv

from http_client import get_json_with_retry

load_dotenv()

# ============================================================
# Trendyol
# ============================================================
TY_SUPPLIER_ID = os.getenv("TRENDYOL_SUPPLIER_ID", "").strip()
TY_API_KEY = os.getenv("TRENDYOL_API_KEY", "").strip()
TY_API_SECRET = os.getenv("TRENDYOL_API_SECRET", "").strip()
TY_ENV = os.getenv("TRENDYOL_ENV", "PROD").strip().upper()
TY_INTEGRATOR_NAME = os.getenv("TRENDYOL_INTEGRATOR_NAME", "SelfIntegration").strip()
TY_BASE_URL = "https://apigw.trendyol.com" if TY_ENV == "PROD" else "https://stageapigw.trendyol.com"
TY_USER_AGENT = f"{TY_SUPPLIER_ID} - {TY_INTEGRATOR_NAME}"


def fetch_trendyol_stock():
    """Onaylı ürünlerin barkod/stockCode -> güncel satılabilir stok eşlemesini döner.

    Dönen her satır: {"marketplace": "trendyol", "sku": <stockCode veya barcode>,
                       "barcode": <barcode>, "quantity": <int>}
    'sku' olarak öncelikle stockCode kullanılır (order_lines.merchant_sku ile
    aynı kavram); stockCode boşsa barcode'a düşülür ki eşleşme hiç kaybolmasın.
    """
    if not (TY_SUPPLIER_ID and TY_API_KEY and TY_API_SECRET):
        raise RuntimeError("Trendyol API bilgileri eksik (.env dosyasını kontrol edin).")

    url = f"{TY_BASE_URL}/integration/product/sellers/{TY_SUPPLIER_ID}/products/approved/inventory-and-price"
    headers = {"User-Agent": TY_USER_AGENT}
    results = []
    page = 0
    size = 100  # bu serviste izin verilen maksimum sayfa boyutu

    while True:
        params = {"page": page, "size": size}
        data = get_json_with_retry(
            url, params=params, headers=headers, auth=(TY_API_KEY, TY_API_SECRET),
            timeout=30, max_retries=None, backoff_mode="fixed", backoff_base_seconds=3,
        )

        for item in data.get("content", []):
            for v in item.get("variants", []):
                sku = v.get("stockCode") or v.get("barcode")
                if not sku:
                    continue
                results.append({
                    "marketplace": "trendyol",
                    "sku": sku,
                    "barcode": v.get("barcode"),
                    "quantity": v.get("quantity"),
                })

        total_pages = data.get("totalPages") or 1
        page += 1
        # Servis dokümantasyonu page*size için 10.000 sınırı belirtiyor;
        # bunun üzerinde nextPageToken gerekir (şimdilik desteklenmiyor —
        # 10.000+ SKU'nuz olursa bu fonksiyona nextPageToken desteği eklenmeli).
        if page >= total_pages or page * size >= 10000:
            break
        time.sleep(0.2)

    return results


# ============================================================
# Hepsiburada
# ============================================================
HB_MERCHANT_ID = os.getenv("HEPSIBURADA_MERCHANT_ID", "").strip()
HB_USERNAME = os.getenv("HEPSIBURADA_USERNAME", "").strip()
HB_PASSWORD = os.getenv("HEPSIBURADA_PASSWORD", "").strip()
HB_ENV = os.getenv("HEPSIBURADA_ENV", "SIT").strip().upper()
HB_USER_AGENT = os.getenv("HEPSIBURADA_USER_AGENT", "softhydra_dev").strip()

HB_LISTING_BASE_URL = (
    "https://listing-external.hepsiburada.com"
    if HB_ENV == "PROD"
    else "https://listing-external-sit.hepsiburada.com"
)


def fetch_hepsiburada_stock():
    """Tüm listinglerin merchantSku -> availableStock eşlemesini döner.

    Dönen her satır: {"marketplace": "hepsiburada", "sku": <merchantSku>,
                       "barcode": None, "quantity": <int>}
    (Bu serviste barkod dönmüyor — eşleştirme merchantSku üzerinden yapılır,
    tıpkı order_lines.merchant_sku gibi.)
    """
    if not (HB_MERCHANT_ID and HB_USERNAME and HB_PASSWORD):
        raise RuntimeError("Hepsiburada API bilgileri eksik (.env dosyasını kontrol edin).")

    url = f"{HB_LISTING_BASE_URL}/listings/merchantid/{HB_MERCHANT_ID}"
    headers = {"User-Agent": HB_USER_AGENT}
    results = []
    offset = 0
    limit = 500

    while True:
        params = {"offset": offset, "limit": limit}
        data = get_json_with_retry(
            url, params=params, headers=headers, auth=(HB_USERNAME, HB_PASSWORD),
            timeout=30, max_retries=None, backoff_mode="fixed", backoff_base_seconds=3,
        )
        listings = data.get("listings", [])

        for l in listings:
            sku = l.get("merchantSku") or l.get("hepsiburadaSku")
            if not sku:
                continue
            results.append({
                "marketplace": "hepsiburada",
                "sku": sku,
                "barcode": None,
                "quantity": l.get("availableStock"),
            })

        total_count = data.get("totalCount", len(listings))
        offset += limit
        if offset >= total_count or not listings:
            break
        time.sleep(0.2)

    return results
