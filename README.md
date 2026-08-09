# LAL Commerce OS

Trendyol Partner (Satıcı) API'si ve Hepsiburada Merchant API'sinden sipariş,
iade ve finans verilerini otomatik çekip; **gerçek kâr/zarar**, günlük satış
özeti, stok ve ödeme (payout) takibini tek bir panelde gösteren, yerelde
(local) çalışan bir Flask uygulamasıdır.

Uygulama, ham sipariş/settlement verisini yalnızca göstermekle kalmaz —
komisyon, kargo, iade, sabit gider ve KDV gibi kalemleri de işleyerek
platformların panelinde göremeyeceğiniz **ürün ve gün bazında net kâr**
hesabı üretir.

> **Bu dosyanın amacı:** Claude ile (veya başka biriyle) bu proje üzerinde
> çalışırken dosya dosya proje içeriğini yeniden aktarmaya gerek kalmasın
> diye — mimari, dizin yapısı, tasarım sistemi, ortam değişkenleri ve
> bilinen kısıtlar burada tek yerde tutuluyor. Yapısal bir değişiklik
> (yeni sayfa, yeni tablo, yeni env değişkeni, tasarım sistemi kuralı)
> yapıldığında bu dosya da aynı PR/commit içinde güncellenmelidir.

---

## Öne Çıkan Özellikler

- **Çoklu pazaryeri desteği** — Trendyol (canlı) ve Hepsiburada (canlı/PROD)
  sipariş, iade ve settlement verilerinin tek veritabanında birleştirilmesi.
  Hepsiburada tarafında hem paketlenmiş (`/packages`) hem de henüz
  "Paketlenecek" kuyruğunda duran paketlenmemiş (`/orders`) siparişler
  ayrı ayrı çekilip birleştirilir — sadece paketlenmiş kayıtlara bakan bir
  entegrasyon, panelde görünen aktif siparişlerin büyük kısmını kaçırır.
- **Gerçek kâr/zarar motoru** (`finance_engine.py`) — komisyon, kargo,
  iade, sabit giderler ve KDV dahil edilerek ürün/gün/ay bazında net kâr
  hesaplanır; henüz settlement'ı oluşmamış siparişler için tahmini gelirle
  düzeltme yapılır. Sipariş listesinde de her satır için tek bir **Net
  Kâr** kolonu gösterilir (aynı siparişteki birden fazla ürün satırı
  sipariş numarası bazında toplanarak tek rakama indirgenir).
- **Ödeme takvimi (payout calendar)** — pazaryerlerinin ödeme tarihlerini
  esas alan, resmi verilerle override edilebilen tahmini nakit akışı.
- **Manuel + zamanlanmış senkronizasyon** — "Verileri Senkronize Et"
  butonu Flask process'i içinde arka plan thread'i tetikler (Celery worker
  şart değildir); ayrıca Celery + Redis üzerinden her gece 03:00'te
  (Europe/Istanbul) otomatik tam senkronizasyon çalışır. İlerleme durumu
  `/api/sync-status` üzerinden izlenebilir. Ayrıntı için bkz. [Senkronizasyon
  Mimarisi](#senkronizasyon-mimarisi).
- **Stok yönetimi** — ürün ve stok senkronizasyonu, maliyet (Excel) içe
  aktarma (`cost_import.py`).
- **Güvenlik** — panel girişi HTTP Basic Auth (kullanıcı adı/şifre) ile
  korunur; pazaryeri API kimlik bilgileri (JWT/Cookie) Fernet ile
  şifrelenerek saklanır.
- **Dashboard, sipariş, finans, stok ve ürün sayfaları** — sunucu taraflı
  render edilen (Jinja2) sayfalar + REST API uç noktaları, kendi tasarım
  sistemi (LDL — bkz. aşağı) ile.

## Mimari Genel Bakış

```
app.py                  → Flask giriş noktası, blueprint kaydı, panel auth (Basic Auth)
database.py              → SQLite şeması, migration'lar, CRUD yardımcıları
finance_engine.py        → Kâr/zarar hesap motoru (komisyon, kargo, KDV, vb.)
sync_core.py             → Trendyol/Hepsiburada senkron mantığı (config + iş mantığı;
                            app.py'den taşındı, blueprint'ler buradan import eder)
trendyol_client.py       → Trendyol API istemcisi (ham HTTP çağrıları)
trendyol_finance.py      → Trendyol finans/settlement senkronizasyonu
stock_client.py          → Stok senkronizasyonu (Trendyol/Hepsiburada)
external_payout_scraper.py / external_payout_db.py
                         → Pazaryeri panel tarafından "resmi" ödeme verisi
                           kazıma (scraping) ve şifreli saklama
celery_app.py / tasks.py / payout_scrape_tasks.py
                         → Zamanlanmış (Celery Beat tetiklemeli) arka plan görevleri
sync_lock.py             → Eşzamanlı senkronizasyonu önleyen kilit mekanizması
blueprints/               → dashboard, order, finance, cost, stock, payout route'ları
templates/ , static/      → Panel arayüzü (Jinja2 + CSS/JS) — bkz. Frontend bölümü
tests/                    → pytest test paketi
migrations/               → Tek seferlik DB migration script'leri (--dry-run/--apply)
```

### Senkronizasyon Mimarisi

İki ayrı mekanizma var, karıştırılmamalı:

1. **Manuel senkron** (topbar'daki "Verileri Senkronize Et" butonu →
   `/api/sync-finance`, `/api/sync-hepsiburada`) — Flask process'i
   içinde `threading.Thread(daemon=True)` ile arka planda çalışır.
   **Celery worker ayakta olmasa da çalışır.** İlerleme `/api/sync-status`
   ile poll edilir.
2. **Zamanlanmış senkron** — Celery Beat, her gece 03:00'te (Europe/Istanbul)
   tam senkronizasyonu tetikler (`celery_app.py` → `beat_schedule`).
   Bunun çalışması için hem `celery -A celery_app worker` hem de
   `celery -A celery_app beat` process'lerinin **ayrıca ve sürekli**
   ayakta olması gerekir — bu üretimde (production) henüz kalıcı bir
   servis olarak koşulmuyor, **açık bir altyapı borcu**.

## Frontend / Tasarım Sistemi (LAL Design Language — "LDL")

Uygulama bir **çok sayfalı Flask uygulaması (MPA)** — React/SPA yok,
sayfa geçişleri tam sayfa yenileme (view-transition API ile yumuşatılmış,
bkz. `tokens.css`). Jinja2 template'leri + vanilla CSS/JS kullanılıyor.

### Şablon yapısı

```
templates/base.html                  → tüm sayfaların ortak iskeleti (head, sidebar,
                                        topbar, footer, script include sırası)
templates/components/_sidebar.html   → sol navigasyon
templates/components/_topbar.html    → üst bar (tema toggle, senkron butonu, tarih filtresi)
templates/pages/*.html               → sayfa içerikleri, {% extends "base.html" %} +
                                        {% block content %}
```

Yeni bir sayfa eklerken: `templates/pages/` altına dosya, `blueprints/`
içinde bir route (`render_template("pages/xxx.html", active_page="xxx")`),
gerekiyorsa `static/js/xxx.js` + `{% block page_scripts %}`.

### CSS katman sırası (ÖNEMLİ — `base.html` içindeki sıra kasıtlı)

```
1. tokens.css            → TEK gerçek tasarım kaynağı (single source of truth).
                            Renk/spacing/radius/font/motion — HER ŞEY burada
                            CSS custom property olarak tanımlı. Component
                            dosyaları asla ham hex/px yazmaz, token'a referans verir.
2. legacy-bridge.css      → eski class isimlerini yeni token'lara bağlayan köprü
3. legacy.css             → refaktör öncesinden kalma component stilleri
4. components.css         → güncel/yeni component stilleri
5. precision-theme.css    → "LAL Precision Console" ortak görsel kimliği +
                            AÇIK TEMA override'ları (html[data-lal-theme="light"])
+ sayfa-özel CSS (varsa)  → payout-calendar.css, monthly-profit-chart.css,
                            ayarlar-credentials.css, components-addition.css
```

Yeni bir renk/spacing değeri gerekiyorsa **önce `tokens.css`'e eklenir**,
sonra component'te `var(--lal-...)` ile kullanılır.

### Açık/koyu tema mekanizması

- Tema seçimi `<html data-lal-theme="dark|light">` attribute'unda tutulur,
  `localStorage['lal-theme']` içinde saklanır (bkz. `static/js/shell.js`).
- `tokens.css` içindeki `--lal-*` değişkenleri **koyu tema varsayılan**
  değerleridir; `precision-theme.css` içinde `html[data-lal-theme="light"]`
  bloğu aynı değişkenleri açık tema değerleriyle **override** eder.
  Yani bir component her zaman `var(--lal-bg)` gibi token'ı kullanır,
  tema farkını asla kendisi `if/else` ile yönetmez.
- Tema değiştiğinde `lal:theme-change` custom event'i dispatch edilir —
  Canvas tabanlı (Chart.js) grafikler CSS değişkenlerini otomatik
  izleyemediği için bu event'i dinleyip kendi renklerini yeniden okur.
- Marka rengi (`--lal-accent`, kırmızı/bordo ton) koyu ve açık temada
  farklı hex değerlere sahip ama aynı "kimliği" taşıyacak şekilde ayrı
  ayrı ayarlanmıştır — birini değiştirirken diğerini unutmayın.

### Tipografi

| Rol | Font | Token |
|---|---|---|
| Başlık/display | Instrument Sans | `--lal-font-display` |
| Gövde metni | Inter | `--lal-font-body` |
| Sayısal/veri (SKU, tutar, tarih, kod) | JetBrains Mono | `--lal-font-mono` |

Google Fonts üzerinden `base.html` `<head>` içinde yükleniyor.

### Renk sistemi özeti (`tokens.css`)

- **Nötr zemin/yüzey:** `--lal-bg`, `--lal-bg-elevated`, `--lal-surface`,
  `--lal-surface-2`, `--lal-surface-hover` (derinlik hissi için 5 kademe)
- **Marka/accent:** `--lal-accent` (kırmızı/bordo, "SoftHydra" kimliği) +
  hover/dim/glow varyantları
- **Semantik (yalnızca 3 anlam rengi):** `--lal-green` (pozitif/kâr),
  `--lal-red` (negatif/zarar), `--lal-amber` (uyarı/orta)
- **Pazaryeri etiketleri (marka rengi DEĞİL, bilgi etiketi):**
  `--lal-mp-trendyol` (turuncu `#F27A1A`), `--lal-mp-hepsiburada`
  (amber `#F5A623`) — semantik renklerle karıştırılmaz. Yeni pazaryeri
  eklenirse (Amazon, N11, Shopify...) buraya eklenir.

### Görsel kimlik / motifler

Genel Bakış sayfası (`ai-genel-bakis.html`) "kargo manifestosu" (waybill)
temasıyla tasarlandı: sıcak kraft kağıt tonları, monospace barkod/etiket
hissi, delikli kenar (perforasyon) motifleri, canlı sipariş akışı, TY/HB
pazaryeri rozetleri. Bu görsel dil diğer sayfalara da (örn. Ürünler kart
tasarımı) kademeli olarak taşınıyor — bkz. Sayfa Durumu.

### JS yapısı

```
static/js/shell.js       → HER sayfada yüklenir. Ortak biçimlendirme (fmtTL,
                            fmtNum, fmtPct, fmtDateShort...), tema toggle,
                            skeleton loading, senkron/mp-switch/range gibi
                            global yardımcılar. Sayfa-özel modüller bunun
                            tanımladığı 'lal:data-refresh' event'ini dinler.
static/js/alerts.js       → HER sayfada yüklenir, üst uyarı bandı
static/js/ui-effects.js   → HER sayfada yüklenir, genel UI efektleri
static/js/gosterge-paneli.js, urunler.js, siparisler.js,
static/js/finans-giderler.js, stock-settings.js,
static/js/ayarlar-credentials.js, payout-calendar.js
                          → sayfa-özel modüller, shell.js'in tanımladığı
                            global yardımcılara bağımlı — bu yüzden
                            base.html'de shell.js'ten SONRA yüklenmeli
                            ({% block page_scripts %} içinde)
```

`app.py`/blueprint route'ları `url_for()` çağrılarında blueprint-qualified
isim kullanır (örn. `url_for('dashboard_routes.urunler_page')`) — bare
endpoint adı blueprint refaktöründen sonra sessizce kırılır.

### Sayfa durumu

| Sayfa | Route | Durum |
|---|---|---|
| AI Genel Bakış | `/genel-bakis` (bkz. `ai-genel-bakis.html`) | ✅ Waybill temasıyla tamamlandı — masaüstü/mobil responsive |
| Gösterge Paneli | `/dashboard`, `/gosterge-paneli` | Mevcut, eski/legacy görsel dilde |
| Ürünler | `/urunler` | 🔧 Kart bazlı yeniden tasarım üzerinde çalışılıyor — Trendyol ürün görseli (ilk görsel), marj rozeti, tıklanınca açılan maliyet paneli. Tablo görünümü de korunacak/toggle olacak (karar aşamasında) |
| Siparişler | `/siparisler`, `/orders` | Mevcut, eski/legacy görsel dilde |
| Finans | `/finans` | Mevcut, eski/legacy görsel dilde |
| Stok | `/stok` | Mevcut, eski/legacy görsel dilde |
| Ayarlar | `/ayarlar` | Mevcut, eski/legacy görsel dilde |

> Not (Ürünler kart tasarımı — açık iş): Trendyol'dan ürün görseli
> senkron katmanında (`sync_core.py`/`trendyol_client.py`) **henüz
> çekilmiyor** ve DB şemasında (`product_costs`, `product_stock`,
> `order_lines`) görsel URL'si için bir kolon **yok**. Kart tasarımına
> geçerken: (1) Trendyol ürün/stok endpoint'inden `image` alanının
> gelip gelmediğini doğrula, (2) DB'ye `image_url` kolonu ekle (migration,
> `--dry-run`/`--apply` destekli), (3) `/api/product-performance`
> response'una bu alanı ekle, (4) frontend'de kart görselini bağla.

## Gereksinimler

- Python 3.10+ (CI 3.11 üzerinde çalışır)
- Redis (Celery broker/backend için)
- Trendyol Satıcı hesabı ve API kimlik bilgileri
- (Opsiyonel) Hepsiburada Merchant hesabı — canlı (PROD) API kimlik
  bilgileriyle test edilmiştir

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
| `PANEL_USERNAME` / `PANEL_PASSWORD` | Panele giriş için kullanıcı adı/şifre (HTTP Basic Auth) — `PROD` ortamında zorunludur |
| `PAYOUT_CREDENTIAL_KEY` | Kaydedilen pazaryeri kimlik bilgilerini şifrelemek için Fernet anahtarı (ilk çalıştırmada otomatik üretilip konsola basılır) |
| `REDIS_URL` | Celery broker/backend bağlantı adresi (ör. `redis://localhost:6379/0`) |
| `LOG_LEVEL` | Log seviyesi (varsayılan `INFO`) |

> `PROD` ortamında `PANEL_USERNAME`/`PANEL_PASSWORD` tanımlı değilse
> uygulama **fail-open olarak açılmaz**; başlatma reddedilir
> (`RuntimeError`, bkz. `app.py`).

### Çalıştırma

Uygulama (Flask):

```bash
python app.py
```

Panel varsayılan olarak **`http://127.0.0.1:5050`** üzerinde açılır
(`app.run(..., port=5050)` — bkz. `app.py`).

Celery worker (yalnızca zamanlanmış görevler ve payout scraping için
gerekli — manuel senkron butonu Celery'siz de çalışır):

```bash
celery -A celery_app worker --loglevel=info
```

Celery Beat (gece 03:00 otomatik senkron için):

```bash
celery -A celery_app beat --loglevel=info
```

## Kullanılabilir Sayfalar

| Yol | Açıklama |
|---|---|
| `/dashboard`, `/gosterge-paneli` | Genel gösterge paneli |
| `/genel-bakis` | AI Genel Bakış (waybill temalı özet) |
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
noktalar `/api/` altında sunulur (tam liste için ilgili `blueprints/*.py`
dosyasındaki route tanımlarına bakın — route'lar `app.py`'den
blueprint'lere taşındı).

## Testler

```bash
pytest
```

CI: GitHub Actions (`.github/workflows/tests.yml`), Python 3.11 + Redis 7
servisiyle her push/PR'da çalışır. Test paketi; finans motoru
hesaplamaları (komisyon/kargo/iade/KDV kuralları, payout calendar
mantığı), veritabanı migration'ları, harici payout DB'si ve HTTP
istemcisi için kapsamlı senaryolar içerir (`tests/`).

## Bilinen Kısıtlar / Açık İşler

- **Celery Beat üretimde kalıcı servis olarak koşmuyor** — gece 03:00
  otomatik senkron, worker/beat process'leri elle/manuel başlatılmadığı
  sürece çalışmaz. Kalıcı servis haline getirilmesi (systemd/launchd/
  supervisor vb.) açık bir iş.
- **Ürünler sayfası kart yeniden tasarımı** devam ediyor — bkz. Sayfa
  Durumu tablosundaki not (Trendyol ürün görseli DB'de henüz yok).
- Diğer sayfalar (Gösterge Paneli, Siparişler, Finans, Stok, Ayarlar)
  henüz LDL waybill görsel diline taşınmadı.

## Proje Notları

- `DEGISIKLIKLER.md` — önceki bir denetimde tespit edilen maddelerin
  uygulanma özetini içerir (credential şifreleme, Celery'ye geçiş, panel
  auth fail-open düzeltmesi vb.).
- `KALAN_ADIMLAR.md` — gerçek Hepsiburada kimlik bilgileriyle elle
  doğrulanması gereken adımları listeler.

## Çalışma Kuralları / Konvansiyonlar

- Davranışsal düzeltmeler (bugfix) ile yapısal refaktörler **ayrı
  commit'lerde** yapılır — git geçmişi temiz/incelenebilir kalsın diye.
- Tek seferlik DB migration script'leri `--dry-run` ve `--apply`
  modlarını destekler (bkz. `migrations/`); DB'yi değiştiren işlemler
  için her zaman açık onay istenir.
- Her düzeltmeye eşlik eden regresyon testi eklenir; push öncesi test
  paketi tam yeşil olmalı.
- Blueprint endpoint'lerine `url_for()` ile referans verirken blueprint
  adı da belirtilir (`blueprint_adı.endpoint_adı`) — bare isim sessizce
  kırılır.
- Trendyol API epoch-ms alanlarını **Europe/Istanbul yerel saatinde**
  kodluyor (gerçek UTC değil); Hepsiburada doğru şekilde UTC veriyor.
  Bu normalize edilmeden kullanılırsa sipariş kaybı/zaman kayması olur
  — normalize noktası: `normalize_trendyol_epoch_ms()`.

## Güvenlik Notları

- `.env`, `*.db` ve `.payout_credential.key` dosyaları `.gitignore`
  içinde hariç tutulmuştur — **asla commit etmeyin**.
- Kaydedilen pazaryeri kimlik bilgileri (JWT/Cookie) Fernet ile
  şifrelenir; anahtar kaybolursa eski kayıtlar okunamaz hale gelir
  (veri kaybı olmaz, formdan yeniden girilmesi gerekir).

## Lisans

Bu depo için henüz bir lisans belirtilmemiştir.
