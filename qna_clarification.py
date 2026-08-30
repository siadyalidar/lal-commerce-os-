"""
qna_clarification.py
-----------------------
Sidar'ın bir NEEDS_CLARIFICATION taslağına verdiği cevabı işleyen modül.
29.08.2026 kararı: "1 kere yanıtlarım, ilerde ben hatırlayacağım" -- yani
bir netleştirme cevabı SADECE o anki soruyu çözmekle kalmaz, aynı SKU
için bekleyen TÜM diğer NEEDS_CLARIFICATION sorularını da (varsa)
otomatik olarak yeniden çözer, ve gelecekte gelecek sorular için de
kalıcı bir fact olarak product_knowledge_facts'te kalır.

30.08.2026 GERÇEK VERİ DOĞRULAMASI: SH-HOUSING-10-14 için canlı Trendyol
hesabından 2 farklı müşteri sorusu (2001, 2002 örnek ID'leri değil,
gerçek soru ID'leri) aynı anda NEEDS_CLARIFICATION olarak bekliyordu.
Bu modül tam olarak bu senaryoyu -- tek bir fact girişiyle ikisini birden
çözmeyi -- kapsıyor.

TASARIM KARARI -- neden "sent" olan taslaklara dokunulmuyor: Sidar zaten
manuel onaylayıp müşteriye göndermiş bir cevabı bu fonksiyon SESSİZCE
değiştirmemeli. Gönderilmiş bir cevap ile yerel DB kaydı arasında tutarsızlık
oluşmasın diye, mark_draft_answer_sent() çağrılmış sorular regenerate
listesinden otomatik hariç tutulur.
"""

import logging

from database import (
    add_product_knowledge_fact,
    get_draft_answer,
    list_customer_questions,
    list_product_knowledge_facts,
    upsert_draft_answer,
)
from qna_ai_engine import generate_draft_answer

logger = logging.getLogger(__name__)


def resolve_clarification(sku, facts, created_by):
    """facts: [{"topic": ..., "fact_text": ...}, ...] -- Sidar'ın bir
    veya birden fazla netleştirme sorusuna verdiği cevap(lar).

    Adımlar:
      1) Her fact product_knowledge_facts'e kalıcı olarak eklenir.
      2) Bu sku için WAITING_FOR_ANSWER + needs_clarification=True +
         henüz gönderilmemiş (sent=0) TÜM sorular bulunur.
      3) Her biri için (artık yeni fact'leri de içeren) güncel fact
         listesiyle qna_ai_engine.generate_draft_answer tekrar çağrılır,
         sonuç question_draft_answers'a upsert edilir.

    Bir sorunun yeniden üretimi başarısız olursa (örn. Ollama o an ayakta
    değil) diğerlerinin işlenmesi ENGELLENMEZ -- kısmi başarısızlık
    izolasyonu, qna_sync_tasks.py ile aynı prensip. Başarısız olan sorunun
    taslağı needs_clarification=True olarak KALIR (bir sonraki manuel
    tetiklemede tekrar denenebilir)."""
    for f in facts:
        add_product_knowledge_fact(sku=sku, topic=f["topic"], fact_text=f["fact_text"], created_by=created_by)

    all_facts = list_product_knowledge_facts(sku=sku)

    pending_questions = [
        q for q in list_customer_questions(sku=sku)
        if q["status"] == "WAITING_FOR_ANSWER"
    ]

    result = {"regenerated": 0, "failed": []}

    for q in pending_questions:
        existing_draft = get_draft_answer(marketplace=q["marketplace"], question_id=q["question_id"])
        if existing_draft is None:
            continue
        if existing_draft["sent"]:
            continue
        if not existing_draft["needs_clarification"]:
            continue

        try:
            draft = generate_draft_answer(sku=sku, question_text=q["question_text"], facts=all_facts)
        except Exception as exc:
            logger.warning(
                f"[QnA Clarification] question_id={q['question_id']} (sku={sku}): "
                f"yeniden üretim başarısız: {exc}"
            )
            result["failed"].append({"question_id": q["question_id"], "error": str(exc)})
            continue

        upsert_draft_answer(
            marketplace=q["marketplace"],
            question_id=q["question_id"],
            draft_text=draft["draft_text"],
            needs_clarification=draft["needs_clarification"],
            clarification_prompt=draft["clarification_prompt"],
            model_used=draft["model_used"],
        )
        result["regenerated"] += 1

    return result
