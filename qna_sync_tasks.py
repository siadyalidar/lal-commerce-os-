"""
qna_sync_tasks.py
--------------------
Trendyol VE Hepsiburada müşteri sorularını senkronize eden ve her yeni
soru için AI taslak cevabı üreten Celery task'ları. hb_review_sync_tasks.py
ile AYNI orkestrasyon deseni: mantık ortak bir yardımcıda (_run_qna_sync),
client (trendyol_qna_client.py / hb_qna_client.py), AI motor
(qna_ai_engine.py) ve DB (database.py) katmanları saf/atomik fonksiyonlar
olarak kalıyor.

AKIŞ (her iki marketplace için AYNI):
  1) "bekleyen" statüdeki tüm sorular çekilir, hepsi customer_questions'a
     upsert edilir (sku eksik olsa bile -- panelde görünür olsun, manuel
     triage mümkün olsun).
  2) sku'su OLAN ve henüz taslağı OLMAYAN her soru için:
     a) o sku'ya ait product_knowledge_facts çekilir
     b) qna_ai_engine.generate_draft_answer çağrılır (yerel Ollama)
     c) sonuç question_draft_answers'a upsert edilir
  3) sku eksikse AI HİÇ ÇAĞRILMAZ -- hangi ürüne ait olduğu bilinmeden
     fact araması güvenilir değil, sessizce yanlış eşleşen fact
     kullanmaktansa hiç taslak üretmemek tercih edildi (29.08.2026 Faz 0).
  4) Zaten taslağı OLAN bir soru için AI TEKRAR ÇAĞRILMAZ.

MARKETPLACE FARKLARI (30.08.2026 Faz 0, HB entegrasyonu):
  - Trendyol status: "WAITING_FOR_ANSWER" (UPPER_SNAKE_CASE)
  - HB status: "WaitingForAnswer" (PascalCase)
  Bu client'lardan (fetch_questions / fetch_hb_questions) OLDUĞU GİBİ
  gelir, _run_qna_sync BUNU NORMALİZE ETMEZ -- customer_questions.status
  kolonunda marketplace'e özgü ham string saklanır. Panel/route
  katmanı (qna_routes.py) bunu status'a göre DEĞİL sadece "taslağı var mı/
  sent mi" bilgisine göre filtreliyor, bu yüzden risk yok (bkz. Faz 2
  audit notu).

  - HB'de cevaplama için 1 iş günü süre sınırı var (expireDate,
    süresi geçen soru otomatik AutoClosed olur) -- Trendyol'da bu kısıt
    yok. Bu yüzden HB sync'i Trendyol'dan daha sık çalışmalı (bkz.
    celery_app.py Beat kaydı, HB için Faz 2'de eklenecek).

PRODUCTION SAFETY:
  - Her marketplace kendi sync_lock adını kullanır ("trendyol_qna" /
    "hepsiburada_qna") -- mevcut "trendyol"/"hepsiburada" (sipariş sync)
    ve "hepsiburada_reviews" kilitlerinden AYRI isim alanları, birbirini
    bloklamaz.
  - `limit` parametresi: bu çalıştırmada AI ile taslak üretilecek TOPLAM
    soru sayısını sınırlar (soru senkronizasyonunun kendisini değil).
    limit=None (varsayılan) sınırsız. Kontrollü ilk rollout için
    limit=3-5 gibi küçük bir değer verilip sonuç doğrulanmalı.
  - Bir sorunun AI çağrısı patlarsa, o soru "failed" listesine eklenir
    ama diğer soruların işlenmesi ENGELLENMEZ (kısmi başarısızlık
    izolasyonu). Başarısız sorular bir sonraki çalıştırmada otomatik
    tekrar denenir.

⚠️ sync_hepsiburada_questions henüz Beat'e (celery_app.py beat_schedule)
OTOMATİK/AKTİF EKLENMEDİ -- Trendyol QnA ile AYNI kural: kontrollü ilk
rollout (limit=3-5 ile manuel tetikleme) onaylanıp sonucu doğrulanmadan
Beat'e bağlanmamalı.
"""

import logging
import time

import database
from celery_app import celery_app
from qna_ai_engine import generate_draft_answer
from sync_lock import acquire_sync_lock, release_sync_lock
from trendyol_qna_client import fetch_questions
from hb_qna_client import fetch_questions as fetch_hb_questions

logger = logging.getLogger(__name__)

_TRENDYOL_LOCK_NAME = "trendyol_qna"
_HB_LOCK_NAME = "hepsiburada_qna"


def _run_qna_sync(marketplace, fetch_fn, status_value, lock_name, limit=None):
    """Trendyol ve HB task'larının paylaştığı ortak orkestrasyon --
    tek fark: hangi client'tan (fetch_fn) hangi status değeriyle
    sorguladığı ve hangi kilidi kullandığı. Dönen dict, sonucun ve varsa
    kısmi başarısızlıkların açık bir özeti -- sync'in "sessizce başarılı
    görünmesi" istenmiyor."""
    lock_token = acquire_sync_lock(lock_name)
    if lock_token is None:
        logger.info(
            f"[QnA Sync/{marketplace}] atlandı -- zaten devam eden bir "
            f"senkronizasyon var (kilit alınamadı)"
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
        questions = fetch_fn(status=status_value)
        stats["questions_synced"] = len(questions)

        rows = [{
            "marketplace": marketplace,
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
                    f"[QnA Sync/{marketplace}] question_id={q['question_id']}: sku eksik, "
                    f"AI taslağı ÜRETİLMEDİ, manuel triage gerekiyor."
                )
                continue

            existing_draft = database.get_draft_answer(marketplace=marketplace, question_id=q["question_id"])
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
                    f"[QnA Sync/{marketplace}] question_id={q['question_id']} (sku={q['sku']}): "
                    f"AI taslak üretimi başarısız: {exc}"
                )
                stats["failed"].append({"question_id": q["question_id"], "sku": q["sku"], "error": str(exc)})
                if budget is not None:
                    budget -= 1
                continue

            database.upsert_draft_answer(
                marketplace=marketplace,
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
        release_sync_lock(lock_token, lock_name)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300, name="qna_sync_tasks.sync_trendyol_questions")
def sync_trendyol_questions(self=None, limit=None):
    """Trendyol WAITING_FOR_ANSWER sorularını senkronize eder ve eksik
    taslakları AI ile üretir."""
    return _run_qna_sync(
        marketplace="trendyol", fetch_fn=fetch_questions,
        status_value="WAITING_FOR_ANSWER", lock_name=_TRENDYOL_LOCK_NAME, limit=limit,
    )


@celery_app.task(bind=True, max_retries=1, default_retry_delay=300, name="qna_sync_tasks.sync_hepsiburada_questions")
def sync_hepsiburada_questions(self=None, limit=None):
    """HB WaitingForAnswer (PascalCase, Trendyol'dan farklı enum formatı)
    sorularını senkronize eder ve eksik taslakları AI ile üretir."""
    return _run_qna_sync(
        marketplace="hepsiburada", fetch_fn=fetch_hb_questions,
        status_value="WaitingForAnswer", lock_name=_HB_LOCK_NAME, limit=limit,
    )
