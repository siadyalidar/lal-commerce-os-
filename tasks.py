"""
tasks.py
--------
Zamanlanmış (Celery Beat tetiklemeli) senkronizasyon görevleri.

app.py'deki mevcut _run_full_sync / _run_hb_sync fonksiyonlarını tekrar
yazmak yerine buradan import ediyoruz — sync mantığı tek yerde kalıyor.
Import'lar fonksiyon içinde (lazy) çünkü Celery worker açılırken app.py'nin
Flask app'ini oluşturması (init_db() dahil) modül yükleme sırasına bağımlı
olmasın diye.

İleride multi-tenant'a geçince: bu task'lar tenant_id parametresi alacak
şekilde genişleyecek (örn. `scheduled_recent_sync.delay(tenant_id)`), ve
_run_full_sync/_run_hb_sync de tenant'a özel kimlik bilgileriyle çalışacak
şekilde parametrize edilecek. Şimdilik tek mağaza (.env'deki kimlik
bilgileri) için.
"""

import os
import sys
from datetime import datetime, timedelta

# GÜVENCE (05.08.2026): 'celery -A celery_app worker' komutu (PATH üzerinden,
# 'python -m celery' OLMADAN) çalıştırıldığında, forklanan worker alt
# süreçlerinde proje klasörü sys.path'te olmayabiliyor (macOS'ta gözlemlendi)
# — bu da bu dosyanın altındaki 'from app import ...' gibi lazy import'ların
# "ModuleNotFoundError: No module named 'app'" ile patlamasına yol açıyordu.
# 'python -m celery' cwd'yi otomatik ekliyordu, düz 'celery' komutu her zaman
# eklemiyor. Bu proje klasörünü EL İLE, hangi şekilde çalıştırılırsa
# çalıştırılsın garantiye alıyor.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from celery.exceptions import SoftTimeLimitExceeded

from celery_app import celery_app
from sync_lock import acquire_sync_lock, release_sync_lock


def _run_locked(name, target_fn, *args, **kwargs):
    """Kilidi alıp verilen sync fonksiyonunu çalıştırır. Kilit zaten
    alınmışsa (örn. kullanıcı panelden elle 'Senkronize Et'e basmışsa)
    sessizce atlar — bir sonraki zamanlanmış tetiklemede tekrar denenecek."""
    token = acquire_sync_lock(name)
    if token is None:
        return f"{name}: atlandı, zaten devam eden bir senkronizasyon var"
    try:
        target_fn(*args, **kwargs)
        return f"{name}: tamamlandı"
    finally:
        release_sync_lock(token, name)


def _scheduled_sync(days, incremental_ok):
    from app import _check_hb_credentials, _run_full_sync, _run_hb_sync

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)

    results = [
        _run_locked("trendyol", _run_full_sync, start_dt, end_dt, incremental_ok=incremental_ok)
    ]
    if _check_hb_credentials() is None:
        results.append(
            _run_locked("hepsiburada", _run_hb_sync, start_dt, end_dt, incremental_ok=incremental_ok)
        )
    return results


@celery_app.task(bind=True, max_retries=2, default_retry_delay=300)
def scheduled_recent_sync(self):
    """Her 20 dakikada bir: son 2 günü, cursor'a göre incremental olarak
    senkronize eder (manuel 'Senkronize Et' ile aynı davranış)."""
    try:
        return _scheduled_sync(days=2, incremental_ok=True)
    except SoftTimeLimitExceeded:
        # Zaman aşımı — bir sonraki periyotta zaten tekrar denenecek, retry'a gerek yok.
        return "zaman aşımına uğradı"
    except Exception as exc:
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=600)
def scheduled_reconciliation_sync(self):
    """Her gece 03:00: son 7 günü cursor'u YOK SAYARAK (incremental_ok=False)
    tam tarar — Trendyol'un gecikmeli işlediği finansal kayıtları yakalamak için."""
    try:
        return _scheduled_sync(days=7, incremental_ok=False)
    except SoftTimeLimitExceeded:
        return "zaman aşımına uğradı"
    except Exception as exc:
        raise self.retry(exc=exc)


# ============================================================
# MANUEL SENKRONİZASYON (panelden "Senkronize Et" butonu)
# ============================================================
# ÖNCEKİ DURUM: app.py'deki /api/sync-finance ve /api/sync-hepsiburada
# route'ları ham threading.Thread(daemon=True) kullanıyordu. Bunun sorunu:
# Flask web süreci restart olursa (deploy, crash, systemd restart) bu
# thread'ler sessizce kaybolur ve database.sync_progress tablosu "running"
# durumunda SONSUZA KADAR takılı kalabilirdi (kimse finish/fail çağırmadığı
# için) — panelde "senkronizasyon devam ediyor" görünüp asla bitmezdi.
#
# ÇÖZÜM: Manuel tetiklemeler de artık Celery task kuyruğuna gönderiliyor.
# Celery worker süreci Flask'tan bağımsız olduğu için web süreci restart
# olsa bile task çalışmaya devam eder; task_time_limit (celery_app.py,
# 30 dk) de "sonsuza kadar running kalma" riskine karşı bir güvenlik ağı
# sağlar (worker task'ı zorla öldürüp progress tablosunu güncellemeye
# fırsat bulamasa bile, en azından süreç kilitlenmez).
#
# NOT (payout_scrape_tasks.py'deki .delay() eleştirisiyle TUTARLI): .delay()
# worker ayakta olmasa bile hata vermeden mesajı broker'a bırakır — kullanıcı
# "başladı" mesajı görüp sonra hiçbir şeyin olmadığını fark edebilir. Bunu
# önlemek için app.py tarafında task'ı .delay() ile göndermeden ÖNCE
# celery_app.control.inspect().ping() ile en az bir worker'ın canlı olduğu
# doğrulanıyor (bkz. app.py _celery_worker_alive()); worker yoksa kullanıcıya
# net bir hata dönülüyor, sessizce "started" denmiyor.
#
# Lock yönetimi: bu task'lar kilidi KENDİLERİ alıp bırakır (_run_locked ile
# aynı desen) — böylece route, task'ı göndermeden önce sadece salt-okunur
# sync_lock_status() ile hızlı bir 409 ön-kontrolü yapabilir, gerçek
# atomik kilit alma işini (race condition'a karşı) task'ın kendisi yapar.

def _epoch_ms_to_dt(ms):
    return datetime.fromtimestamp(ms / 1000)


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def manual_sync_trendyol(self, start_ms, end_ms, incremental_ok=True):
    """Panelden 'Senkronize Et' ile tetiklenen Trendyol senkronu.
    start_ms/end_ms: epoch milisaniye (Celery JSON serializer datetime
    taşımadığı için int olarak gönderiliyor)."""
    from app import _run_full_sync

    start_dt, end_dt = _epoch_ms_to_dt(start_ms), _epoch_ms_to_dt(end_ms)
    try:
        return _run_locked("trendyol", _run_full_sync, start_dt, end_dt, incremental_ok=incremental_ok)
    except SoftTimeLimitExceeded:
        return "zaman aşımına uğradı"


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def manual_sync_hepsiburada(self, start_ms, end_ms, incremental_ok=True):
    """Panelden 'Senkronize Et' ile tetiklenen Hepsiburada senkronu."""
    from app import _run_hb_sync

    start_dt, end_dt = _epoch_ms_to_dt(start_ms), _epoch_ms_to_dt(end_ms)
    try:
        return _run_locked("hepsiburada", _run_hb_sync, start_dt, end_dt, incremental_ok=incremental_ok)
    except SoftTimeLimitExceeded:
        return "zaman aşımına uğradı"


# ============================================================
# HB "settlement-only" QUANTITY BACKFILL (Faz 3) — periyodik hale getirme
# ============================================================
# ÖNCEKİ DURUM: app.py'deki backfill_hb_settlement_only_quantities() SADECE
# panelden elle "Eksik miktarları tamamla" butonuyla (veya cron ile dışarıdan)
# tetikleniyordu. Kullanıcı bunu unutursa, settlement-only HB paketlerindeki
# quantity=1 VARSAYIMI (bkz. finance_engine.py BACKFILL NOTU) süresiz kalıp
# kâr hesabını kalıcı olarak saptırabilir — özellikle çok adetli satışlarda.
#
# Bu task, küçük partiler halinde (varsayılan 30 sipariş/çalıştırma — HB API
# rate limitine takılmamak için kasıtlı düşük tutuldu) kuyruğu otomatik
# eritir. BİLİNÇLİ OLARAK Beat'e VARSAYILAN OLARAK EKLENMEDİ (celery_app.py'de
# yorum satırı halinde örnek var) — çünkü app.py'deki backfill fonksiyonunun
# kendi docstring'i bunun canlı HB kimlik bilgileriyle HENÜZ TEST EDİLMEDİĞİNİ
# açıkça belirtiyor; küçük bir örneklemle (limit=3-5) manuel doğrulama
# yapılmadan otomatik/periyodik çalıştırmak riskli olur. Doğrulandıktan sonra
# celery_app.py'deki örnek satırın yorumunu kaldırmanız yeterli.
@celery_app.task(bind=True, max_retries=1, default_retry_delay=300)
def backfill_hb_quantities(self, limit=30):
    from app import _check_hb_credentials, backfill_hb_settlement_only_quantities

    if _check_hb_credentials() is not None:
        return "atlandı: Hepsiburada kimlik bilgileri tanımlı değil"
    try:
        return backfill_hb_settlement_only_quantities(limit=limit)
    except SoftTimeLimitExceeded:
        return "zaman aşımına uğradı"
