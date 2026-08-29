"""
tests/test_trendyol_qna_client.py
------------------------------------
trendyol_qna_client.py'nin saf parse mantığını kapsar. trendyol_get()
(dolayısıyla gerçek Trendyol API'si) her testte mock'lanır — bu client'ın
GET questionsFilter / POST createAnswer davranışı 29.08.2026 Faz 0
denetiminde resmi dokümantasyondan (CONFIRMED) doğrulandı:
https://developers.trendyol.com/docs/müşteri-sorularını-çekme
https://developers.trendyol.com/docs/müşteri-sorularını-cevaplama
"""

from unittest.mock import patch

import pytest

from trendyol_qna_client import fetch_questions, send_answer


_SAMPLE_API_RESPONSE = {
    "content": [
        {
            "id": 1001,
            "text": "Bu cihaz pil ile mi çalışıyor?",
            "customerId": 888888888,
            "creationDate": 1756000000000,
            "productMainId": "SH-8IN1-METER",
            "status": "WAITING_FOR_ANSWER",
            "answer": None,
        },
        {
            "id": 1002,
            "text": "Kutu içeriği neler?",
            "customerId": 777777777,
            "creationDate": 1755000000000,
            "productMainId": "SH-8IN1-METER",
            "status": "ANSWERED",
            "answer": {"id": 5, "text": "Kutuda cihaz ve kullanım kılavuzu bulunur.", "creationDate": 1755100000000},
        },
    ],
    "page": 0,
    "size": 20,
    "totalElements": 2,
    "totalPages": 1,
}


@patch("trendyol_qna_client.trendyol_get")
def test_fetch_questions_normalizes_rows(mock_get):
    mock_get.return_value = _SAMPLE_API_RESPONSE
    rows = fetch_questions(status="WAITING_FOR_ANSWER")
    assert len(rows) == 2
    assert rows[0]["question_id"] == "1001"
    assert rows[0]["sku"] == "SH-8IN1-METER"
    assert rows[0]["question_text"] == "Bu cihaz pil ile mi çalışıyor?"
    assert rows[0]["status"] == "WAITING_FOR_ANSWER"
    assert rows[0]["answer_text"] is None


@patch("trendyol_qna_client.trendyol_get")
def test_fetch_questions_includes_existing_answer_text(mock_get):
    """status=ANSWERED sorularda answer.text de normalize edilmeli --
    bu, AI'nin few-shot öğrenme kaynağı olarak kullanılacak (bkz. Faz 0
    kararı: geçmiş gerçek cevaplar öğrenme verisi)."""
    mock_get.return_value = _SAMPLE_API_RESPONSE
    rows = fetch_questions(status="ANSWERED")
    answered = [r for r in rows if r["question_id"] == "1002"][0]
    assert answered["answer_text"] == "Kutuda cihaz ve kullanım kılavuzu bulunur."


@patch("trendyol_qna_client.trendyol_get")
def test_fetch_questions_passes_status_param(mock_get):
    mock_get.return_value = {"content": [], "totalPages": 1}
    fetch_questions(status="WAITING_FOR_ANSWER")
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["status"] == "WAITING_FOR_ANSWER"


@patch("trendyol_qna_client.trendyol_get")
def test_fetch_questions_paginates_until_last_page(mock_get):
    page0 = {"content": [_SAMPLE_API_RESPONSE["content"][0]], "page": 0, "totalPages": 2}
    page1 = {"content": [_SAMPLE_API_RESPONSE["content"][1]], "page": 1, "totalPages": 2}
    mock_get.side_effect = [page0, page1]
    rows = fetch_questions(status="WAITING_FOR_ANSWER")
    assert len(rows) == 2
    assert mock_get.call_count == 2


@patch("trendyol_qna_client.trendyol_get")
def test_fetch_questions_missing_product_main_id_not_silently_dropped(mock_get):
    """productMainId eksikse sku None kalmalı, satır SESSİZCE atlanmamalı
    (no silent data absence prensibi) -- sync_task bu satırı loglayıp
    ayrıca ele almalı."""
    broken_row = dict(_SAMPLE_API_RESPONSE["content"][0])
    broken_row.pop("productMainId")
    mock_get.return_value = {"content": [broken_row], "page": 0, "totalPages": 1}
    rows = fetch_questions(status="WAITING_FOR_ANSWER")
    assert len(rows) == 1
    assert rows[0]["sku"] is None


@patch("trendyol_qna_client.trendyol_post")
def test_send_answer_posts_text_body(mock_post):
    mock_post.return_value = {"answerId": 42}
    result = send_answer(question_id="1001", text="Cihazımız pil ile çalışır.")
    assert result["answerId"] == 42
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json_body"] == {"text": "Cihazımız pil ile çalışır."}


def test_send_answer_rejects_too_short_text():
    """Trendyol servis kısıtı: min 10 karakter (CONFIRMED, resmi doküman)."""
    with pytest.raises(ValueError):
        send_answer(question_id="1001", text="kısa")


def test_send_answer_rejects_too_long_text():
    """Trendyol servis kısıtı: max 2000 karakter (CONFIRMED, resmi doküman)."""
    with pytest.raises(ValueError):
        send_answer(question_id="1001", text="a" * 2001)
