"""
payout_scrape_tasks.py
-----------------------
Celery task: Trendyol + Hepsiburada'nın resmi API'lerinde olmayan "gelecek
hakediş" verisini external_payout_scraper.py ile çekip external_payout_db'ye
yazar. payout_routes.py /api/payout-external-sync ile manuel tetikler;
Beat üzerinden periyodik çalıştırmak istersen aşağıdaki NOT'a bak.

NOT 1 — ✅ TAMAMLANDI: celery_app.py artık `include=["tasks", "payout_scrape_tasks"]`
  olarak güncellendi, bu task worker tarafından otomatik keşfediliyor.

NOT 2 — Beat'e otomatik zamanlama eklemedim:
  Trendyol tarafı kısa ömürlü Authorization token'a bağlı olduğu için
  (bkz. external_payout_scraper.py başındaki not) periyodik otomatik çekim
  Trendyol için çoğu zaman 401 ile başarısız olacaktır. Hepsiburada tek
  başına periyodik çekilebilir ama şu an bu dosyada TEK bir task içinde
  ikisi birlikte deneniyor (biri başarısız olsa da diğeri denenir, sonuç
  ayrı ayrı raporlanır). İstersen celery_app.py'deki beat_schedule'a
  şunu ekleyebilirsin (örn. HB için günde birkaç kez):
      "hb-payout-estimates-sync": {
          "task": "payout_scrape_tasks.sync_external_payout_estimates",
          "schedule": 60 * 60 * 4,  # 4 saatte bir
      },
  Trendyol tarafı büyük olasılıkla bu çalıştırmalarda 401 verecek — bu
  beklenen bir durum, panelde "Şimdi Çek" ile manuel tazelenmesi gerekiyor.
"""

from celery_app import celery_app
from external_payout_db import record_scrape_attempt, save_estimates
from external_payout_scraper import fetch_hb_upcoming_payments, fetch_ty_upcoming_payments


@celery_app.task(name="payout_scrape_tasks.sync_external_payout_estimates")
def sync_external_payout_estimates():
    """Her iki pazaryerini de dener; biri başarısız olsa da diğerini
    engellemez. Sonuç: {"trendyol": {...}, "hepsiburada": {...}}"""
    results = {}
    for marketplace, fetch_fn in (
        ("trendyol", fetch_ty_upcoming_payments),
        ("hepsiburada", fetch_hb_upcoming_payments),
    ):
        try:
            estimates = fetch_fn()
            save_estimates(marketplace, estimates)
            record_scrape_attempt(marketplace, ok=True)
            results[marketplace] = {"ok": True, "count": len(estimates)}
        except Exception as e:
            record_scrape_attempt(marketplace, ok=False, error=str(e))
            results[marketplace] = {"ok": False, "error": str(e)}
    return results
