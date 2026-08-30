"""
celery_app.py
-------------
Celery uygulaması + Beat (zamanlanmış görev) tanımları.

Çalıştırma (3 ayrı terminal/servis gerekir):
    1) redis-server
    2) celery -A celery_app worker --loglevel=info
    3) celery -A celery_app beat --loglevel=info

Prod'da bunları systemd servisleri veya Docker Compose ile ayağa kaldırın;
üçü de düşerse otomatik sync durur.
"""

import os

from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

# DÜZELTME: Bu dosya önceden load_dotenv() çağırmıyordu — REDIS_URL gibi
# .env'de tanımlı değişkenler, sadece kabuk (shell) ortamında export
# edilmişse görünüyordu. `celery -A celery_app worker` çıplak bir terminalde
# çalıştırıldığında .env'deki REDIS_URL sessizce YOK SAYILIP varsayılan
# redis://localhost:6379/0'a düşülüyordu — Redis'i farklı bir host/portta
# çalıştıranlar için bu, worker'ın broker'a hiç bağlanamamasına (ve
# app.py'deki _celery_worker_alive() ping'inin sessizce False dönmesine)
# yol açabilirdi.
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "trendyol_satis",
    broker=REDIS_URL,
    backend=REDIS_URL,
    # DÜZELTME: payout_scrape_tasks.py burada eksikti — o dosyanın kendi
    # docstring'i ("NOT 1") bu güncellemenin elle yapılması gerektiğini
    # zaten belirtiyordu. Artık worker açılırken bu modüldeki task da
    # (payout_scrape_tasks.sync_external_payout_estimates) otomatik
    # keşfediliyor; Beat'e zamanlama eklemek isteyenler artık sadece
    # aşağıdaki beat_schedule'a bir girdi eklemesi yeterli (bkz.
    # payout_scrape_tasks.py NOT 2 — Trendyol tarafı kısa ömürlü token
    # nedeniyle otomatik zamanlamaya hâlâ uygun değil, ama HB için artık
    # mümkün).
    # hb_review_sync_tasks: 23.08.2026 eklendi. Beat'e OTOMATİK eklenmedi
    # (bkz. hb_review_sync_tasks.py docstring'i) -- canlı ortamda küçük bir
    # örneklemle manuel doğrulama yapılmadan periyodik çalıştırmak riskli.
    # qna_sync_tasks: 29.08.2026 eklendi -- AYNI kural, Beat'e otomatik
    # eklenmedi (bkz. qna_sync_tasks.py docstring'i).
    include=["tasks", "payout_scrape_tasks", "hb_review_sync_tasks", "qna_sync_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Istanbul",
    enable_utc=True,
    task_track_started=True,
    # Bir sync task'i donarsa worker'ı sonsuza kadar kilitlemesin:
    task_time_limit=60 * 30,       # hard limit: 30 dk sonra öldürülür
    task_soft_time_limit=60 * 25,  # soft limit: 25 dk'da SoftTimeLimitExceeded fırlatılır
)

celery_app.conf.beat_schedule = {
    # Sık senkronizasyon: son 2 günü çeker (yeni siparişler + hızlı finans güncellemeleri).
    # Süreyi mağaza sayısı/plan tipine göre artırıp azaltabilirsiniz.
    "periodic-recent-sync": {
        "task": "tasks.scheduled_recent_sync",
        "schedule": 60 * 20,  # her 20 dakikada bir
    },
    # Gece mutabakatı: Trendyol bazı finansal kayıtları (settlement/otherfinancials)
    # gecikmeli işleyebiliyor; son 7 günü yeniden çekip gecikmeli gelen kayıtları yakalar.
    "nightly-reconciliation-sync": {
        "task": "tasks.scheduled_reconciliation_sync",
        "schedule": crontab(hour=3, minute=0),  # her gece 03:00 (Europe/Istanbul)
    },
    # OPSİYONEL — canlı HB kimlik bilgileriyle küçük bir örneklemle (limit=3-5)
    # manuel doğrulama yaptıktan SONRA aşağıdaki yorumu kaldırın (bkz.
    # tasks.py backfill_hb_quantities docstring'i). Kuyruk boşalana kadar
    # günde birkaç kez küçük partiler halinde HB "settlement-only" paketlerin
    # gerçek quantity/fiyat bilgisini geriye dönük doldurur.
    # "hb-quantity-backfill": {
    #     "task": "tasks.backfill_hb_quantities",
    #     "schedule": 60 * 60 * 6,  # 6 saatte bir, limit=30 (varsayılan)
    # },
    #
    # OPSİYONEL — DEFAULT_REFERER varsayımı 24.08.2026'da canlı ortamda
    # doğrulandı (bkz. Faz1 Manuel Doğrulama Raporu). Ancak tüm katalog
    # için Beat'e bağlamadan önce, önce kontrollü bir ilk rollout
    # (sync_hepsiburada_reviews.delay(limit=5) gibi küçük bir limit'le)
    # manuel tetiklenip sonucu doğrulanmalı -- SONRA aşağıdaki yorumu
    # kaldırın. "nightly-reconciliation-sync" (03:00) ile çakışmasın diye
    # 04:00 seçildi; review içeriği sipariş verisi kadar zaman-kritik
    # olmadığı için günde bir kez yeterli görülüyor.
    "hb-review-sync": {
        "task": "hb_review_sync_tasks.sync_hepsiburada_reviews",
        "schedule": crontab(hour=4, minute=0),  # her gece 04:00 (Europe/Istanbul)
        "kwargs": {"limit": None},
    },
    # OPSİYONEL — canlı Trendyol kimlik bilgileriyle küçük bir örneklemle
    # (limit=3-5) manuel doğrulama yaptıktan SONRA aşağıdaki yorumu kaldırın
    # (bkz. qna_sync_tasks.py docstring'i, 29.08.2026). 03:00 ve 04:00
    # dolu olduğu için 05:00 seçildi.
    # "trendyol-qna-sync": {
    #     "task": "qna_sync_tasks.sync_trendyol_questions",
    #     "schedule": crontab(hour=5, minute=0),  # her gece 05:00 (Europe/Istanbul)
    #     "kwargs": {"limit": None},
    # },
}
