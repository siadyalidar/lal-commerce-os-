"""
tests/test_hb_qna_client.py
------------------------------------
hb_qna_client.py'nin saf parse mantığını kapsar. hb_qna_get() (dolayısıyla
gerçek "Satıcıya Sor" API'si) her testte mock'lanır.

ÖNEMLİ (30.08.2026 CANLI ORTAM DOĞRULAMASI): resmi Developer Portal
dokümanı response şemasını "items"/"totalCount" gibi tarif ediyordu, ama
Sidar'ın canlı ortamda çalıştırdığı GERÇEK istek şunu döndü:
  {"data": [...], "currentPage": 1, "currentPageSize": 5,
   "totalPageCount": 0, "totalItemCount": 0, "nextPage": null,
   "previousPage": null}
Yani gerçek alan adları "data" + "currentPage"/"totalPageCount"/"nextPage"
-- "items" YOK. Bu testler CANLI DOĞRULANMIŞ şemayı esas alır, dokümanın
örnek şemasını DEĞİL.

Diğer alanlar (issueNumber, product.sku, conversations[], status vb.)
henüz canlı bir "dolu" response ile doğrulanmadı (test sırasında HB'de
bekleyen soru yoktu) -- bunlar hâlâ dokümana dayalı, UNVERIFIED olarak
işaretli kalıyor. İlk gerçek soru geldiğinde bu alanlar da teyit
edilmeli.
"""

from unittest.mock import patch

import pytest

from hb_qna_client import fetch_questions, send_answer


_SAMPLE_API_RESPONSE = {
    "currentPage": 1,
    "currentPageSize": 25,
    "totalPageCount": 1,
    "totalItemCount": 2,
    "nextPage": None,
    "previousPage": None,
    "data": [
        {
            "issueNumber": 5001,
            "createdAt": "2026-08-28T10:00:00Z",
            "status": "WaitingForAnswer",
            "expireDate": "2026-08-30T10:00:00Z",
            "product": {"sku": "SH-HOUSING-10-14", "name": "Su Arıtma Kabini"},
            "lastContent": "Bu üründen kaç adet var, hepsi aynı renk mi?",
            "conversations": [
                {
                    "id": 1,
                    "from": "Customer",
                    "content": "Bu üründen kaç adet var, hepsi aynı renk mi?",
                    "createdAt": "2026-08-28T10:00:00Z",
                }
            ],
        },
        {
            "issueNumber": 5002,
            "createdAt": "2026-08-27T09:00:00Z",
            "status": "Answered",
            "expireDate": "2026-08-29T09:00:00Z",
            "product": {"sku": "SH-HOUSING-10-14", "name": "Su Arıtma Kabini"},
            "lastContent": "Evet, 5 mikron filtre uyumludur.",
            "conversations": [
                {
                    "id": 2,
                    "from": "Customer",
                    "content": "5 mikron filtre bu üründe kullanılabilir mi?",
                    "createdAt": "2026-08-27T09:00:00Z",
                },
                {
                    "id": 3,
                    "from": "Merchant",
                    "content": "Evet, 5 mikron filtre uyumludur.",
                    "createdAt": "2026-08-27T11:00:00Z",
                },
            ],
        },
    ],
}


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_normalizes_rows(mock_get):
    mock_get.return_value = _SAMPLE_API_RESPONSE
    rows = fetch_questions(status="WaitingForAnswer")
    assert len(rows) == 2
    assert rows[0]["question_id"] == "5001"
    assert rows[0]["sku"] == "SH-HOUSING-10-14"
    assert rows[0]["question_text"] == "Bu üründen kaç adet var, hepsi aynı renk mi?"
    assert rows[0]["status"] == "WaitingForAnswer"
    assert rows[0]["answer_text"] is None


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_extracts_question_from_customer_conversation(mock_get):
    """question_text her zaman conversations[] içindeki from=Customer olan
    ilk mesajdan gelmeli -- lastContent Answered durumunda satıcının son
    mesajı olabilir, soruyla karıştırılmamalı."""
    mock_get.return_value = _SAMPLE_API_RESPONSE
    rows = fetch_questions(status="Answered")
    answered = [r for r in rows if r["question_id"] == "5002"][0]
    assert answered["question_text"] == "5 mikron filtre bu üründe kullanılabilir mi?"


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_extracts_merchant_answer_text(mock_get):
    """status=Answered sorularda conversations içindeki from=Merchant mesajı
    answer_text olarak normalize edilmeli -- AI'nin few-shot öğrenme kaynağı
    (Trendyol client'ıyla aynı Faz 0 kararı, 29.08.2026)."""
    mock_get.return_value = _SAMPLE_API_RESPONSE
    rows = fetch_questions(status="Answered")
    answered = [r for r in rows if r["question_id"] == "5002"][0]
    assert answered["answer_text"] == "Evet, 5 mikron filtre uyumludur."


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_passes_status_param(mock_get):
    mock_get.return_value = {"data": [], "currentPage": 1, "totalPageCount": 0, "nextPage": None}
    fetch_questions(status="WaitingForAnswer")
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["status"] == "WaitingForAnswer"


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_paginates_using_next_page_field(mock_get):
    """CANLI DOĞRULANDI (30.08.2026): response'ta totalPages YOK, ama
    nextPage/currentPage/totalPageCount VAR -- sayfalama bunlara göre
    yapılmalı (items sayısına bakan tahmin bazlı heuristiğe DEĞİL)."""
    page1 = {
        "data": [_SAMPLE_API_RESPONSE["data"][0]],
        "currentPage": 1, "totalPageCount": 2, "nextPage": 2,
    }
    page2 = {
        "data": [_SAMPLE_API_RESPONSE["data"][1]],
        "currentPage": 2, "totalPageCount": 2, "nextPage": None,
    }
    mock_get.side_effect = [page1, page2]
    rows = fetch_questions(status="WaitingForAnswer")
    assert len(rows) == 2
    assert mock_get.call_count == 2


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_stops_when_data_empty(mock_get):
    """30.08.2026 canlı ortamda bekleyen soru olmadığında gerçek response:
    data=[], totalPageCount=0, nextPage=null -- tek çağrıda durmalı,
    sonsuz döngüye girmemeli."""
    mock_get.return_value = {
        "data": [], "currentPage": 1, "currentPageSize": 5,
        "totalPageCount": 0, "totalItemCount": 0, "nextPage": None, "previousPage": None,
    }
    rows = fetch_questions(status="WaitingForAnswer")
    assert rows == []
    assert mock_get.call_count == 1


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_missing_sku_not_silently_dropped(mock_get):
    """product.sku eksikse sku None kalmalı, satır SESSİZCE atlanmamalı
    (no silent data absence prensibi, Trendyol client'ıyla aynı kural)."""
    broken_row = dict(_SAMPLE_API_RESPONSE["data"][0])
    broken_row["product"] = {}
    mock_get.return_value = {
        "data": [broken_row], "currentPage": 1, "totalPageCount": 1, "nextPage": None,
    }
    rows = fetch_questions(status="WaitingForAnswer")
    assert len(rows) == 1
    assert rows[0]["sku"] is None


@patch("hb_qna_client.hb_qna_post")
def test_send_answer_posts_multipart_answer_field(mock_post):
    mock_post.return_value = {"message": "ok"}
    send_answer(question_id="5001", text="Evet, hepsi aynı renk.")
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["data"]["Answer"] == "Evet, hepsi aynı renk."


def test_send_answer_rejects_too_long_text():
    """HB servis kısıtı: max 2000 karakter (CONFIRMED, resmi doküman).
    NOT: HB dokümanında Trendyol'daki gibi bir MIN karakter kısıtı
    belirtilmemiş -- bu yüzden burada min kontrolü YOK (UNVERIFIED alan
    icat edilmedi)."""
    with pytest.raises(ValueError):
        send_answer(question_id="5001", text="a" * 2001)

