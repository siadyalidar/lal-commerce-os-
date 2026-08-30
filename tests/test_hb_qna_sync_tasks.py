"""
tests/test_hb_qna_sync_tasks.py
-----------------------------------
qna_sync_tasks.sync_hepsiburada_questions Celery task'ının orkestrasyon
mantığını kapsar. test_qna_sync_tasks.py (Trendyol) ile AYNI senaryo seti
-- iki task da 30.08.2026'da eklenen ortak _run_qna_sync() yardımcısını
paylaşıyor, bu yüzden testler paralel yapıda.

hb_qna_client.fetch_questions ve qna_ai_engine.generate_draft_answer
mock'lanır -- ne gerçek HB API'sine ne de gerçek Ollama'ya istek atılır.
"""

from unittest.mock import patch

from database import get_draft_answer, list_customer_questions
from qna_sync_tasks import sync_hepsiburada_questions


_SAMPLE_HB_QUESTIONS = [
    {
        "question_id": "5001",
        "sku": "SH-HOUSING-10-14",
        "question_text": "Bu üründen kaç adet var, hepsi aynı renk mi?",
        "status": "WaitingForAnswer",
        "source_created_at": "2026-08-28T10:00:00Z",
        "answer_text": None,
    },
    {
        "question_id": "5002",
        "sku": "SH-HOUSING-10-14",
        "question_text": "Garanti süresi ne kadar?",
        "status": "WaitingForAnswer",
        "source_created_at": "2026-08-28T11:00:00Z",
        "answer_text": None,
    },
]


def _mock_draft(needs_clarification=False):
    if needs_clarification:
        return {
            "needs_clarification": True,
            "draft_text": None,
            "clarification_prompt": "Bu konuda bilgim yok.",
            "model_used": "gemma4:e4b",
        }
    return {
        "needs_clarification": False,
        "draft_text": "Test taslak cevap.",
        "clarification_prompt": None,
        "model_used": "gemma4:e4b",
    }


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_hb_questions")
def test_sync_upserts_hb_questions_and_generates_drafts(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    mock_fetch.return_value = _SAMPLE_HB_QUESTIONS
    mock_generate.return_value = _mock_draft()

    result = sync_hepsiburada_questions()

    questions = list_customer_questions(marketplace="hepsiburada")
    assert len(questions) == 2
    # HB status'u PascalCase olarak OLDUĞU GİBİ saklanmalı (Trendyol'un
    # UPPER_SNAKE_CASE'ine dönüştürülmemeli -- 30.08.2026 Faz 0 kararı)
    assert questions[0]["status"] == "WaitingForAnswer"

    draft = get_draft_answer(marketplace="hepsiburada", question_id="5001")
    assert draft is not None
    assert draft["draft_text"] == "Test taslak cevap."
    assert result["drafts_generated"] == 2
    assert result["failed"] == []


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_hb_questions")
def test_sync_respects_limit(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    mock_fetch.return_value = _SAMPLE_HB_QUESTIONS
    mock_generate.return_value = _mock_draft()

    result = sync_hepsiburada_questions(limit=1)

    assert result["drafts_generated"] == 1
    assert result["skipped_due_to_limit"] == 1


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_hb_questions")
def test_sync_skips_ai_generation_when_sku_missing(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    broken = dict(_SAMPLE_HB_QUESTIONS[0])
    broken["sku"] = None
    mock_fetch.return_value = [broken]

    result = sync_hepsiburada_questions()

    questions = list_customer_questions(marketplace="hepsiburada")
    assert len(questions) == 1
    mock_generate.assert_not_called()
    assert result["skipped_missing_sku"] == 1


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_hb_questions")
def test_sync_isolates_partial_failure(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    mock_fetch.return_value = _SAMPLE_HB_QUESTIONS
    mock_generate.side_effect = [Exception("Ollama bağlantı hatası"), _mock_draft()]

    result = sync_hepsiburada_questions()

    assert len(result["failed"]) == 1
    assert result["failed"][0]["question_id"] == "5001"
    assert result["drafts_generated"] == 1


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_hb_questions")
def test_sync_uses_own_lock_name_not_shared_with_others(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    """HB QnA sync kendi kilit adını kullanmalı -- mevcut 'trendyol',
    'hepsiburada' (sipariş sync), 'hepsiburada_reviews' VE 'trendyol_qna'
    kilitlerinden AYRI bir isim alanı, hiçbirini bloklamamalı."""
    mock_fetch.return_value = []
    sync_hepsiburada_questions()
    lock_name_used = mock_acquire.call_args.args[0] if mock_acquire.call_args.args else mock_acquire.call_args.kwargs.get("name")
    assert lock_name_used not in ("trendyol", "hepsiburada", "hepsiburada_reviews", "trendyol_qna")


@patch("qna_sync_tasks.acquire_sync_lock", return_value=None)
def test_sync_skips_if_lock_not_acquired(mock_acquire, db):
    result = sync_hepsiburada_questions()
    assert result["skipped"] is True
    assert list_customer_questions(marketplace="hepsiburada") == []


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_questions")
@patch("qna_sync_tasks.fetch_hb_questions")
def test_trendyol_and_hb_sync_do_not_mix_rows(mock_fetch_hb, mock_fetch_tr, mock_generate, mock_release, mock_acquire, db):
    """İki marketplace'in soruları aynı question_id'yi paylaşsa bile
    (bileşik anahtar: marketplace+question_id) birbirine karışmamalı --
    mevcut composite-key mimarisinin QnA'da da doğru çalıştığını kanıtlar."""
    from qna_sync_tasks import sync_trendyol_questions

    shared_id_tr = dict(_SAMPLE_HB_QUESTIONS[0])
    shared_id_tr["question_id"] = "9999"
    shared_id_hb = dict(_SAMPLE_HB_QUESTIONS[0])
    shared_id_hb["question_id"] = "9999"

    mock_fetch_tr.return_value = [shared_id_tr]
    mock_fetch_hb.return_value = [shared_id_hb]
    mock_generate.return_value = _mock_draft()

    sync_trendyol_questions()
    sync_hepsiburada_questions()

    assert len(list_customer_questions(marketplace="trendyol")) == 1
    assert len(list_customer_questions(marketplace="hepsiburada")) == 1
    assert get_draft_answer(marketplace="trendyol", question_id="9999") is not None
    assert get_draft_answer(marketplace="hepsiburada", question_id="9999") is not None
