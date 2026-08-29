"""
tests/test_qna_db.py
----------------------
Müşteri Soruları AI Asistanı özelliğinin DB katmanını kapsar:
  - customer_questions   (marketplace, question_id) composite PK
  - question_draft_answers (marketplace, question_id) composite PK
  - product_knowledge_facts (sku, fact_id) composite PK — sku bazlı,
    marketplace'ten bağımsız (bkz. product_images ile aynı sku kavramı).

Idempotent upsert davranışı (aynı kaydın tekrar upsert edilmesi ikinci
satır oluşturmamalı) ve temel list/get fonksiyonları test ediliyor.
"""

from database import (
    add_product_knowledge_fact,
    get_draft_answer,
    list_customer_questions,
    list_product_knowledge_facts,
    upsert_customer_questions,
    upsert_draft_answer,
)


def _seed_question(marketplace="trendyol", question_id="1001", sku="SH-8IN1-METER", status="WAITING_FOR_ANSWER"):
    upsert_customer_questions([{
        "marketplace": marketplace,
        "question_id": question_id,
        "sku": sku,
        "question_text": "Bu cihaz pil ile mi çalışıyor?",
        "status": status,
        "source_created_at": "2026-08-29T10:00:00",
    }])


def test_upsert_customer_question_creates_row(db):
    _seed_question()
    rows = list_customer_questions(marketplace="trendyol")
    assert len(rows) == 1
    assert rows[0]["question_id"] == "1001"
    assert rows[0]["sku"] == "SH-8IN1-METER"
    assert rows[0]["status"] == "WAITING_FOR_ANSWER"


def test_upsert_customer_question_is_idempotent(db):
    _seed_question(status="WAITING_FOR_ANSWER")
    _seed_question(status="ANSWERED")  # aynı (marketplace, question_id) — güncellenmeli, yeni satır değil
    rows = list_customer_questions(marketplace="trendyol")
    assert len(rows) == 1
    assert rows[0]["status"] == "ANSWERED"


def test_composite_pk_distinguishes_marketplaces(db):
    """Aynı question_id farklı marketplace'lerde ayrı satır olmalı
    (settlements/cargo_costs ile aynı marketplace-namespaced PK deseni)."""
    _seed_question(marketplace="trendyol", question_id="1001")
    _seed_question(marketplace="hepsiburada", question_id="1001")
    rows = list_customer_questions()
    assert len(rows) == 2


def test_upsert_draft_answer_and_get(db):
    _seed_question()
    upsert_draft_answer(
        marketplace="trendyol",
        question_id="1001",
        draft_text="Cihazımız pil ile çalışır, şarj edilebilir değildir.",
        needs_clarification=False,
        clarification_prompt=None,
        model_used="gemma4:e4b",
    )
    draft = get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft is not None
    assert draft["needs_clarification"] is False
    assert "pil ile çalışır" in draft["draft_text"]
    assert draft["sent"] is False


def test_upsert_draft_answer_needs_clarification_flag(db):
    _seed_question()
    upsert_draft_answer(
        marketplace="trendyol",
        question_id="1001",
        draft_text=None,
        needs_clarification=True,
        clarification_prompt="Garanti süresi bilgi tabanında yok, girer misiniz?",
        model_used="gemma4:e4b",
    )
    draft = get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft["needs_clarification"] is True
    assert draft["clarification_prompt"] == "Garanti süresi bilgi tabanında yok, girer misiniz?"


def test_upsert_draft_answer_is_idempotent(db):
    """Aynı soru için taslak yeniden üretilirse (örn. yeni fact eklendiğinde)
    eski taslağın üzerine yazılmalı, ikinci satır oluşmamalı."""
    _seed_question()
    upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="ilk taslak",
                         needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    upsert_draft_answer(marketplace="trendyol", question_id="1001", draft_text="güncellenmiş taslak",
                         needs_clarification=False, clarification_prompt=None, model_used="gemma4:e4b")
    draft = get_draft_answer(marketplace="trendyol", question_id="1001")
    assert draft["draft_text"] == "güncellenmiş taslak"


def test_add_product_knowledge_fact_and_list(db):
    add_product_knowledge_fact(
        sku="SH-8IN1-METER",
        topic="pil",
        fact_text="Kapak kısmından açılan bölmede 4 adet saat/düğme pili kullanılır, şarjlı değildir.",
        created_by="sidar",
    )
    facts = list_product_knowledge_facts(sku="SH-8IN1-METER")
    assert len(facts) == 1
    assert facts[0]["topic"] == "pil"
    assert "4 adet" in facts[0]["fact_text"]


def test_product_knowledge_facts_scoped_by_sku(db):
    add_product_knowledge_fact(sku="SH-8IN1-METER", topic="pil", fact_text="pil bilgisi", created_by="sidar")
    add_product_knowledge_fact(sku="SH-OTHER-SKU", topic="pil", fact_text="başka ürün pil bilgisi", created_by="sidar")
    facts = list_product_knowledge_facts(sku="SH-8IN1-METER")
    assert len(facts) == 1
    assert facts[0]["fact_text"] == "pil bilgisi"


def test_list_customer_questions_filters_by_status(db):
    _seed_question(question_id="1001", status="WAITING_FOR_ANSWER")
    _seed_question(question_id="1002", status="ANSWERED")
    waiting = list_customer_questions(marketplace="trendyol", status="WAITING_FOR_ANSWER")
    assert len(waiting) == 1
    assert waiting[0]["question_id"] == "1001"
