"""
trendyol_qna_client.py
------------------------
Trendyol Soru-Cevap Entegrasyonu client'ı. trendyol_client.py'deki
trendyol_get() (retry/throttle/backoff mantığı) ve aynı auth/base URL
kurulumu yeniden kullanılır -- ayrı bir HTTP katmanı icat edilmedi.

CONFIRMED (29.08.2026 Faz 0, resmi Trendyol dokümantasyonundan):
  GET  https://apigw.trendyol.com/integration/qna/sellers/{sellerId}/questions/filter
       Parametreler: barcode, page, size (max 50), startDate/endDate
       (epoch ms, max 2 hafta aralık), status, orderByField, orderByDirection.
       status: WAITING_FOR_ANSWER, ANSWERED, REPORTED, REJECTED, UNANSWERED.
       Sayfalama page=0'dan başlar.
       https://developers.trendyol.com/docs/müşteri-sorularını-çekme

  POST https://apigw.trendyol.com/integration/qna/sellers/{sellerId}/questions/{id}/answers
       Body: {"text": "..."}. Cevap 10-2000 karakter arası olmalı, yasaklı
       kelime kontrolünden geçiyor. Sadece WAITING_FOR_ANSWER statüsündeki
       sorular cevaplanabilir.
       https://developers.trendyol.com/docs/müşteri-sorularını-cevaplama

DRAFT-ONLY MİMARİ (29.08.2026 kararı): send_answer() burada TANIMLI ama
qna_sync_tasks.py veya başka hiçbir yerden OTOMATİK ÇAĞRILMIYOR. Sidar
panelden taslağı manuel onaylayıp göndermek istediğinde, ilerideki bir
Faz'da bir buton bu fonksiyona bağlanacak -- şu an için sadece hazır
duruyor, test edildi, ama devrede değil.
"""

import requests

import trendyol_client as _tc
from trendyol_client import BASE_URL, SUPPLIER_ID, trendyol_get


def trendyol_post(path, json_body):
    """trendyol_get ile AYNI auth/User-Agent kurulumunu POST için uygular.
    http_client.get_json_with_retry GET'e özel (POST body desteklemiyor),
    bu yüzden burada minimal, ayrı bir POST -- ama aynı kimlik bilgileri."""
    resp = requests.post(
        f"{BASE_URL}{path}", json=json_body,
        headers={"User-Agent": _tc.USER_AGENT},
        auth=(_tc.API_KEY, _tc.API_SECRET),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_questions(status, barcode=None, page_size=50):
    """Verilen status'teki TÜM soruları (tüm sayfalar) çeker ve normalize
    edilmiş satırlar döner: [{question_id, sku, question_text, status,
    source_created_at, answer_text}].

    answer_text: status=ANSWERED sorularda geçmiş cevabınızı taşır --
    bu, qna_ai_engine'in few-shot öğrenme kaynağı (29.08.2026 kararı:
    "geçmişte platformlarda verilmiş gerçek cevaplar").

    sku eksikse (productMainId API'den gelmezse) SESSİZCE atlanmaz,
    None olarak işaretlenir -- çağıran taraf (sync_task) bunu loglamalı."""
    all_rows = []
    page = 0
    total_pages = 1

    while page < total_pages:
        params = {"page": page, "size": page_size, "status": status}
        if barcode:
            params["barcode"] = barcode

        data = trendyol_get(
            f"/integration/qna/sellers/{SUPPLIER_ID}/questions/filter",
            params=params,
        )

        for item in data.get("content", []):
            answer = item.get("answer")
            all_rows.append({
                "question_id": str(item["id"]),
                "sku": item.get("productMainId"),
                "question_text": item["text"],
                "status": item.get("status", status),
                "source_created_at": item.get("creationDate"),
                "answer_text": answer["text"] if answer else None,
            })

        total_pages = data.get("totalPages", 1)
        page += 1

    return all_rows


def send_answer(question_id, text):
    """Cevap kısıtları (CONFIRMED, resmi doküman): 10-2000 karakter arası.
    Bu fonksiyon henüz hiçbir yerden çağrılmıyor (draft-only mimari,
    bkz. modül docstring'i)."""
    if len(text) < 10:
        raise ValueError("Cevap metni en az 10 karakter olmalı (Trendyol servis kısıtı).")
    if len(text) > 2000:
        raise ValueError("Cevap metni en fazla 2000 karakter olabilir (Trendyol servis kısıtı).")

    return trendyol_post(
        f"/integration/qna/sellers/{SUPPLIER_ID}/questions/{question_id}/answers",
        json_body={"text": text},
    )
