"""
hb_qna_client.py
------------------------
Hepsiburada "Satıcıya Sor" (Ask To Seller) entegrasyonu client'ı.
sync_core.py'deki HB_USERNAME/HB_PASSWORD/HB_USER_AGENT ve
get_json_with_retry() aynen yeniden kullanılır -- Sidar'ın 30.08.2026
teyidine göre auth zaten çalışıyor (aynı kimlik bilgileriyle diğer HB
servisleri kullanılıyor), sadece bu servis için host/header farklı.

CONFIRMED (30.08.2026 Faz 0, Hepsiburada Developer Portal'dan):
  Sunucu: https://api-asktoseller-merchant{-sit}.hepsiburada.com
          (SIT/test'te "-sit" var, PROD'da yok -- HB_BASE_URL ile aynı desen)
  Auth: Basic Auth + merchantId header + User-Agent header (path'te DEĞİL,
        HB_PACKAGES_PATH gibi diğer HB endpoint'lerinden FARKLI olarak).

  GET /api/v1.0/issues
      Query: status (1=WaitingForAnswer,2=Answered,3=Rejected,4=AutoClosed
             -- ama response'ta status STRING olarak "WaitingForAnswer" vb.
             geliyor, doküman örneklerinde tutarsızlık var, response'taki
             string değer esas alındı), page (1'den başlar), size (varsayılan
             25), sortBy, desc, minCreatedAt/maxCreatedAt.
      Response: items[] -- issueNumber, createdAt, status, expireDate,
             product.sku, product.name, lastContent, conversations[]
             (id, from: Customer/Merchant, content, createdAt).
      totalPages YOK (Trendyol'dan farklı) -- sayfalama items boşalana/
      size'dan az gelene kadar devam eder.

  POST /api/v1.0/issues/{number}/answer
      multipart/form-data. Alanlar: Answer (metin, max 2000 karakter),
      Files (opsiyonel ekler). Header: merchantId, User-Agent (Basic Auth).

  ÖNEMLİ: Cevaplama için satıcının 1 iş günü hakkı var (expireDate alanında
  belirtilir), süresi geçen sorular otomatik AutoClosed olur ve bir daha
  cevaplanamaz -- servis hata döner. Bu yüzden sync sıklığı Trendyol'daki
  gibi günde bir kez DEĞİL, daha sık olmalı (bkz. celery_app.py Beat kaydı).

  UNVERIFIED: HB dokümanında Trendyol'daki gibi bir MIN karakter kısıtı
  belirtilmedi -- bu client min kontrolü YAPMAZ (icat edilmedi).

DRAFT-ONLY MİMARİ: send_answer() burada TANIMLI ama hiçbir yerden OTOMATİK
ÇAĞRILMIYOR -- Trendyol client'ıyla aynı karar (29.08.2026), Sidar panelden
taslağı onaylayıp manuel gönderecek.
"""

import requests

from sync_core import HB_USERNAME, HB_PASSWORD, HB_USER_AGENT, HB_MERCHANT_ID, HB_ENV
from http_client import get_json_with_retry

HB_QNA_BASE_URL = (
    "https://api-asktoseller-merchant.hepsiburada.com"
    if HB_ENV == "PROD"
    else "https://api-asktoseller-merchant-sit.hepsiburada.com"
)


def hb_qna_get(path, params=None, max_retries=5, throttle_seconds=0.1):
    """hepsiburada_get ile AYNI auth/retry mantığı, ama merchantId AYRI bir
    header (diğer HB endpoint'lerinde path içinde geçiyordu)."""
    return get_json_with_retry(
        f"{HB_QNA_BASE_URL}{path}", params=params,
        headers={"User-Agent": HB_USER_AGENT, "merchantId": HB_MERCHANT_ID},
        auth=(HB_USERNAME, HB_PASSWORD), timeout=30, max_retries=max_retries,
        throttle_seconds=throttle_seconds, backoff_mode="header_or_linear",
        backoff_base_seconds=2, retry_wait_header="X-RateLimit-Reset",
    )


def hb_qna_post(path, data, files=None):
    """multipart/form-data POST -- hb_qna_get'teki AYNI auth/header kurulumu,
    ama get_json_with_retry sadece GET için (retry mantığı burada yok,
    Trendyol POST client'ıyla aynı minimal desen)."""
    resp = requests.post(
        f"{HB_QNA_BASE_URL}{path}", data=data, files=files or {},
        headers={"User-Agent": HB_USER_AGENT, "merchantId": HB_MERCHANT_ID},
        auth=(HB_USERNAME, HB_PASSWORD), timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_question_text(item):
    """Sorunun asıl metni conversations[]'teki İLK from=Customer mesajı --
    lastContent Answered durumunda satıcının son mesajı olabilir (doküman
    net değil), bu yüzden conversations esas alınır."""
    for conv in item.get("conversations", []):
        if conv.get("from") == "Customer":
            return conv.get("content")
    return item.get("lastContent")


def _extract_answer_text(item):
    """Cevaplanmış sorularda conversations[]'teki SON from=Merchant mesajı
    -- AI'nin few-shot öğrenme kaynağı (Trendyol client'ıyla aynı Faz 0
    kararı, 29.08.2026: geçmiş gerçek cevaplar öğrenme verisi)."""
    merchant_msgs = [c for c in item.get("conversations", []) if c.get("from") == "Merchant"]
    if merchant_msgs:
        return merchant_msgs[-1].get("content")
    return None


def fetch_questions(status, page_size=25):
    """Verilen status'teki TÜM soruları (tüm sayfalar) çeker ve Trendyol
    client'ıyla AYNI normalize şemada satırlar döner: [{question_id, sku,
    question_text, status, source_created_at, answer_text}].

    status burada HAM STRING olarak API'ye geçilir ve response'tan gelen
    status da OLDUĞU GİBİ döner ("WaitingForAnswer" vb, PascalCase) --
    Trendyol'un UPPER_SNAKE_CASE enum'uyla KARIŞTIRILMAMALI, çağıran taraf
    (marketplace-aware) bunu bilerek ele almalı.

    sku eksikse (product.sku API'den gelmezse) SESSİZCE atlanmaz, None
    olarak işaretlenir -- Trendyol client'ıyla aynı kural."""
    all_rows = []
    page = 1

    while True:
        params = {"page": page, "size": page_size, "status": status}
        data = hb_qna_get("/api/v1.0/issues", params=params)
        items = data.get("items", [])

        for item in items:
            all_rows.append({
                "question_id": str(item["issueNumber"]),
                "sku": (item.get("product") or {}).get("sku"),
                "question_text": _extract_question_text(item),
                "status": item.get("status", status),
                "source_created_at": item.get("createdAt"),
                "answer_text": _extract_answer_text(item),
            })

        if len(items) < page_size:
            break
        page += 1

    return all_rows


def send_answer(question_id, text):
    """Cevap kısıtı (CONFIRMED, resmi doküman): max 2000 karakter.
    Bu fonksiyon henüz hiçbir yerden çağrılmıyor (draft-only mimari,
    bkz. modül docstring'i)."""
    if len(text) > 2000:
        raise ValueError("Cevap metni en fazla 2000 karakter olabilir (HB servis kısıtı).")

    return hb_qna_post(
        f"/api/v1.0/issues/{question_id}/answer",
        data={"Answer": text},
    )
