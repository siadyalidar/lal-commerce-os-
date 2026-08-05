"""
external_payout_scraper.py
---------------------------
Trendyol ve Hepsiburada'nın resmi/dokümante Finans API'lerinde bulunmayan
"gelecek hakediş / upcoming payments" verisini, panel arayüzünden elle
girilen bir tarayıcı kimlik bilgisiyle (external_payout_db.get_credential)
çeker.

⚠️ Bunlar RESMİ/dokümante endpoint'ler DEĞİL — tarayıcı DevTools üzerinden
tespit edildi, önceden haber vermeden değişebilir/kaldırılabilir.

⚠️ Trendyol: Authorization header'ı bir JWT ve KISA ÖMÜRLÜ (tipik 15dk-1sa).
Panel arka planda otomatik yenileyemiyor (bir refreshToken akışı henüz
bulunamadı) — bu yüzden Celery Beat üzerinden sık otomatik çekim GERÇEKÇİ
DEĞİL: token süresi dolduğunda sync sessizce 401 ile başarısız olur ve bu,
credential_status/get_scrape_status üzerinden panelde "süresi dolmuş,
yenile" uyarısı olarak yansır (bkz. payout_routes.py
/api/payout-external-status). Gerçekçi kullanım: kullanıcı panelden
"Şimdi Çek" butonuyla o anki token'la TEK SEFERLİK manuel çekim yapar.

⚠️ Hepsiburada: Cookie tabanlı, haftalarca geçerli — Beat üzerinden periyodik
otomatik çekim (payout_scrape_tasks.py) bu taraf için pratik.
"""

import requests

from external_payout_db import get_credential

TY_FUTURE_PAYMENTS_URL = (
    "https://apigw.trendyol.com/partner/seller-reporting-sfjdomestic-santral-v2"
    "/dashboard/future-payments/list"
)

_REQUEST_TIMEOUT = 20
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _require_credential(marketplace):
    cred = get_credential(marketplace)
    if not cred:
        raise RuntimeError(
            f"{marketplace} için kayıtlı bir kimlik bilgisi yok — panelden "
            f"/api/payout-external-credential ile girilmesi lazım."
        )
    return cred["value"]


def fetch_ty_upcoming_payments():
    """Trendyol future-payments/list -> [{"region_code", "payment_date", "amount"}]

    Ham response formatı (gözlemlenen, 02.08.2026 DevTools yakalaması):
      {
        "items": [
          {
            "paymentDate": 1785704400000,
            "paymentGroup": {"name": "Türkiye, Körfez", "regionId": 1, "code": "TR"},
            "value": {"amount": {"value": 127...}}
          },
          ...
        ]
      }

    NOT: "flex-payment" isimli ayrı bir endpoint de vardı ama o "Erken Ödeme
    Al" (bakiyeyi erken çekme) teklif tutarını dönüyor — hakediş takvimiyle
    ilgisi yok, kasıtlı olarak burada kullanılmıyor.
    """
    token = _require_credential("trendyol")
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://partner.trendyol.com/",
        "Origin": "https://partner.trendyol.com",
    }
    resp = requests.get(TY_FUTURE_PAYMENTS_URL, headers=headers, timeout=_REQUEST_TIMEOUT)
    if resp.status_code == 401:
        raise RuntimeError(
            "Trendyol Authorization token'ı süresi dolmuş/geçersiz (401) — "
            "panelden DevTools ile alınan yeni token'ı gir."
        )
    if not resp.ok:
        # raise_for_status() gövdeyi hiç göstermiyordu (556 gibi standart
        # olmayan kodlarda "<none>" reason'ı yalnızca durumu gizliyordu) —
        # asıl sebebi (WAF blok mesajı, rate-limit, vs.) görebilmek için
        # gövdenin ilk 500 karakterini hataya ekliyoruz.
        body_preview = (resp.text or "")[:500]
        raise RuntimeError(
            f"Trendyol future-payments isteği {resp.status_code} döndü: {body_preview!r}"
        )
    data = resp.json()

    estimates = []
    for item in data.get("items", []):
        payment_date = item.get("paymentDate")
        region_code = (item.get("paymentGroup") or {}).get("code")
        amount = ((item.get("value") or {}).get("amount") or {}).get("value")
        if payment_date is None or amount is None:
            continue
        estimates.append({
            "region_code": region_code or "TR",
            "payment_date": payment_date,
            "amount": float(amount),
        })
    return estimates


def fetch_hb_upcoming_payments():
    """Hepsiburada gelecek hakediş çekimi.

    ⚠️ HENÜZ TAMAMLANMADI. Önceki oturumda DevTools'ta "upcomingestimated
    paymentlist" isimli bir istek tespit edilmiş ve 200 OK döndüğü
    doğrulanmıştı, ama o oturum mesaj limitine takıldığı için:
      - isteğin TAM URL'si
      - Request/Response header'ları
      - örnek response JSON'u
    bu dosyaya hiç işlenemedi. Devam etmek için panelden HB tarafında o
    isteğe tekrar tıklayıp (Network paneli → arama kutusuna "payment" veya
    "upcoming" yaz) Üst bilgiler + Önizle sekmelerinin ekran görüntüsünü
    paylaşman yeterli — Trendyol tarafında yaptığımız gibi aynı şekilde
    tamamlarım.
    """
    raise NotImplementedError(
        "Hepsiburada future-payments endpoint'i henüz bu dosyaya işlenmedi "
        "— tam URL, header'lar ve örnek response gerekiyor (bkz. docstring)."
    )
