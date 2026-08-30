"""
tests/test_hb_qna_client.py
------------------------------------
hb_qna_client.py'nin saf parse mantığını kapsar. hb_qna_get() (dolayısıyla
gerçek "Satıcıya Sor" API'si) her testte mock'lanır -- bu client'ın
GET issues / POST issues/{number}/answer davranışı 30.08.2026 Faz 0
denetiminde resmi Hepsiburada Developer Portal dokümantasyonundan
(CONFIRMED) doğrulandı:
https://developers.hepsiburada.com/tr -> Satıcıya Sor Entegrasyonu

ÖNEMLİ FARK (Trendyol'a göre): HB status değerleri PascalCase
("WaitingForAnswer", "Answered", "Rejected", "AutoClosed") -- Trendyol'un
UPPER_SNAKE_CASE ("WAITING_FOR_ANSWER") formatından farklı. Bu client
ham status string'ini OLDUĞU GİBİ döner, normalize etmez -- çağıran taraf
(marketplace-aware sync_task/routes) bunu bilerek ele almalı.

CONFIRMED: cevaplama için 1 iş günü süre sınırı var, süre dolan sorular
otomatik AutoClosed'a düşüyor (Trendyol'da böyle bir kısıt yok).
"""

from unittest.mock import patch

import pytest

from hb_qna_client import fetch_questions, send_answer


_SAMPLE_API_RESPONSE = {
    "totalCount": 2,
    "items": [
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
    mock_get.return_value = {"items": [], "totalCount": 0}
    fetch_questions(status="WaitingForAnswer")
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["status"] == "WaitingForAnswer"


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_paginates_until_empty_page(mock_get):
    """CONFIRMED doküman page/size döndürüyor ama toplam sayfa sayısı
    dönmüyor -- boş bir sayfa gelene kadar (veya items < size olana kadar)
    sayfalamaya devam etmek gerekiyor (Trendyol'daki totalPages deseninden
    FARKLI, bu yüzden ayrı test)."""
    page0_items = [_SAMPLE_API_RESPONSE["items"][0]] * 25  # size=25 dolu sayfa
    page1_items = [_SAMPLE_API_RESPONSE["items"][1]]  # yarım sayfa -> son sayfa
    mock_get.side_effect = [
        {"items": page0_items, "totalCount": 26},
        {"items": page1_items, "totalCount": 26},
    ]
    rows = fetch_questions(status="WaitingForAnswer")
    assert len(rows) == 26
    assert mock_get.call_count == 2


@patch("hb_qna_client.hb_qna_get")
def test_fetch_questions_missing_sku_not_silently_dropped(mock_get):
    """product.sku eksikse sku None kalmalı, satır SESSİZCE atlanmamalı
    (no silent data absence prensibi, Trendyol client'ıyla aynı kural)."""
    broken_row = dict(_SAMPLE_API_RESPONSE["items"][0])
    broken_row["product"] = {}
    mock_get.return_value = {"items": [broken_row], "totalCount": 1}
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
