"""
qna_sync_tasks.py
--------------------
Trendyol müşteri sorularını senkronize eden ve her yeni soru için AI
taslak cevabı üreten Celery task'ı. hb_review_sync_tasks.py ile AYNI
orkestrasyon deseni: mantık doğrudan task fonksiyonunun içinde, client
(trendyol_qna_client.py), AI motor (qna_ai_engine.py) ve DB (database.py)
katmanları saf/atomik fonksiyonlar olarak kalıyor.

AKIŞ:
  1) status=WAITING_FOR_ANSWER tüm sorular Trendyol'dan çekilir, hepsi
     customer_questions'a upsert edilir (sku eksik olsa bile -- panelde
     görünür olsun, manuel triage mümkün olsun).
  2) sku'su OLAN ve henüz taslağı OLMAYAN her soru için:
     a) o sku'ya ait product_knowledge_facts çekilir
     b) qna_ai_engine.generate_draft_answer çağrılır (yerel Ollama)
     c) sonuç question_draft_answers'a upsert edilir
  3) sku eksikse (Trendyol productMainId döndürmediyse) AI HİÇ ÇAĞRILMAZ
     -- hangi ürüne ait olduğu bilinmeden fact araması güvenilir değil,
     sessizce yanlış eşleşen fact kullanmaktansa hiç taslak üretmemek
     tercih edildi (bkz. 29.08.2026 Faz 0 kararı).
  4) Zaten taslağı OLAN bir soru için AI TEKRAR ÇAĞRILMAZ -- gereksiz
     Ollama compute'u israf etmemek için. Yeniden üretim (örn. yeni bir
     fact eklendiğinde) ileride manuel bir tetikleyiciyle olacak
     (henüz bu Faz'da yok).

PRODUCTION SAFETY:
  - sync_lock.py ile "trendyol_qna" adıyla kilit alınır -- mevcut
    "trendyol"/"hepsiburada" (sipariş sync) ve "hepsiburada_reviews"
    kilitlerinden AYRI bir isim alanı, birbirini bloklamaz.
  - `limit` parametresi: bu çalıştırmada AI ile taslak üretilecek TOPLAM
    soru sayısını sınırlar (soru senkronizasyonunun kendisini değil --
    sorular her zaman tam çekilir, sadece AI üretimi limitlenir, çünkü
    Ollama çağrısı pahalı/yavaş kısım). limit=None (varsayılan) sınırsız.
    Kontrollü ilk rollout için limit=3-5 gibi küçük bir değer verilip
    sonuç doğrulanmalı (hb_review_sync ile AYNI kontrollü rollout deseni).
  - Bir sorunun AI çağrısı patlarsa (örn. Ollama o an ayakta değil), o soru
    "failed" listesine eklenir ama diğer soruların işlenmesi ENGELLENMEZ
    (kısmi başarısızlık izolasyonu, hb_review_sync_tasks._sync_one_sku ile
    aynı prensip). Başarısız sorular bir sonraki çalıştırmada (draft'ları
    hâlâ yok olduğu için) otomatik tekrar denenir.

⚠️ Bu task henüz Beat'e (celery_app.py beat_schedule) OTOMATİK/AKTİF
EKLENMEDİ -- HB review sync ile AYNI kural: kontrollü ilk rollout (limit=3-5
ile manuel tetikleme) onaylanıp sonucu doğrulanmadan Beat'e bağlanmamalı.

⚠️ Hepsiburada "Satıcıya Sor" tarafı bu task'ta YOK -- endpoint şeması
hâlâ UNVERIFIED (bkz. 29.08.2026 Faz 0 notları), HB entegrasyonu ayrı bir
sonraki fazda.
"""

import logging
import time

import database
from celery_app import celery_app
from qna_ai_engine import generate_draft_answer
from sync_lock import acquire_sync_lock, release_sync_lock
from trendyol_qna_client import fetch_questions

logger = logging.getLogger(__name__)

_LOCK_NAME = "trendyol_qna"


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300, name="qna_sync_tasks.sync_trendyol_questions")
def sync_trendyol_questions(self=None, limit=None):
    """Trendyol WAITING_FOR_ANSWER sorularını senkronize eder ve eksik
    taslakları AI ile üretir. Dönen dict, sonucun ve varsa kısmi
    başarısızlıkların açık bir özeti -- sync'in "sessizce başarılı
    görünmesi" istenmiyor."""
    lock_token = acquire_sync_lock(_LOCK_NAME)
    if lock_token is None:
        logger.info(
            "[QnA Sync] atlandı -- zaten devam eden bir Trendyol QnA "
            "senkronizasyonu var (kilit alınamadı)"
        )
        return {"skipped": True, "reason": "zaten devam eden bir senkronizasyon var"}

    start_time = time.time()
    stats = {
        "questions_synced": 0,
        "drafts_generated": 0,
        "skipped_missing_sku": 0,
        "skipped_already_drafted": 0,
        "skipped_due_to_limit": 0,
        "failed": [],
        "duration_seconds": 0.0,
    }

    try:
        questions = fetch_questions(status="WAITING_FOR_ANSWER")
        stats["questions_synced"] = len(questions)

        rows = [{
            "marketplace": "trendyol",
            "question_id": q["question_id"],
            "sku": q["sku"],
            "question_text": q["question_text"],
            "status": q["status"],
            "source_created_at": q["source_created_at"],
        } for q in questions]
        database.upsert_customer_questions(rows)

        budget = limit

        for q in questions:
            if q["sku"] is None:
                stats["skipped_missing_sku"] += 1
                logger.warning(
                    f"[QnA Sync] question_id={q['question_id']}: sku eksik "
                    f"(Trendyol productMainId döndürmedi), AI taslağı ÜRETİLMEDİ, "
                    f"manuel triage gerekiyor."
                )
                continue

            existing_draft = database.get_draft_answer(marketplace="trendyol", question_id=q["question_id"])
            if existing_draft is not None:
                stats["skipped_already_drafted"] += 1
                continue

            if budget is not None and budget <= 0:
                stats["skipped_due_to_limit"] += 1
                continue

            facts = database.list_product_knowledge_facts(sku=q["sku"])
            try:
                draft = generate_draft_answer(sku=q["sku"], question_text=q["question_text"], facts=facts)
            except Exception as exc:
                logger.warning(
                    f"[QnA Sync] question_id={q['question_id']} (sku={q['sku']}): "
                    f"AI taslak üretimi başarısız: {exc}"
                )
                stats["failed"].append({"question_id": q["question_id"], "sku": q["sku"], "error": str(exc)})
                if budget is not None:
                    budget -= 1
                continue

            database.upsert_draft_answer(
                marketplace="trendyol",
                question_id=q["question_id"],
                draft_text=draft["draft_text"],
                needs_clarification=draft["needs_clarification"],
                clarification_prompt=draft["clarification_prompt"],
                model_used=draft["model_used"],
            )
            stats["drafts_generated"] += 1
            if budget is not None:
                budget -= 1

        stats["duration_seconds"] = round(time.time() - start_time, 2)
        return stats
    finally:
        release_sync_lock(lock_token, _LOCK_NAME)
