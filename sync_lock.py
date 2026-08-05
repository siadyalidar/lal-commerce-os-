"""
sync_lock.py
------------
Süreçler-arası (Flask web süreci, Celery worker, Celery Beat) senkronizasyon
kilidi. Eskiden app.py'deki `threading.Lock()` bunu yapıyordu ama o kilit
sadece TEK bir Python sürecini korur — Celery worker ayrı bir süreç olduğu
için aynı anda hem kullanıcı "Senkronize Et" butonuna basıp hem de zamanlanmış
görev tetiklenirse, iki süreç de aynı SQLite dosyasına aynı anda yazabilirdi.

Redis SETNX (set + nx=True) ile atomik kilit alıyoruz. TTL koyuyoruz ki bir
süreç kilidi alıp çökerse (örn. sunucu yeniden başlarsa) kilit sonsuza kadar
takılı kalmasın.
"""

import os
import uuid

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis = redis.Redis.from_url(REDIS_URL)

_LOCK_PREFIX = "trendyol_satis:sync_lock:"
# Normalde bir senkronizasyon bundan çok daha kısa sürer; süreç çökerse
# kilidin kalıcı takılı kalmaması için güvenlik payı.
_LOCK_TTL_SECONDS = 30 * 60

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def acquire_sync_lock(name="trendyol"):
    """Kilidi almayı dener (marketplace/mağaza bazında ayrı kilit — 'trendyol'
    ve 'hepsiburada' aynı anda çalışabilir, ikisi de birbirini beklemez).
    Başarılıysa release_sync_lock'a geçireceğiniz bir token döner; kilit
    zaten alınmışsa None döner."""
    token = uuid.uuid4().hex
    acquired = _redis.set(_LOCK_PREFIX + name, token, nx=True, ex=_LOCK_TTL_SECONDS)
    return token if acquired else None


def release_sync_lock(token, name="trendyol"):
    """Sadece kilidi biz aldıysak serbest bırakır (token eşleşmezse dokunmaz —
    böylece TTL dolup başka bir süreç kilidi aldıktan sonra bizim finally
    bloğumuz onun kilidini yanlışlıkla açmaz)."""
    if not token:
        return
    try:
        _redis.eval(_RELEASE_SCRIPT, 1, _LOCK_PREFIX + name, token)
    except redis.RedisError:
        # Redis geçici olarak erişilemezse kilidi TTL zaten temizleyecek;
        # burada patlamak senkronizasyonun kendisini başarısız göstermemeli.
        pass


def sync_lock_status(name="trendyol"):
    """Debug/monitoring için: kilit şu an tutuluyor mu?"""
    return _redis.exists(_LOCK_PREFIX + name) == 1
