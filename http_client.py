"""
http_client.py
----------------
Trendyol ve Hepsiburada istemcilerinin (trendyol_client.py, app.py'deki
Hepsiburada GET fonksiyonları, stock_client.py) neredeyse birebir aynı
"429 (rate limit) durumunda bekle → tekrar dene, başarılı istekten sonra
küçük bir throttle uygula" mantığını TEK yerde topluyor.

ÖNCEKİ DURUM: Bu desen dört ayrı yerde ayrı ayrı kopyalanmıştı
(app.py.trendyol_get, app.py.hepsiburada_get, app.py.hepsiburada_finance_get,
trendyol_client.trendyol_get) — biri düzeltilip diğerleri unutulma riski
taşıyordu (örn. birinde 429 handling güncellenip diğerlerinde unutulabilirdi).

İki bekleme (backoff) modu destekleniyor, mevcut davranışları BİREBİR korur:
  "exponential" -> wait = base_seconds * (2 ** attempt)          (Trendyol tarzı)
  "header_or_linear" -> resp header'dan oku, yoksa base*(attempt+1)  (Hepsiburada tarzı)
"""

import time

import requests


def get_json_with_retry(
    url,
    params=None,
    headers=None,
    auth=None,
    timeout=30,
    max_retries=5,
    throttle_seconds=0.0,
    backoff_mode="exponential",
    backoff_base_seconds=3,
    retry_wait_header=None,
):
    """GET isteği atar, 429'da bekleyip tekrar dener, başarılı JSON döner.

    backoff_mode:
      "exponential"       -> wait = backoff_base_seconds * (2 ** attempt)     (Trendyol tarzı)
      "header_or_linear"  -> retry_wait_header varsa yanıt header'ından okur,
                              yoksa backoff_base_seconds * (attempt + 1)       (Hepsiburada tarzı)
      "fixed"             -> her denemede her zaman backoff_base_seconds sn bekler
                              (bazı eski çağrı noktalarının orijinal davranışı)

    max_retries=None verilirse SINIRSIZ dener (orijinal bazı `while True: ...
    continue` desenleriyle birebir aynı davranış — rate limit süresi ne kadar
    uzun sürerse sürsün pes etmez). Sayısal bir değer verilirse, tüm denemeler
    429 ile biterse son yanıt üzerinde raise_for_status() çağrılır.
    """
    resp = None
    attempt = 0
    while max_retries is None or attempt < max_retries:
        resp = requests.get(url, params=params, headers=headers, auth=auth, timeout=timeout)
        if resp.status_code == 429:
            if backoff_mode == "fixed":
                wait = backoff_base_seconds
            elif backoff_mode == "header_or_linear":
                default_wait = backoff_base_seconds * (attempt + 1)
                wait = int(resp.headers.get(retry_wait_header, default_wait)) if retry_wait_header else default_wait
            else:
                wait = backoff_base_seconds * (2 ** attempt)
            time.sleep(wait)
            attempt += 1
            continue
        resp.raise_for_status()
        if throttle_seconds:
            time.sleep(throttle_seconds)
        return resp.json()

    resp.raise_for_status()
    return resp.json()
