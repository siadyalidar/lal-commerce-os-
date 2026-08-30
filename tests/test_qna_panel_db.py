"""
tests/test_qna_panel_db.py
-----------------------------
Panel için gereken iki DB fonksiyonunu kapsar:
  - list_questions_with_drafts: soru + taslak LEFT JOIN, gönderilmişleri
    varsayılan olarak dışlar
  - finalize_draft_answer: Sidar'ın panelde düzenlediği son metni kaydeder
    + sent=1 yapar (tek adımda)
"""

from database import (
    finalize_draft_answer,
    get_draft_answer,
    list_questions_with_drafts,
    upsert_customer_questions,
    upsert_draft_answer,
)


def _seed(question_id="1001", sku="SH-8IN1-METER"):
    upsert_customer_questions([{
        "marketplace": "trendyol", "question_id": question_id, "sku": sku,
        "question_text": "Pil ömrü ne kadar?", "status": "WAITING_FOR_ANSWER",
        "source_created_at": "2026-08-30T10:00:00",
    }])


def test_list_questions_with_drafts_includes_question_without_draft(db):
    _seed()
    rows = list_questions_with_drafts(marketplace="trendyol")
    assert len(rows) == 1
    assert rows[0]["question_id"] == "1001"
    assert rows[0]["draft_text"] is None
    assert rows[0]["needs_clarification"] is None


def test_list_questions_with_drafts_joins_draft_fields(db):
    _seed()
    upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="taslak metni",
                         needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    rows = list_questions_with_drafts(marketplace="trendyol")
    assert rows[0]["draft_text"] == "taslak metni"
    assert rows[0]["needs_clarification"] is False


def test_list_questions_with_drafts_excludes_sent_by_default(db):
    _seed()
    upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="taslak",
                         needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    finalize_draft_answer(marketplace="trendyol", question_id="1001", final_text="gönderilen metin")
    rows = list_questions_with_drafts(marketplace="trendyol")
    assert rows == []


def test_list_questions_with_drafts_include_sent_true_shows_everything(db):
    _seed()
    upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="taslak",
                         needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    finalize_draft_answer(marketplace="trendyol", question_id="1001", final_text="gönderilen metin")
    rows = list_questions_with_drafts(marketplace="trendyol", include_sent=True)
    assert len(rows) == 1
    assert rows[0]["sent"] is True


def test_finalize_draft_answer_saves_edited_text_and_marks_sent(db):
    _seed()
    upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="ilk taslak",
                         needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    finalize_draft_answer(marketplace="trendyol", question_id="1001", final_text="Sidar'ın düzenlediği son metin")
    draft = get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft["draft_text"] == "Sidar'ın düzenlediği son metin"
    assert draft["sent"] is True
    assert draft["sent_at"] is not None
