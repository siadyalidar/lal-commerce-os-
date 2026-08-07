# Trendyol & Hepsiburada Satış / Kâr-Zarar Paneli

Trendyol Partner (Satıcı) API'si ve Hepsiburada Merchant API'sinden sipariş,
iade ve finans verilerini otomatik çekip; **gerçek kâr/zarar**, günlük satış
özeti, stok ve ödeme (payout) takibini tek bir panelde gösteren, yerelde
(local) çalışan bir Flask uygulamasıdır.

Uygulama, ham sipariş/settlement verisini yalnızca göstermekle kalmaz —
komisyon, kargo, iade, sabit gider ve KDV gibi kalemleri de işleyerek
platformların panelinde göremeyeceğiniz **ürün ve gün bazında net kâr**
hesabı üretir.

---

## Öne Çıkan Özellikler

- **Çoklu pazaryeri desteği** — Trendyol (canlı) ve Hepsiburada (SIT/test
  aşamasında) sipariş, iade ve settlement verilerinin tek veritabanında
  birleştirilmesi.
- **Gerçek kâr/zarar motoru** (`finance_engine.py`) — komisyon, kargo,
  iade, sabit giderler ve KDV dahil edilerek ürün/gün/ay bazında net kâr
  hesaplanır; henüz settlement'ı oluşmamış siparişler için tahmini gelirle
  düzeltme yapılır.
- **Ödeme takvimi (payout calendar)** — pazaryerlerinin ödeme tarihlerini
  esas alan, resmi verilerle override edilebilen tahmini nakit akışı.
- **Asenkron senkronizasyon** — Celery + Redis ile arka planda veri çekme;
  ilerleme durumu `/api/sync-status` üzerinden izlenebilir.
- **Stok yönetimi** — ürün ve stok senkronizasyonu, maliyet (Excel) içe
  aktarma (`cost_import.py`).
- **Güvenlik** — panel girişi kullanıcı adı/şifre ile korunur; pazaryeri
  API kimlik bilgileri (JWT/Cookie) Fernet ile şifrelenerek saklanır.
- **Dashboard, sipariş, finans, stok ve ürün sayfaları** — sunucu taraflı
  render edilen (Jinja2) sayfalar + REST API uç noktaları.

## Mimari Genel Bakış

```
app.py                 → Flask giriş noktası, route'lar, panel auth
database.py             → SQLite şeması, migration'lar, CRUD yardımcıları
finance_engine.py       → Kâr/zarar hesap motoru (komisyon, kargo, KDV, vb.)
trendyol_client.py      → Trendyol API istemcisi
trendyol_finance.py     → Trendyol finans/settlement senkronizasyonu
stock_client.py         → Stok senkronizasyonu (Trendyol/Hepsiburada)
external_payout_scraper.py / external_payout_db.py
                        → Pazaryeri panel tarafından "resmi" ödeme verisi
                          kazıma (scraping) ve şifreli saklama
celery_app.py / tasks.py / payout_scrape_tasks.py
                        → Arka plan görevleri (senkronizasyon, scraping)
sync_lock.py            → Eşzamanlı senkronizasyonu önleyen kilit mekanizması
blueprints/              → cost, payout, stock için ayrı Flask blueprint'leri
templates/ , static/     → Panel arayüzü (Jinja2 + CSS/JS)
tests/                   → pytest test paketi
```

Senkronizasyon mekanizması Celery üzerinden çalışır; `/api/sync-finance` ve
`/api/sync-hepsiburada` uç noktaları Celery worker ayakta değilse `503`
döner. Yani panelin tam işlevsel çalışması için Celery worker (ve Beat,
zamanlanmış görevler için) sürecinin de çalışıyor olması gerekir.

## Gereksinimler

- Python 3.10+
- Redis (Celery broker/backend için)
- Trendyol Satıcı hesabı ve API kimlik bilgileri
- (Opsiyonel) Hepsiburada Merchant hesabı — entegrasyon henüz test/SIT
  aşamasında

## Kurulum

```bash
git clone https://github.com/siadyalidar/lal-commerce-os-.git
cd lal-commerce-os-
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Geliştirme/test bağımlılıkları için ayrıca:

```bash
pip install -r requirements-dev.txt
```

### Ortam Değişkenleri (`.env`)

Proje kökünde bir `.env` dosyası oluşturup aşağıdaki değişkenleri doldurun:

| Değişken | Açıklama |
|---|---|
| `TRENDYOL_SUPPLIER_ID` | Trendyol satıcı (supplier) kimliği |
| `TRENDYOL_API_KEY` / `TRENDYOL_API_SECRET` | Trendyol Partner API kimlik bilgileri |
| `TRENDYOL_ENV` | `PROD` veya `STAGE` (varsayılan `PROD`) |
| `TRENDYOL_INTEGRATOR_NAME` | API isteklerinde kullanılan entegratör adı |
| `TRENDYOL_DATA_START_DATE` | "Tüm Zamanlar" senkronizasyonunun başlangıç tarihi |
| `HEPSIBURADA_MERCHANT_ID` / `HEPSIBURADA_USERNAME` / `HEPSIBURADA_PASSWORD` | Hepsiburada Merchant API kimlik bilgileri |
| `HEPSIBURADA_ENV` | `SIT` (test) veya canlı ortam |
| `HEPSIBURADA_USER_AGENT` | Scraping/istemci için User-Agent değeri |
| `PANEL_USERNAME` / `PANEL_PASSWORD` | Panele giriş için kullanıcı adı/şifre — `PROD` ortamında zorunludur |
| `PAYOUT_CREDENTIAL_KEY` | Kaydedilen pazaryeri kimlik bilgilerini şifrelemek için Fernet anahtarı (ilk çalıştırmada otomatik üretilip konsola basılır) |
| `REDIS_URL` | Celery broker/backend bağlantı adresi (ör. `redis://localhost:6379/0`) |
| `LOG_LEVEL` | Log seviyesi (varsayılan `INFO`) |

> `PROD` ortamında `PANEL_USERNAME`/`PANEL_PASSWORD` tanımlı değilse
> uygulama artık **fail-open olarak açılmaz**; başlatma reddedilir.

### Çalıştırma

Uygulama (Flask):

```bash
python app.py
```

Celery worker (senkronizasyon görevleri için zorunlu):

```bash
celery -A celery_app worker --loglevel=info
```

Celery Beat (zamanlanmış görevler için):

```bash
celery -A celery_app beat --loglevel=info
```

Panel varsayılan olarak `http://localhost:5000` üzerinde açılır.

## Kullanılabilir Sayfalar

| Yol | Açıklama |
|---|---|
| `/dashboard`, `/gosterge-paneli` | Genel gösterge paneli |
| `/siparisler`, `/orders` | Sipariş listesi |
| `/finans` | Kâr/zarar ve ödeme takvimi |
| `/stok` | Stok durumu |
| `/urunler` | Ürün performansı |
| `/ayarlar` | Panel ve API ayarları |

## Başlıca API Uç Noktaları

`dashboard-summary`, `daily-sales`, `daily-returns`, `monthly-profit`,
`best-sellers`, `product-performance`, `orders`, `sync-finance`,
`sync-hepsiburada`, `sync-status`, `fixed-expenses`, `config-status`,
`hb-config-status`, `today-net-profit`, `today-order-count` gibi uç
noktalar `/api/` altında sunulur (tam liste için `app.py` içindeki route
tanımlarına bakın).

## Testler

```bash
pytest
```

Test paketi; finans motoru hesaplamaları (komisyon/kargo/iade/KDV
kuralları, payout calendar mantığı), veritabanı migration'ları, harici
payout DB'si ve HTTP istemcisi için kapsamlı senaryolar içerir
(`tests/`).

## Proje Notları

- `DEGISIKLIKLER.md` — önceki bir denetimde tespit edilen maddelerin
  uygulanma özetini içerir (credential şifreleme, Celery'ye geçiş, panel
  auth fail-open düzeltmesi vb.).
- `KALAN_ADIMLAR.md` — gerçek Hepsiburada kimlik bilgileriyle elle
  doğrulanması gereken adımları listeler (bu geliştirme ortamının
  Hepsiburada API'sine ağ erişimi yoktur).

## Güvenlik Notları

- `.env`, `*.db` ve `.payout_credential.key` dosyaları `.gitignore`
  içinde hariç tutulmuştur — **asla commit etmeyin**.
- Kaydedilen pazaryeri kimlik bilgileri (JWT/Cookie) Fernet ile
  şifrelenir; anahtar kaybolursa eski kayıtlar okunamaz hale gelir
  (veri kaybı olmaz, formdan yeniden girilmesi gerekir).

## Lisans

Bu depo için henüz bir lisans belirtilmemiştir.
