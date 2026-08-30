"""
tests/test_qna_sync_tasks.py
-------------------------------
qna_sync_tasks.sync_trendyol_questions Celery task'ının orkestrasyon
mantığını kapsar. trendyol_qna_client.fetch_questions ve
qna_ai_engine.generate_draft_answer mock'lanır -- ne gerçek Trendyol
API'sine ne de gerçek Ollama'ya istek atılır (Ollama sadece Sidar'ın
Mac'inde çalışıyor, CI/test ortamında yok).

hb_review_sync_tasks.py ile AYNI desen: discover + sync, sync_lock ile
paralel çalışmayı önleme, `limit` ile kontrollü rollout, kısmi başarısızlık
izolasyonu (bir sorunun draft üretimi patlarsa diğerlerini engellemez).
"""

from unittest.mock import patch

from database import get_draft_answer, list_customer_questions
from qna_sync_tasks import sync_trendyol_questions


_SAMPLE_QUESTIONS = [
    {
        "question_id": "1001",
        "sku": "SH-8IN1-METER",
        "question_text": "Pil ömrü ne kadar?",
        "status": "WAITING_FOR_ANSWER",
        "source_created_at": 1756000000000,
        "answer_text": None,
    },
    {
        "question_id": "1002",
        "sku": "SH-8IN1-METER",
        "question_text": "Garanti süresi ne kadar?",
        "status": "WAITING_FOR_ANSWER",
        "source_created_at": 1756000100000,
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
@patch("qna_sync_tasks.fetch_questions")
def test_sync_upserts_questions_and_generates_drafts(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    mock_fetch.return_value = _SAMPLE_QUESTIONS
    mock_generate.return_value = _mock_draft()

    result = sync_trendyol_questions()

    questions = list_customer_questions(marketplace="trendyol")
    assert len(questions) == 2

    draft = get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft is not None
    assert draft["draft_text"] == "Test taslak cevap."
    assert result["drafts_generated"] == 2
    assert result["failed"] == []


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_questions")
def test_sync_skips_existing_draft_does_not_regenerate(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    """Zaten taslağı olan bir soru için AI TEKRAR ÇAĞRILMAMALI (gereksiz
    Ollama compute'u israf etmemek için) -- 29.08.2026 kararı: yeniden
    üretim ileride manuel bir tetikleyiciyle olacak, otomatik değil."""
    mock_fetch.return_value = [_SAMPLE_QUESTIONS[0]]
    mock_generate.return_value = _mock_draft()

    sync_trendyol_questions()
    assert mock_generate.call_count == 1

    sync_trendyol_questions()
    assert mock_generate.call_count == 1  # ikinci çalıştırmada artmadı


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_questions")
def test_sync_respects_limit(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    mock_fetch.return_value = _SAMPLE_QUESTIONS
    mock_generate.return_value = _mock_draft()

    result = sync_trendyol_questions(limit=1)

    assert result["drafts_generated"] == 1
    assert result["skipped_due_to_limit"] == 1


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_questions")
def test_sync_skips_ai_generation_when_sku_missing(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    """sku=None ise (Trendyol productMainId döndürmediyse) soru YİNE DE
    customer_questions'a upsert edilir (panelde görünür, manuel triage
    için) ama AI çağrılmaz -- hangi ürüne ait olduğunu bilmeden fact
    araması yapılamaz, sessizce yanlış/eşleşmeyen fact kullanmaktansa
    hiç draft üretmemek daha güvenli."""
    broken = dict(_SAMPLE_QUESTIONS[0])
    broken["sku"] = None
    mock_fetch.return_value = [broken]

    result = sync_trendyol_questions()

    questions = list_customer_questions(marketplace="trendyol")
    assert len(questions) == 1
    mock_generate.assert_not_called()
    draft = get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft is None
    assert result["skipped_missing_sku"] == 1


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_questions")
def test_sync_isolates_partial_failure(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    """Bir sorunun AI çağrısı patlarsa (örn. Ollama o an ayakta değil),
    diğer sorunun işlenmesi ENGELLENMEMELİ -- kısmi başarısızlık izolasyonu
    (hb_review_sync_tasks.py'deki _sync_one_sku ile aynı prensip)."""
    mock_fetch.return_value = _SAMPLE_QUESTIONS
    mock_generate.side_effect = [Exception("Ollama bağlantı hatası"), _mock_draft()]

    result = sync_trendyol_questions()

    assert len(result["failed"]) == 1
    assert result["failed"][0]["question_id"] == "1001"
    assert result["drafts_generated"] == 1
    # ikinci soru için draft başarıyla üretilmiş olmalı
    draft2 = get_draft_answer(marketplace="trendyol", question_id="1002")
    assert draft2 is not None


@patch("qna_sync_tasks.acquire_sync_lock", return_value=None)
def test_sync_skips_if_lock_not_acquired(mock_acquire, db):
    """Zaten devam eden bir sync varsa (kilit alınamazsa) task hemen
    atlanmalı, hiçbir şey işlenmemeli."""
    result = sync_trendyol_questions()
    assert result["skipped"] is True
    assert list_customer_questions() == []


@patch("qna_sync_tasks.acquire_sync_lock", return_value="test-token")
@patch("qna_sync_tasks.release_sync_lock")
@patch("qna_sync_tasks.generate_draft_answer")
@patch("qna_sync_tasks.fetch_questions")
def test_sync_uses_qna_lock_name_not_shared_with_order_sync(mock_fetch, mock_generate, mock_release, mock_acquire, db):
    """hepsiburada_reviews kilidiyle AYNI isim alanı deseni: qna sync'i
    kendi kilit adını kullanmalı, mevcut 'trendyol'/'hepsiburada' sipariş
    sync kilitleriyle birbirini bloklamamalı."""
    mock_fetch.return_value = []
    sync_trendyol_questions()
    lock_name_used = mock_acquire.call_args.args[0] if mock_acquire.call_args.args else mock_acquire.call_args.kwargs.get("name")
    assert lock_name_used not in ("trendyol", "hepsiburada")
