"""
tests/test_qna_clarification.py
-----------------------------------
qna_clarification.resolve_clarification -- Sidar'ın bir netleştirme
sorusuna VERDİĞİ CEVABI iki yere aynı anda işler:
  1) product_knowledge_facts'e kalıcı fact olarak ekler ("1 kere
     yanıtlarım, ilerde hatırlanır" -- 29.08.2026 kararı)
  2) o SKU için NEEDS_CLARIFICATION durumunda bekleyen TÜM soruların
     taslağını (sadece hedef soru değil) yeni fact'lerle YENİDEN üretir
     -- aynı ürün hakkında birden fazla bekleyen soru varsa hepsi tek
     seferde çözülür.

30.08.2026 gerçek veri testinde SH-HOUSING-10-14 için 2 farklı müşteri
sorusu aynı anda NEEDS_CLARIFICATION olarak bekliyordu -- bu senaryo
gerçek, uydurma değil.
"""

from unittest.mock import patch

from database import (
    add_product_knowledge_fact,
    get_draft_answer,
    list_product_knowledge_facts,
    mark_draft_answer_sent,
    upsert_customer_questions,
    upsert_draft_answer,
)
from qna_clarification import resolve_clarification


def _seed_pending_question(question_id, sku="SH-HOUSING-10-14", question_text="soru"):
    upsert_customer_questions([{
        "marketplace": "trendyol",
        "question_id": question_id,
        "sku": sku,
        "question_text": question_text,
        "status": "WAITING_FOR_ANSWER",
        "source_created_at": "2026-08-30T10:00:00",
    }])
    upsert_draft_answer(
        marketplace="trendyol", question_id=question_id, draft_text=None,
        needs_clarification=True, clarification_prompt="bilgim yok", model_used="gemma4:e4b",
    )


def _mock_answered_draft():
    return {
        "needs_clarification": False,
        "draft_text": "Evet, ürünümüz Tayvan üretimidir.",
        "clarification_prompt": None,
        "model_used": "gemma4:e4b",
    }


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_adds_fact_to_knowledge_base(mock_generate, db):
    _seed_pending_question("2001")
    mock_generate.return_value = _mock_answered_draft()

    resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[{"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."}],
        created_by="sidar",
    )

    facts = list_product_knowledge_facts(sku="SH-HOUSING-10-14")
    assert len(facts) == 1
    assert facts[0]["fact_text"] == "Tayvan üretimidir."


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_regenerates_pending_draft(mock_generate, db):
    _seed_pending_question("2001")
    mock_generate.return_value = _mock_answered_draft()

    resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[{"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."}],
        created_by="sidar",
    )

    draft = get_draft_answer(marketplace="trendyol", question_id="2001")
    assert draft["needs_clarification"] is False
    assert "Tayvan" in draft["draft_text"]


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_regenerates_all_pending_questions_same_sku(mock_generate, db):
    """30.08.2026 gerçek senaryo: aynı SKU için 2 farklı soru aynı anda
    bekliyordu -- ikisi de TEK bir fact eklenmesiyle çözülmeli."""
    _seed_pending_question("2001", question_text="Tayvan üretimi mi?")
    _seed_pending_question("2002", question_text="Menşei neresi?")
    mock_generate.return_value = _mock_answered_draft()

    result = resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[{"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."}],
        created_by="sidar",
    )

    assert result["regenerated"] == 2
    draft1 = get_draft_answer(marketplace="trendyol", question_id="2001")
    draft2 = get_draft_answer(marketplace="trendyol", question_id="2002")
    assert draft1["needs_clarification"] is False
    assert draft2["needs_clarification"] is False


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_does_not_touch_other_sku(mock_generate, db):
    _seed_pending_question("2001", sku="SH-HOUSING-10-14")
    _seed_pending_question("3001", sku="SH-8IN1-METER")
    mock_generate.return_value = _mock_answered_draft()

    resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[{"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."}],
        created_by="sidar",
    )

    other_draft = get_draft_answer(marketplace="trendyol", question_id="3001")
    assert other_draft["needs_clarification"] is True  # dokunulmadı
    mock_generate.assert_called_once()  # sadece SH-HOUSING-10-14 sorusu için çağrıldı


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_skips_already_sent_drafts(mock_generate, db):
    """Sidar zaten manuel onaylayıp göndermiş bir taslağa YENİDEN
    dokunulmamalı -- gönderilmiş bir cevabı sessizce değiştirmek
    tehlikeli olur (müşteriye giden şey ile DB'deki kayıt tutarsız kalır)."""
    _seed_pending_question("2001")
    mark_draft_answer_sent(marketplace="trendyol", question_id="2001")
    mock_generate.return_value = _mock_answered_draft()

    resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[{"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."}],
        created_by="sidar",
    )

    mock_generate.assert_not_called()


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_isolates_partial_failure(mock_generate, db):
    _seed_pending_question("2001")
    _seed_pending_question("2002")
    mock_generate.side_effect = [Exception("Ollama hatası"), _mock_answered_draft()]

    result = resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[{"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."}],
        created_by="sidar",
    )

    assert result["regenerated"] == 1
    assert len(result["failed"]) == 1
    # başarısız olan sorunun taslağı hâlâ needs_clarification=True olarak kalmalı
    remaining = get_draft_answer(marketplace="trendyol", question_id=result["failed"][0]["question_id"])
    assert remaining["needs_clarification"] is True


@patch("qna_clarification.generate_draft_answer")
def test_resolve_clarification_accepts_multiple_facts_at_once(mock_generate, db):
    """30.08.2026 gerçek senaryo: Sidar tek seferde 3 ayrı konuda (üretim
    yeri, kabartma, nipel/dirsek dahil olma) bilgi veriyor -- hepsi tek
    çağrıda kaydedilebilmeli."""
    _seed_pending_question("2001")
    mock_generate.return_value = _mock_answered_draft()

    resolve_clarification(
        sku="SH-HOUSING-10-14",
        facts=[
            {"topic": "üretim_yeri", "fact_text": "Tayvan üretimidir."},
            {"topic": "yüzey", "fact_text": "Plastik üzerinde kabartma/logo baskısı yoktur."},
            {"topic": "set_icerigi", "fact_text": "3'lü alımlarda normalde sadece dirsek dahildir, ara nipel dahil değildir; müşteri özel talep ederse eklenebilir."},
        ],
        created_by="sidar",
    )

    facts = list_product_knowledge_facts(sku="SH-HOUSING-10-14")
    assert len(facts) == 3
