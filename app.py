"""
Trendyol Günlük Satış Paneli + Kâr/Zarar Paneli
--------------------------------------------------
Trendyol Partner (Satıcı) API'sinden sipariş, iade ve finans verilerini çekip
satış özeti ve gerçek kâr/zarar hesaplaması gösteren local Flask uygulaması.

Resmi API dokümantasyonu:
https://developers.trendyol.com/docs/sipariş-paketlerini-çekme-getshipmentpackages
https://developers.trendyol.com/docs/2-authorization
https://developers.trendyol.com/docs/cari-hesap-ekstresi-entegrasyonu

Hepsiburada entegrasyonu (SIT/test aşamasında):
https://developers.hepsiburada.com/hepsiburada/reference/get_orders-merchantid-merchantid
https://developers.hepsiburada.com/hepsiburada/reference/get_packages-merchantid-merchantid

Çalıştırmadan önce .env dosyasını doldurmanız gerekir (bkz. .env.example).
"""

import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, Response

from http_client import get_json_with_retry
from database import (
    delete_fixed_expense,
    fail_sync_progress,
    finish_sync_progress,
    get_connection,
    get_sync_progress,
    get_sync_state,
    init_db,
    list_fixed_expenses,
    set_sync_state,
    start_sync_progress,
    update_sync_progress,
    upsert_cargo_costs,
    upsert_fixed_expense,
    upsert_order_lines,
    upsert_orders,
    upsert_other_financials,
    upsert_settlements,
)
from finance_engine import best_sellers as compute_best_sellers
from finance_engine import compute_profit_summary
from finance_engine import monthly_profit as compute_monthly_profit
from trendyol_finance import sync_finance_data
from sync_lock import acquire_sync_lock, release_sync_lock, sync_lock_status
# NOT (28.07.2026 mimari düzeltmesi): profit_engine.py + hb_profit_engine.py
# TEK bir finance_engine.py ile değiştirildi. Sebep: iki motor, aynı
# ekonomik olay için (özellikle Hepsiburada satışları) FARKLI "revenue"
# tanımları kullanıyordu ve marketplace='all' asla marketplace='trendyol'
# + marketplace='hepsiburada' toplamına eşit olmuyordu. Ayrıca eski
# hb_profit_engine.py, henüz settlement'ı oluşmamış (InTransit) siparişler
# için gelir=0 gösterip tam maliyeti düşüyordu (bkz. denetim notu) — bu artık
# TY'deki gibi tahmini gelir ile düzeltiliyor. Eski dosyalar referans için
# saklanabilir ama artık hiçbir route tarafından import edilmiyor.

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("trendyol_satis")

SUPPLIER_ID = os.getenv("TRENDYOL_SUPPLIER_ID", "").strip()
API_KEY = os.getenv("TRENDYOL_API_KEY", "").strip()
API_SECRET = os.getenv("TRENDYOL_API_SECRET", "").strip()
ENV = os.getenv("TRENDYOL_ENV", "PROD").strip().upper()  # PROD veya STAGE
INTEGRATOR_NAME = os.getenv("TRENDYOL_INTEGRATOR_NAME", "SelfIntegration").strip()

# "Tüm Zamanlar" senkronizasyonunun başlangıç noktası. Trendyol mağazanızın
# gerçek açılış tarihini .env'de TRENDYOL_DATA_START_DATE=YYYY-MM-DD olarak
# belirtmezseniz, güvenli bir varsayım olarak 3 yıl öncesi kullanılır.
_DEFAULT_START = os.getenv("TRENDYOL_DATA_START_DATE", "").strip()
try:
    DATA_START_DATE = datetime.strptime(_DEFAULT_START, "%Y-%m-%d") if _DEFAULT_START else (datetime.now() - timedelta(days=365 * 3))
except ValueError:
    DATA_START_DATE = datetime.now() - timedelta(days=365 * 3)

BASE_URL = (
    "https://apigw.trendyol.com"
    if ENV == "PROD"
    else "https://stageapigw.trendyol.com"
)

# Trendyol dokümantasyonuna göre User-Agent zorunlu:
# "{SatıcıId} - {EntegratörFirmaAdı}" ya da kendi yazılımınızsa "{SatıcıId} - SelfIntegration"
USER_AGENT = f"{SUPPLIER_ID} - {INTEGRATOR_NAME}"

app = Flask(__name__)
init_db()

from blueprints.cost_routes import bp as cost_routes_bp  # noqa: E402 (init_db'den sonra olmalı)
app.register_blueprint(cost_routes_bp)

from blueprints.stock_routes import bp as stock_routes_bp  # noqa: E402
app.register_blueprint(stock_routes_bp)

from blueprints.payout_routes import bp as payout_routes_bp  # noqa: E402
app.register_blueprint(payout_routes_bp)

# ============================================================
# ERİŞİM KONTROLÜ (HTTP Basic Auth)
# ============================================================
# Panel artık Celery/Redis ile arka planda sürekli çalıştığından, sadece
# "yerel makinemde çalışıyor" varsayımına güvenmek yeterli değil. .env'de
# PANEL_USERNAME + PANEL_PASSWORD tanımlıysa tüm route'lar bu bilgiyle
# korunur. STAGE/yerel geliştirmede tanımlı değilse (opsiyonel) auth devre
# dışı kalır ve başlangıçta uyarı basılır.
#
# GÜVENLİK DÜZELTMESİ: Önceden bu davranış PROD ortamında da "fail-open"dı
# — yani TRENDYOL_ENV=PROD olsa bile PANEL_USERNAME/PASSWORD unutulursa
# panel şifresiz internete açılabiliyordu (sadece bir log satırı uyarıyordu,
# uygulama yine de başlıyordu). Artık PROD'da bu kombinasyon EKSİKSE
# uygulama kasıtlı olarak başlamayı reddediyor (RuntimeError) — "sessizce
# şifresiz açık kalmak" yerine "gürültülü şekilde hiç açılmamak" tercih
# edildi, çünkü ilki fark edilmeden aylarca sürebilir.
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "").strip()
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "").strip()
_AUTH_ENABLED = bool(PANEL_USERNAME and PANEL_PASSWORD)

if ENV == "PROD" and not _AUTH_ENABLED:
    raise RuntimeError(
        "TRENDYOL_ENV=PROD iken PANEL_USERNAME ve PANEL_PASSWORD .env dosyasında "
        "tanımlı olmalı — aksi halde panel internete şifresiz açık kalır. "
        "Sadece yerel geliştirme için TRENDYOL_ENV=STAGE kullanın (bu kontrolü atlar)."
    )


def _auth_ok(auth):
    if not auth or not auth.username or not auth.password:
        return False
    # secrets.compare_digest: zamanlama saldırısına karşı sabit-zamanlı karşılaştırma.
    user_ok = secrets.compare_digest(auth.username, PANEL_USERNAME)
    pass_ok = secrets.compare_digest(auth.password, PANEL_PASSWORD)
    return user_ok and pass_ok


@app.before_request
def _require_auth():
    if not _AUTH_ENABLED:
        return None
    auth = request.authorization
    if not _auth_ok(auth):
        return Response(
            "Bu panele erişmek için kullanıcı adı/parola gerekli.",
            401,
            {"WWW-Authenticate": 'Basic realm="Trendyol Satis Paneli"'},
        )
    return None

# NOT (05.08.2026 mimari düzeltme — Celery'den geri dönüş): Manuel
# senkronizasyon tetiklemeleri artık Celery task kuyruğu YERİNE Flask
# süreci içinde bir arka plan thread'inde çalışıyor. Sebep: panel tek
# kullanıcılı/yerel bir araç, ayrı bir "celery -A celery_app worker"
# terminali açık tutmayı unutmak (ya da worker'ın çökmüş olması)
# senkronizasyonun 503 ile başarısız olmasına yol açıyordu — kullanıcı
# için bu, worker'ı ayrı ayakta tutmanın getirdiği faydadan (süreç
# restart'ına dayanıklılık) daha maliyetliydi.
#
# BİLİNÇLİ OLARAK KABUL EDİLEN RİSK: Flask süreci (python3 app.py) bir
# senkronizasyon devam ederken çöker/yeniden başlarsa, o senkron sessizce
# yarıda kesilir ve sync_progress tablosu "running" durumunda takılı
# kalabilir (bir sonraki senkronizasyon denemesi zaten bu durumu
# günceller, ama arada panel yanlışlıkla "devam ediyor" gösterebilir).
# Tek kullanıcılı/yerel kullanım için bu risk küçük ve kabul edildi.
#
# Kilit hâlâ Redis üzerinden (bkz. sync_lock.py) — Flask süreci içinde
# birden fazla thread aynı anda tetiklenirse (örn. kullanıcı butona iki
# kez basarsa) birbirinin üzerine yazmasınlar diye. Celery Beat'in
# zamanlanmış görevleri (celery_app.py/tasks.py — 20 dakikalık otomatik
# senkron, gece mutabakatı, HB backfill) buna DOKUNULMADI: hâlâ tanımlı
# duruyorlar, sadece siz ayrıca bir Celery worker/beat çalıştırmadığınız
# sürece tetiklenmiyorlar. İleride otomatik/periyodik senkron isterseniz
# worker'ı ayrıca ayağa kaldırmanız yeterli, bu değişiklik onu engellemez.
def _run_sync_in_thread(name, target_fn, *args, **kwargs):
    """Senkronizasyonu Flask sürecinin içinde arka plan thread'inde
    çalıştırır (Celery worker gerektirmez). Kilidi thread'in kendisi alıp
    bırakır — böylece aynı anda iki senkron aynı DB'ye yazmaz."""
    def runner():
        token = acquire_sync_lock(name)
        if token is None:
            return
        try:
            target_fn(*args, **kwargs)
        finally:
            release_sync_lock(token, name)
    threading.Thread(target=runner, daemon=True).start()

# ============================================================
# HEPSİBURADA ENTEGRASYONU
# ============================================================
# NOT (25.07.2026 güncellemesi): /orders/merchantid/{id} endpoint'i sadece
# "Paketlenecek" kuyruğundaki (ödemesi tamamlanmış ama henüz paketlenmemiş)
# siparişleri dönüyordu — Merchant Panel'de "Gönderime hazır" ve "Kargoda"
# durumundaki siparişler bu listede hiç görünmüyor, panelde sadece 1 sipariş
# gözükmesinin sebebi buydu. Bunun yerine /packages/merchantid/{id} kullanılıyor:
# bu endpoint paketlenmiş TÜM paketleri (Open/gönderime hazır, kargoya
# verilmiş, teslim edilmiş vb. durumdakiler) begindate/enddate aralığında
# döner — Trendyol'daki getShipmentPackages'ın karşılığı budur.
#
# ÖNEMLİ (25.07.2026 güncellemesi): Alan adları artık canlı yanıtla doğrulandı
# (bkz. fetch_all_hb_packages docstring'i). begindate/enddate bu endpoint'te
# çalışmıyor — timespan (saat) parametresi kullanılıyor, sonra istenen tarih
# aralığına client-side filtreleniyor.

HB_MERCHANT_ID = os.getenv("HEPSIBURADA_MERCHANT_ID", "").strip()
HB_USERNAME = os.getenv("HEPSIBURADA_USERNAME", "").strip()
HB_PASSWORD = os.getenv("HEPSIBURADA_PASSWORD", "").strip()
HB_ENV = os.getenv("HEPSIBURADA_ENV", "SIT").strip().upper()  # SIT (test) veya PROD
HB_USER_AGENT = os.getenv("HEPSIBURADA_USER_AGENT", "softhydra_dev").strip()

HB_BASE_URL = (
    "https://oms-external.hepsiburada.com"
    if HB_ENV == "PROD"
    else "https://oms-external-sit.hepsiburada.com"
)
HB_PACKAGES_PATH = f"/packages/merchantid/{HB_MERCHANT_ID}"
_HB_DEBUG_LOGGED = False  # ilk senkronda ham response'u bir kez konsola basmak için

# NOT (27.07.2026 güncellemesi): /packages/merchantid/{id} (üstteki HB_PACKAGES_PATH)
# resmi dokümana göre SADECE "Open" statüsündeki (henüz kargoya verilmemiş, paketlenmeyi
# bekleyen) paketleri döner -- status parametresi gönderilse de gönderilmese de fark
# etmiyor, endpoint zaten sadece açık kuyruğa bakıyor. Web sitesinde görünen ama panelde
# hiç görünmeyen satışlar bu yüzden eksikti: hepsi zaten kargoya verilmiş/tamamlanmış
# paketlerdi. Kargoya verilmiş paketler için AYRI bir endpoint var:
#   GET /packages/merchantid/{id}/shipped  -> sadece PackageNumber/OrderNumber/ShippedDate
#   (canlı yanıtla doğrulandı, 27.07.2026) -- ürün/fiyat detayı İÇERMİYOR.
# Asıl ürün/fiyat detayını almak için her OrderNumber için ayrıca:
#   GET /orders/merchantid/{id}/ordernumber/{orderNumber}
# çağırmak gerekiyor (canlı yanıtla doğrulandı, 27.07.2026 -- items[] içinde name/sku/
# quantity/unitPrice/totalPrice/hbDiscount/merchantDiscount/vat/vatRate/status var).
HB_SHIPPED_PATH = f"/packages/merchantid/{HB_MERCHANT_ID}/shipped"
HB_DELIVERED_PATH = f"/packages/merchantid/{HB_MERCHANT_ID}/delivered"
HB_ORDER_DETAIL_PATH_TMPL = f"/orders/merchantid/{HB_MERCHANT_ID}/ordernumber/{{order_number}}"
_HB_SHIPPED_DEBUG_LOGGED = False
_HB_DELIVERED_DEBUG_LOGGED = False

# --- HB Muhasebe/Finans API (Kayıt Bazlı Muhasebe Servisi) ---
# https://developers.hepsiburada.com/hepsiburada/reference/get_transactions-merchantid-merchantid
# Ayrı bir host: mpfinance-external(-sit).hepsiburada.com. ŞEMA HENÜZ CANLI YANITLA
# DOĞRULANMADI (dokümantasyon sayfası örnek response içermiyor, sadece query
# parametreleri ve TransactionTypes enum'unu veriyor) -- /packages'de olduğu gibi
# ilk çağrının ham JSON'u debug için basılacak, alan adları gerekirse düzeltilecek.
HB_FINANCE_BASE_URL = (
    "https://mpfinance-external.hepsiburada.com"
    if HB_ENV == "PROD"
    else "https://mpfinance-external-sit.hepsiburada.com"
)
HB_TRANSACTIONS_PATH = f"/transactions/merchantid/{HB_MERCHANT_ID}"
_HB_FINANCE_DEBUG_LOGGED = False

# Trendyol'unkinden ayrı kilit adı ('hepsiburada') — ikisi aynı anda çalışabilir.


def _check_hb_credentials():
    if not (HB_MERCHANT_ID and HB_USERNAME and HB_PASSWORD):
        return (
            "Hepsiburada API bilgileri eksik. .env dosyasını HEPSIBURADA_MERCHANT_ID, "
            "HEPSIBURADA_USERNAME ve HEPSIBURADA_PASSWORD değerleriyle doldurun."
        )
    return None


def hepsiburada_get(path, params=None, max_retries=5, throttle_seconds=0.05):
    """Hepsiburada API'ye GET isteği atar. User-Agent header'ı zorunlu.
    Rate limiti Trendyol'dan çok daha yüksek olduğu için throttle küçük tutuldu."""
    return get_json_with_retry(
        f"{HB_BASE_URL}{path}", params=params, headers={"User-Agent": HB_USER_AGENT},
        auth=(HB_USERNAME, HB_PASSWORD), timeout=30, max_retries=max_retries,
        throttle_seconds=throttle_seconds, backoff_mode="header_or_linear",
        backoff_base_seconds=2, retry_wait_header="X-RateLimit-Reset",
    )


def hepsiburada_finance_get(path, params=None, max_retries=5, throttle_seconds=0.05):
    """HB Muhasebe/Finans API'sine (mpfinance-external host) GET isteği atar.
    Auth ve User-Agent /packages ile aynı (aynı satıcı hesabı), sadece host farklı."""
    return get_json_with_retry(
        f"{HB_FINANCE_BASE_URL}{path}", params=params, headers={"User-Agent": HB_USER_AGENT},
        auth=(HB_USERNAME, HB_PASSWORD), timeout=30, max_retries=max_retries,
        throttle_seconds=throttle_seconds, backoff_mode="header_or_linear",
        backoff_base_seconds=2, retry_wait_header="X-RateLimit-Reset",
    )


def fetch_all_hb_packages(start_dt, end_dt, status=None):
    """/packages/merchantid/{id} — ŞEMA CANLI YANITLA DOĞRULANDI (25.07.2026).

    ÖNEMLİ KEŞİF: begindate/enddate parametreleri bu endpoint'te sessizce hiçbir
    şey döndürmüyor (aynı hesap/tarih aralığında /orders veri dönerken /packages
    boş [] dönüyordu). Bunun yerine Hepsiburada'nın kendi örneklerinde kullanılan
    "timespan" (saat cinsinden, "şu andan N saat öncesine kadar") parametresi
    çalışıyor. Bu yüzden start_dt/end_dt'yi "şimdiden kaç saat önce" hesabına
    çevirip timespan olarak gönderiyoruz, sonra istenen aralığa client-side
    filtreliyoruz (timespan "şimdi"den geriye saydığı için end_dt gelecekte
    değilse zaten fazladan veri gelmez, ama yine de güvenlik için filtreliyoruz).

    Yanıt zarfı da /orders'tan farklı: düz bir LİSTE döner (obje/"items" değil).
    Her eleman bir PAKET'tir (id, status, packageNumber, orderDate, customerName,
    cargoCompany, totalPrice:{amount}, items:[{lineItemId, hbSku, merchantSku,
    productName, quantity, price:{amount}, commissionRate, totalHBDiscount:{amount},
    totalMerchantDiscount:{amount}, orderNumber, ...}, ...]). Yani /orders'ın
    aksine burada gerçek bir "paket" objesi var ve kalemler onun "items" alanında
    iç içe — grup birleştirmeye gerek yok, HB zaten paket bazında gruplamış."""
    global _HB_DEBUG_LOGGED
    hours = max(1, int((datetime.now() - start_dt).total_seconds() // 3600) + 1)
    logger.debug(f"[HB DEBUG] istenen timespan (saat): {hours} (start_dt={start_dt})")

    all_items = []
    offset = 0
    limit = 100

    while True:
        params = {"timespan": hours, "limit": limit, "offset": offset}
        if status:
            params["status"] = status

        data = hepsiburada_get(HB_PACKAGES_PATH, params)
        items = data if isinstance(data, list) else ((data or {}).get("items") or [])
        logger.debug(f"[HB DEBUG] offset={offset} -> {len(items)} paket döndü")

        if not _HB_DEBUG_LOGGED and items:
            import json as _json
            logger.debug("=" * 60)
            logger.debug("HEPSİBURADA /packages HAM YANIT — İLK ELEMAN (teşhis için):")
            logger.debug(_json.dumps(items[0], ensure_ascii=False, indent=2, default=str)[:4000])
            logger.debug("=" * 60)
            _HB_DEBUG_LOGGED = True

        all_items.extend(items)
        if len(items) < limit:
            break
        offset += limit

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    filtered = []
    for it in all_items:
        od = _hb_iso_to_epoch_ms(it.get("orderDate"))
        if od is None or (start_ms <= od <= end_ms):
            filtered.append(it)
    return filtered


def _fetch_hb_packages_by_lifecycle_endpoint(path, start_dt, end_dt, label,
                                              default_status, order_detail_cache,
                                              debug_state, failed_order_numbers=None):
    """/packages/merchantid/{id}/{shipped|delivered} gibi statü-bazlı listeleme
    endpoint'lerinin ortak mantığı: PackageNumber/OrderNumber listesini çeker,
    her benzersiz OrderNumber için /orders/.../ordernumber/{n} ile ürün/fiyat
    detayını alıp fetch_all_hb_packages ile aynı paket şekline dönüştürür.

    order_detail_cache çağıranlar arasında paylaşılır (örn. hem shipped hem
    delivered aynı OrderNumber'a değinirse sipariş detayı sadece bir kez çekilir).
    NOT: HB dokümantasyonuna göre bu tür statü-bazlı listeleme endpoint'leri
    (shipped/delivered/undelivered/cancelled) sadece SON 1 AYLIK veriye erişim
    veriyor -- timespan büyük gönderilse bile daha eski kayıtlar dönmüyor.

    failed_order_numbers: verilirse, sipariş detayı ÇEKİLEMEYEN (401/404/ağ
    hatası vb.) OrderNumber'lar bu set'e eklenir. DÜZELTME: önceden bu hata
    sadece debug log'a düşüp tamamen sessizce yutuluyordu — sync sonucu
    kullanıcıya "tamamlandı" olarak dönerken kaç siparişin ürün/fiyat detayı
    eksik kaldığı hiçbir yerde görünmüyordu. Artık sync_hb_packages_to_db bu
    sayıyı ilerleme mesajına ekliyor."""
    hours = max(1, int((datetime.now() - start_dt).total_seconds() // 3600) + 1)
    logger.debug(f"[HB {label} DEBUG] istenen timespan (saat): {hours} (start_dt={start_dt})")

    records = []
    offset = 0
    limit = 100
    while True:
        params = {"timespan": hours, "limit": limit, "offset": offset}
        data = hepsiburada_get(path, params)
        items = data if isinstance(data, list) else ((data or {}).get("items") or [])
        logger.debug(f"[HB {label} DEBUG] offset={offset} -> {len(items)} paket döndü")

        if not debug_state["logged"] and items:
            import json as _json
            logger.debug("=" * 60)
            logger.debug(f"HEPSİBURADA {path} HAM YANIT — İLK ELEMAN (teşhis için):")
            logger.debug(_json.dumps(items[0], ensure_ascii=False, indent=2, default=str)[:4000])
            logger.debug("=" * 60)
            debug_state["logged"] = True

        records.extend(items)
        if len(items) < limit:
            break
        offset += limit

    packages = []
    for rec in records:
        order_number = (rec.get("OrderNumber") or rec.get("orderNumber")
                         or (rec.get("OrderNumbers") or rec.get("orderNumbers") or [None])[0])
        package_number = rec.get("PackageNumber") or rec.get("packageNumber")
        if not order_number or not package_number:
            continue

        if order_number not in order_detail_cache:
            try:
                detail = hepsiburada_get(HB_ORDER_DETAIL_PATH_TMPL.format(order_number=order_number))
            except requests.RequestException as e:
                logger.debug(f"[HB {label} DEBUG] sipariş detayı alınamadı (orderNumber={order_number}): {e}")
                if failed_order_numbers is not None:
                    failed_order_numbers.add(order_number)
                detail = None
            order_detail_cache[order_number] = detail
        detail = order_detail_cache[order_number]
        if not detail:
            continue

        raw_items = detail.get("items") or []
        mapped_items = []
        for it in raw_items:
            mapped_items.append({
                "hbSku": it.get("sku"),
                "merchantSku": it.get("merchantSKU") or it.get("merchantSku"),
                "productName": it.get("name"),
                "quantity": it.get("quantity"),
                "price": it.get("unitPrice"),
                "commissionRate": it.get("commissionRate"),
                "totalHBDiscount": (it.get("hbDiscount") or {}).get("totalPrice"),
                "totalMerchantDiscount": (it.get("merchantDiscount") or {}).get("totalPrice"),
                "orderNumber": order_number,
            })

        first_item_status = raw_items[0].get("status") if raw_items else None
        packages.append({
            "packageNumber": package_number,
            "orderNumber": order_number,
            "orderDate": detail.get("orderDate"),
            "status": first_item_status or default_status,
            "customerName": (detail.get("customer") or {}).get("name"),
            "cargoCompany": None,
            "items": mapped_items,
        })

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    return [pkg for pkg in packages
            if (od := _hb_iso_to_epoch_ms(pkg.get("orderDate"))) is None or (start_ms <= od <= end_ms)]


_HB_SHIPPED_DEBUG_STATE = {"logged": False}
_HB_DELIVERED_DEBUG_STATE = {"logged": False}


def fetch_all_hb_shipped_packages(start_dt, end_dt, order_detail_cache, failed_order_numbers=None):
    """Kargoya verilmiş (henüz teslim edilmemiş) paketler."""
    return _fetch_hb_packages_by_lifecycle_endpoint(
        HB_SHIPPED_PATH, start_dt, end_dt, "SHIPPED", "Shipped",
        order_detail_cache, _HB_SHIPPED_DEBUG_STATE, failed_order_numbers,
    )


def fetch_all_hb_delivered_packages(start_dt, end_dt, order_detail_cache, failed_order_numbers=None):
    """Müşteriye teslim edilmiş paketler -- HB'de /shipped'ten AYRI bir liste;
    bir paket teslim edilince artık /shipped'te görünmüyor, sadece burada
    görünüyor. Web sitesinde görünüp panelde hiç görünmeyen satışların çoğu
    büyük ihtimalle buradaki (zaten teslim edilmiş) paketlerdi."""
    return _fetch_hb_packages_by_lifecycle_endpoint(
        HB_DELIVERED_PATH, start_dt, end_dt, "DELIVERED", "Delivered",
        order_detail_cache, _HB_DELIVERED_DEBUG_STATE, failed_order_numbers,
    )


def _hb_money(value):
    """Hepsiburada para alanını float'a çevirir.

    Desteklenen formatlar (canlı yanıtlarla doğrulandı, 25.07.2026):
      /packages:     {"currency": "TRY", "amount": 799.89}
      /transactions: {"value": -94.20, "currencyCode": "949"}
      düz sayı / string: 94.2, "94.2"

    amount.value ZATEN İŞARETLİ gelir (negatif = gider, pozitif = gelir/iade);
    bu fonksiyon işareti değiştirmez, yalnızca düz float üretir.
    SQLite 'Error binding parameter - unsupported type' hatasını önlemek için
    dict'leri asla olduğu gibi bırakmaz.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        if "value" in value:
            value = value["value"]
        elif "amount" in value:
            value = value["amount"]
        else:
            return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hb_iso_to_epoch_ms(iso_str):
    """HB tarihleri '2026-07-24T15:36:20' veya '...20.549' (ms'li) formatında
    ISO string olarak gelir; Trendyol ile aynı epoch-ms tam sayıya çevirir."""
    if not iso_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(datetime.strptime(iso_str, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _hb_line_rows_for(package_id, obj):
    """Bir paket objesinden satır listesi üretir. Gerçek şemada kalemler
    her zaman paketin "items" alanında iç içe gelir (bkz. fetch_all_hb_packages)."""
    nested = obj.get("items") if isinstance(obj.get("items"), list) else None
    source_lines = nested if nested is not None else [obj]
    rows = []
    for ln in source_lines:
        rows.append({
            "shipment_package_id": package_id,
            "marketplace": "hepsiburada",
            "barcode": ln.get("hbSku") or ln.get("productBarcode"),
            "merchant_sku": ln.get("merchantSku") or ln.get("merchantSKU"),
            "product_name": ln.get("productName") or ln.get("name"),
            "quantity": ln.get("quantity"),
            "line_unit_price": _hb_money(ln.get("price") or ln.get("unitPrice")),
            "commission_rate": ln.get("commissionRate"),
        })
    return rows, source_lines


def sync_hb_packages_to_db(start_dt, end_dt, progress_cb=None):
    """orders/order_lines tablolarına marketplace='hepsiburada' ile yazar.
    /packages/merchantid/{id} paketlenmiş TÜM paketleri (Open/gönderime hazır,
    kargoda, teslim edildi vb.) döner — eski /orders endpoint'i sadece
    "Paketlenecek" kuyruğunu döndüğü için panelde sadece 1 sipariş görünüyordu.

    Alan eşlemesi 25.07.2026'da canlı yanıtla doğrulandı, bkz. fetch_all_hb_packages
    ve _hb_line_rows_for docstring'leri."""
    if progress_cb:
        progress_cb("Hepsiburada paketleri çekiliyor…")

    open_items = fetch_all_hb_packages(start_dt, end_dt)

    order_detail_cache = {}  # shipped ve delivered arasında paylaşılır
    failed_order_numbers = set()  # sipariş detayı çekilemeyen OrderNumber'lar (bkz. docstring)

    if progress_cb:
        progress_cb("Hepsiburada kargolanmış siparişleri çekiliyor…")
    shipped_items = fetch_all_hb_shipped_packages(start_dt, end_dt, order_detail_cache, failed_order_numbers)

    if progress_cb:
        progress_cb("Hepsiburada teslim edilmiş siparişleri çekiliyor…")
    delivered_items = fetch_all_hb_delivered_packages(start_dt, end_dt, order_detail_cache, failed_order_numbers)

    # Üç kaynağı da packageNumber bazında tekilleştirip birleştiriyoruz -- bir
    # paket teorik olarak sadece bir statüde (Open / Shipped / Delivered) olur,
    # ama garanti olsun diye en "ileri" statü (delivered > shipped > open)
    # öncelikli tutuluyor.
    merged_by_package = {}
    for it in open_items:
        pid = it.get("packageNumber") or it.get("id")
        if pid is not None:
            merged_by_package[pid] = it
    for it in shipped_items:
        pid = it.get("packageNumber")
        if pid is not None:
            merged_by_package[pid] = it
    for it in delivered_items:
        pid = it.get("packageNumber")
        if pid is not None:
            merged_by_package[pid] = it
    items = list(merged_by_package.values())
    logger.debug(f"[HB DEBUG] birleştirilmiş toplam paket sayısı: {len(items)} "
          f"(open={len(open_items)}, shipped={len(shipped_items)}, delivered={len(delivered_items)})")

    order_rows = []
    line_rows = []

    for it in items:
        raw_pid = it.get("packageNumber") or it.get("id")
        if raw_pid is None:
            continue
        try:
            package_id = int(raw_pid)
        except (TypeError, ValueError):
            package_id = raw_pid

        lines, source_lines = _hb_line_rows_for(package_id, it)
        line_rows.extend(lines)

        gross = _hb_money(it.get("totalPrice"))
        if gross is None:
            gross = sum((_hb_money(ln.get("price") or ln.get("unitPrice")) or 0) * (ln.get("quantity") or 1)
                         for ln in source_lines)

        # Paket seviyesinde ayrı bir indirim alanı yok — HB ve satıcı indirimleri
        # her kalemin totalHBDiscount / totalMerchantDiscount alanında geliyor.
        discount = sum(
            (_hb_money(ln.get("totalHBDiscount")) or 0) + (_hb_money(ln.get("totalMerchantDiscount")) or 0)
            for ln in source_lines
        )
        net = gross - discount

        # orderNumber paket objesinin kendisinde değil, kalemlerin içinde geliyor.
        order_number = it.get("orderNumber")
        if not order_number and source_lines:
            order_number = source_lines[0].get("orderNumber")

        order_rows.append({
            "shipment_package_id": package_id,
            "marketplace": "hepsiburada",
            "order_number": order_number,
            "order_date": _hb_iso_to_epoch_ms(it.get("orderDate") or it.get("packageDate")),
            "status": it.get("status") or it.get("packageStatus"),
            "customer": it.get("customerName"),
            "cargo_provider": it.get("cargoCompany"),
            "gross_amount": gross,
            "discount_amount": discount,
            "net_amount": net,
        })

    upsert_orders(order_rows)
    upsert_order_lines(line_rows)
    return len(order_rows), len(line_rows), len(failed_order_numbers)


# --- 28.07.2026 (Faz 3): HB "settlement-only" paketler için gerçek quantity backfill ---
# GEREKÇE: /packages/merchantid/{id} sadece "Open" kuyruğunu döndüğü için, teslim
# edilmiş GEÇMİŞ HB siparişleri order_lines'a hiç girmeyebiliyor. finance_engine.py
# bu durumda quantity=1 VARSAYIYOR (bkz. finance_engine.py "BACKFILL NOTU") — bu,
# birden fazla adet satılmış ürünlerde kâr hesabını kalıcı olarak yanlış yapabilir.
# Bu fonksiyon, settlements'ta olup order_lines'ta OLMAYAN her HB paketi için
# /orders/merchantid/{id}/ordernumber/{orderNumber} adresinden gerçek quantity/fiyat
# bilgisini çeker ve order_lines'ı geriye dönük doldurur.
#
# ÖNEMLİ (dürüstlük notu): Bu fonksiyon canlı HB kimlik bilgileriyle TEST EDİLMEDİ
# (bu ortamda Hepsiburada'nın API'sine ağ erişimi yok). /orders/.../ordernumber/{n}
# endpoint'inin yanıt şemasının paket detayıyla (items[] içinde name/sku/quantity/
# unitPrice) aynı olduğu app.py'deki önceki dokümantasyon notuna dayanıyor (25.07.2026
# tarihli, canlı yanıtla doğrulanmış). Prod'a almadan önce küçük bir örneklemle
# (limit=3-5) manuel doğrulama yapılması önerilir.
def find_hb_settlement_only_packages(limit=None):
    """settlements'ta olup order_lines'ta karşılığı olmayan (marketplace, shipment_package_id,
    order_number) üçlülerini döndürür — backfill kuyruğu budur."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT s.shipment_package_id, s.order_number
            FROM settlements s
            WHERE s.marketplace = 'hepsiburada'
              AND s.shipment_package_id IS NOT NULL
              AND s.order_number IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM order_lines ol
                  WHERE ol.marketplace = 'hepsiburada'
                    AND ol.shipment_package_id = s.shipment_package_id
              )
            ORDER BY s.shipment_package_id
        """).fetchall()
    pairs = [(r["shipment_package_id"], r["order_number"]) for r in rows]
    return pairs[:limit] if limit else pairs


def backfill_hb_settlement_only_quantities(limit=50, progress_cb=None):
    """Kuyruktaki paketlerden en fazla `limit` tanesini işler (rate-limit'e
    takılmamak için KADEMELİ kullanım amaçlanmıştır — tek seferde tüm geçmişi
    çekmeye ÇALIŞMAYIN; bu fonksiyonu periyodik olarak, örn. /api/backfill-hb-quantities
    üzerinden küçük partiler halinde tekrar tekrar çağırın, kuyruk boşalana kadar).

    Returns: {"processed": int, "updated_lines": int, "failed": int, "remaining": int}
    """
    pairs = find_hb_settlement_only_packages(limit=limit)
    remaining_total = len(find_hb_settlement_only_packages())

    updated_lines = 0
    failed = 0
    for i, (package_id, order_number) in enumerate(pairs):
        if progress_cb:
            progress_cb(f"HB quantity backfill: {i + 1}/{len(pairs)} (sipariş {order_number})")
        try:
            path = HB_ORDER_DETAIL_PATH_TMPL.format(order_number=order_number)
            detail = hepsiburada_get(path)
        except Exception as e:
            logger.debug(f"[HB BACKFILL] sipariş {order_number} çekilemedi: {e}")
            failed += 1
            continue

        lines, _ = _hb_line_rows_for(package_id, detail)
        # order_number bilgisini de order_lines şemasına eklemiyoruz (o kolonu yok,
        # orders tablosunda tutuluyor) -- sadece barkod/miktar/fiyat/komisyon güncellenir.
        if lines:
            upsert_order_lines(lines)
            updated_lines += len(lines)

    return {
        "processed": len(pairs),
        "updated_lines": updated_lines,
        "failed": failed,
        "remaining": max(remaining_total - len(pairs) + failed, 0),
    }


@app.route("/api/backfill-hb-quantities", methods=["POST"])
def api_backfill_hb_quantities():
    """Faz 3 backfill'i küçük bir parti (varsayılan 50 sipariş) için tetikler.
    Kuyruk boşalana kadar tekrar tekrar çağırılması amaçlanmıştır (örn. panelden
    'Eksik miktarları tamamla' butonu, ya da cron ile günde birkaç kez)."""
    cred_error = _check_hb_credentials()
    if cred_error:
        return jsonify({"error": cred_error}), 400

    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, 500))
    try:
        result = backfill_hb_settlement_only_quantities(limit=limit)
    except Exception as e:
        return jsonify({"error": f"Backfill hatası: {e}"}), 500
    return jsonify(result)


@app.route("/api/backfill-hb-quantities/status")
def api_backfill_hb_quantities_status():
    """Kuyrukta kaç paket kaldığını (henüz gerçek quantity'si çekilmemiş) döner."""
    try:
        remaining = len(find_hb_settlement_only_packages())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"remaining": remaining})


# --- HB Muhasebe/Finans senkronizasyonu ---
# ŞEMA 25.07.2026'da CANLI YANITLA DOĞRULANDI (bkz. terminal logundaki
# "HEPSİBURADA /transactions HAM YANIT" çıktısı). Gerçek alan adları:
#   id (benzersiz UUID), transactionType, status, sku, packageNumber, orderNumber,
#   invoiceNumber, orderItemNumber, quantity,
#   amount: {value, currencyCode}   <-- /packages'taki {"currency","amount"} formatından
#                                        FARKLI: burada anahtar "amount" değil "value".
#   taxAmount: {value, currencyCode}, netAmount: {value, currencyCode},
#   orderDate, invoiceDate, dueDate, paymentDate, isInvoice, isIncome,
#   transactionTypeCategory, productName.
# ÖNEMLİ: amount.value ZATEN İŞARETLİ geliyor (negatif = satıcıdan çıkan tutar/gider,
# pozitif = satıcıya giren tutar/gelir-iade). _hb_money hem {"value"} hem {"amount"}
# formatını destekler; işaret ASLA çevrilmez.
#
# TransactionTypes eşlemesi (resmi dokümantasyon + canlı yanıt):
#   kargo/teslimat  -> cargo_costs
#   stopaj          -> other_financials
#   Sale/Commission/Return -> settlements tablosuna canonical 'Sale'/'Return'
#   transaction_type ile yazılır (bkz. sync_hb_finance_data).
_HB_CARGO_TRANSACTION_TYPES = {
    "ShipmentCostSharingExpense", "ShipmentCostSharingIncome",
    "OneClickReturnShipmentCostSharingExpense", "ReturnShipmentCostSharingExpense",
    "DropShipmentCostSharingExpense", "DropShipmentCostSharingIncome",
    "TransportExpense", "TransportExpenseRefund",
    "DeliveryProcessingFee", "DeliveryProcessingFeeRefund", "ReturnDeliveryProcessingFee",
    "CargoCostRefund", "CargoMargin",
    "CargoCompensationIncome", "CargoCompensationIncomeRefund",
    "CargoCompensationExpenseRefund", "CargoCompensationSellerSatisfactionIncome",
    "CargoLimitExcessCompensationIncome",
}
_HB_STOPPAGE_TRANSACTION_TYPES = {"Stoppage", "StoppageRefund", "GoldLaborStoppage", "GoldLaborStoppageRefund"}
# Aşağıdaki üç grup settlements tablosuna canonical transaction_type='Sale' (satış +
# komisyon, ikisi de aynı grupta toplanır -> SUM(seller_revenue) netleşir) veya
# 'Return' (iade) olarak yazılır. Bkz. sync_hb_finance_data.
_HB_SALE_TRANSACTION_TYPES = {"Payment"}
_HB_COMMISSION_TRANSACTION_TYPES = {
    "Commission", "CommissionRefund", "CommissionCorrection", "CommissionInvoiceRefund",
    "PaymentServiceCostReflection", "PaymentServiceCostReflectionRefund",
    "ProcessingFeeExpense",
}
_HB_RETURN_TRANSACTION_TYPES = {"Return"}

_HB_FINANCE_TYPE_SAMPLES_LOGGED = set()  # her yeni tip GRUBU için ham örneği bir kez basmak üzere


def _hb_txn_amount(t, key="amount"):
    """/transactions kaydından tutar okur. _hb_money üzerinden hem
    {"value": ...} hem {"amount": ...} formatlarını destekler; işaret korunur."""
    return _hb_money(t.get(key) if isinstance(t, dict) else None)


def _log_hb_finance_sample_once(group_label, raw_type, t):
    """Her yeni tip grubunda (SATIŞ/KOMİSYON/İADE) ilk canlı kaydı bir kez konsola
    basar -- eşleme varsayımlarını (özellikle işaret yönünü) gözle doğrulamak için."""
    if group_label in _HB_FINANCE_TYPE_SAMPLES_LOGGED:
        return
    _HB_FINANCE_TYPE_SAMPLES_LOGGED.add(group_label)
    import json as _json
    logger.debug("=" * 60)
    logger.debug(f"HEPSİBURADA /transactions ÖRNEK KAYIT — {group_label} (tip: {raw_type}):")
    logger.debug(_json.dumps(t, ensure_ascii=False, indent=2, default=str)[:3000])
    logger.debug("=" * 60)


def _fetch_hb_transactions_chunk(start_dt, end_dt, transaction_types=None):
    """Tek bir (<=1 ay) tarih aralığı için tüm sayfaları çeker."""
    global _HB_FINANCE_DEBUG_LOGGED
    chunk_items = []
    offset = 0
    limit = 100

    while True:
        params = {
            "Offset": offset,
            "Limit": limit,
            "OrderDateStart": start_dt.strftime("%Y-%m-%d"),
            "OrderDateEnd": end_dt.strftime("%Y-%m-%d"),
        }
        if transaction_types:
            params["TransactionTypes"] = ",".join(transaction_types)

        data = hepsiburada_finance_get(HB_TRANSACTIONS_PATH, params)
        items = data if isinstance(data, list) else (
            (data or {}).get("items") or (data or {}).get("transactions") or (data or {}).get("data") or []
        )

        if not _HB_FINANCE_DEBUG_LOGGED and items:
            import json as _json
            logger.debug("=" * 60)
            logger.debug("HEPSİBURADA /transactions HAM YANIT — İLK ELEMAN (teşhis için):")
            logger.debug(_json.dumps(items[0], ensure_ascii=False, indent=2, default=str)[:4000])
            logger.debug("=" * 60)
            _HB_FINANCE_DEBUG_LOGGED = True

        chunk_items.extend(items)
        if len(items) < limit:
            break
        offset += limit

    return chunk_items


def fetch_all_hb_transactions(start_dt, end_dt, transaction_types=None):
    """/transactions/merchantid/{id} -- Kayıt Bazlı Muhasebe Servisi.

    ÖNEMLİ (25.07.2026 keşfi): API, OrderDateStart/OrderDateEnd (ve diğer
    tüm tarih filtresi çiftlerinin) aralığının 1 aydan uzun olmasına izin
    vermiyor (400: "range cannot be greater than 1 month"). Bu yüzden
    istenen [start_dt, end_dt] aralığını <=28 günlük dilimlere bölüp her
    dilim için ayrı ayrı sorgulayıp sonuçları birleştiriyoruz."""
    all_items = []
    chunk_start = start_dt
    step = timedelta(days=28)

    while chunk_start < end_dt:
        chunk_end = min(chunk_start + step, end_dt)
        logger.debug(f"[HB FINANCE DEBUG] dilim: {chunk_start.date()} -> {chunk_end.date()}")
        chunk_items = _fetch_hb_transactions_chunk(chunk_start, chunk_end, transaction_types)
        logger.debug(f"[HB FINANCE DEBUG]   -> {len(chunk_items)} işlem")
        all_items.extend(chunk_items)
        chunk_start = chunk_end

    return all_items


def sync_hb_finance_data(start_dt, end_dt, progress_cb=None):
    """Kargo maliyeti (ShipmentCostSharing*, TransportExpense, DeliveryProcessingFee,
    CargoCostRefund vb.) -> cargo_costs; stopaj (Stoppage/StoppageRefund) ->
    other_financials; satış+komisyon (EInvoiceSales*/Commission*) ve iade (Return)
    -> settlements tablosuna yazar (hepsi marketplace='hepsiburada' ile).

    amount.value ZATEN İŞARETLİ gelir (negatif = gider, pozitif = gelir/iade).
    İşaret asla çevrilmez; _hb_money / _hb_txn_amount yalnızca float parse eder.

    Settlements netleşmesi: EInvoiceSales* (pozitif gelir) ve Commission*
    (negatif gider) kayıtları AYNI canonical transaction_type='Sale' ile,
    ayrı satırlar halinde settlements'a yazılır. profit_engine.py
    _load_sale_settlements() bunları (marketplace, shipment_package_id, barcode)
    bazında SUM(seller_revenue) ile topladığı için komisyon otomatik netleşir —
    burada manuel eşleştirme/matching yapmaya gerek yok. commission_amount ayrıca
    (pozitif, bilgi amaçlı) saklanır. Return kayıtları transaction_type='Return'
    ile debt/credit alanlarına yazılır (_load_return_totals SUM(debt) kullanır).
    """
    if progress_cb:
        progress_cb("Hepsiburada finans kayıtları çekiliyor…")

    types_to_fetch = sorted(
        _HB_CARGO_TRANSACTION_TYPES | _HB_STOPPAGE_TRANSACTION_TYPES
        | _HB_SALE_TRANSACTION_TYPES | _HB_COMMISSION_TRANSACTION_TYPES | _HB_RETURN_TRANSACTION_TYPES
    )
    transactions = fetch_all_hb_transactions(start_dt, end_dt, transaction_types=types_to_fetch)

    cargo_rows = []
    other_rows = []
    settlement_rows = []
    unmapped_types = set()

    for t in transactions:
        raw_type = t.get("transactionType") or t.get("TransactionType") or t.get("type")
        if raw_type is None:
            continue

        # Canlı yanıtla doğrulanmış alan adları (25.07.2026)
        order_number = t.get("orderNumber") or t.get("OrderNumber")
        package_number = t.get("packageNumber") or t.get("PackageNumber")
        sku = t.get("sku") or t.get("Sku")
        record_date = _hb_iso_to_epoch_ms(
            t.get("orderDate") or t.get("invoiceDate") or t.get("recordDate") or t.get("paymentDate")
        )
        description = t.get("productName") or t.get("description") or raw_type

        # İşaret korunur — API zaten negatif (gider) / pozitif (gelir-iade) gönderir
        signed_amount = _hb_txn_amount(t, "amount")
        if signed_amount is None:
            signed_amount = 0.0

        try:
            spid = int(package_number) if package_number is not None else None
        except (TypeError, ValueError):
            spid = package_number

        raw_id = t.get("id") or t.get("Id") or t.get("transactionId")
        stable_id = raw_id or f"{raw_type}|{order_number}|{sku}|{package_number}|{record_date}"
        row_id = f"hb_{stable_id}"

        if raw_type in _HB_CARGO_TRANSACTION_TYPES:
            _log_hb_finance_sample_once("KARGO", raw_type, t)
            cargo_rows.append({
                "id": row_id,
                "marketplace": "hepsiburada",
                "invoice_serial_number": t.get("invoiceNumber"),
                "shipment_package_id": spid,
                "order_number": order_number,
                "barcode": sku,
                "amount": signed_amount,
                "raw_json": _json_dump_safe(t),
            })
        elif raw_type in _HB_STOPPAGE_TRANSACTION_TYPES:
            _log_hb_finance_sample_once("STOPAJ", raw_type, t)
            # Negatif (gider) → debt, pozitif (iade/gelir) → credit
            debt = abs(signed_amount) if signed_amount < 0 else 0.0
            credit = signed_amount if signed_amount > 0 else 0.0
            other_rows.append({
                "id": row_id,
                "marketplace": "hepsiburada",
                "transaction_date": record_date,
                "barcode": sku,
                "transaction_type": "Stoppage",
                "raw_transaction_type": raw_type,
                "transaction_sub_type": None,
                "receipt_id": None,
                "description": description,
                "debt": debt,
                "credit": credit,
                "order_number": order_number,
                "payment_order_id": None,
                "payment_date": _hb_iso_to_epoch_ms(t.get("paymentDate")),
                "shipment_package_id": spid,
            })
        elif raw_type in _HB_SALE_TRANSACTION_TYPES:
            _log_hb_finance_sample_once("SATIŞ", raw_type, t)
            settlement_rows.append({
                "id": row_id,
                "marketplace": "hepsiburada",
                "transaction_date": record_date,
                "barcode": sku,
                "transaction_type": "Sale",
                "raw_transaction_type": raw_type,
                "receipt_id": None,
                "description": description,
                "debt": None,
                "credit": None,
                "payment_period": None,
                "commission_rate": None,
                "commission_amount": 0.0,
                "seller_revenue": signed_amount,
                "order_number": order_number,
                "payment_order_id": None,
                "payment_date": _hb_iso_to_epoch_ms(t.get("paymentDate")),
                "shipment_package_id": spid,
            })
        elif raw_type in _HB_COMMISSION_TRANSACTION_TYPES:
            _log_hb_finance_sample_once("KOMİSYON", raw_type, t)
            # Komisyon kaydı da 'Sale' grubuna yazılır ki _load_sale_settlements
            # SUM(seller_revenue) ile aynı (package, barcode) anahtarındaki satış
            # tutarından otomatik düşsün (signed_amount burada negatif/gider).
            settlement_rows.append({
                "id": row_id,
                "marketplace": "hepsiburada",
                "transaction_date": record_date,
                "barcode": sku,
                "transaction_type": "Sale",
                "raw_transaction_type": raw_type,
                "receipt_id": None,
                "description": description,
                "debt": None,
                "credit": None,
                "payment_period": None,
                "commission_rate": None,
                "commission_amount": abs(signed_amount),
                "seller_revenue": signed_amount,
                "order_number": order_number,
                "payment_order_id": None,
                "payment_date": _hb_iso_to_epoch_ms(t.get("paymentDate")),
                "shipment_package_id": spid,
            })
        elif raw_type in _HB_RETURN_TRANSACTION_TYPES:
            _log_hb_finance_sample_once("İADE", raw_type, t)
            debt = abs(signed_amount) if signed_amount < 0 else 0.0
            credit = signed_amount if signed_amount > 0 else 0.0
            settlement_rows.append({
                "id": row_id,
                "marketplace": "hepsiburada",
                "transaction_date": record_date,
                "barcode": sku,
                "transaction_type": "Return",
                "raw_transaction_type": raw_type,
                "receipt_id": None,
                "description": description,
                "debt": debt,
                "credit": credit,
                "payment_period": None,
                "commission_rate": None,
                "commission_amount": None,
                "seller_revenue": None,
                "order_number": order_number,
                "payment_order_id": None,
                "payment_date": _hb_iso_to_epoch_ms(t.get("paymentDate")),
                "shipment_package_id": spid,
            })
        else:
            unmapped_types.add(raw_type)

    upsert_cargo_costs(cargo_rows)
    upsert_other_financials(other_rows)
    upsert_settlements(settlement_rows)

    if unmapped_types and progress_cb:
        progress_cb(f"Not: eşlenmeyen işlem tipleri (atlandı): {', '.join(sorted(unmapped_types))}")

    return {
        "cargo_count": len(cargo_rows),
        "stoppage_count": len(other_rows),
        "settlement_count": len(settlement_rows),
        "unmapped_types": sorted(unmapped_types),
        "raw_count": len(transactions),
    }


def _json_dump_safe(obj):
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return None


# --- Incremental senkronizasyon ---
# Her tam senkronizasyon, o veri türü için zaten taranmış olan tarih aralığını
# yeniden taramak yerine, en son senkronize edilen noktadan (bir güvenlik payı
# çıkararak) devam eder. Bu payın nedeni: siparişler (Created -> Shipped ->
# Delivered/Cancelled) zaman içinde DURUM DEĞİŞTİRİR ama order_date sabit
# kalır -- bu yüzden son senkrondan sonraki günlerde hâlâ "açık" olabilecek
# siparişleri yakalamak için cursor'dan biraz geriye gidilir. Finans kayıtları
# (settlements/other_financials/cargo) ise oluştuktan sonra DEĞİŞMEZ, bu yüzden
# onlar için pay daha küçük tutulabilir.
#
# ÖNEMLİ: Bu daraltma SADECE varsayılan "days=N" (gündelik/otomatik) senkron
# çağrılarında uygulanır -- kullanıcı açıkça bir start_date veya
# full_history=true isterse (bilinçli geçmişe dönük tarama), cursor tamamen
# yok sayılır ve tam istenen aralık taranır. Çok uzun süre "açık" kalan
# (14 günden fazla) siparişleri/kayıtları yakalamak için ara sıra (örn.
# haftada bir) "Tüm Zamanlar" ile tam senkron yapılması önerilir.
_ORDERS_INCREMENTAL_OVERLAP_DAYS = 14
_FINANCE_INCREMENTAL_OVERLAP_DAYS = 3


def _is_incremental_eligible(args):
    """full_history veya açık start_date istenmişse False (cursor yok sayılır,
    tam aralık taranır); sadece varsayılan days=N senkronunda True döner."""
    if args.get("full_history", "").lower() == "true":
        return False
    if args.get("start_date"):
        return False
    return True


def _last_synced_dt(state_key):
    """sync_state'ten epoch ms okuyup datetime döner; kayıt yoksa None."""
    val = get_sync_state(state_key)
    if not val:
        return None
    try:
        return datetime.fromtimestamp(int(val) / 1000)
    except (TypeError, ValueError):
        return None


def _mark_synced(state_key, end_dt):
    set_sync_state(state_key, str(int(end_dt.timestamp() * 1000)))


def _incremental_start(requested_start, state_key, overlap_days):
    """Cursor varsa (cursor - overlap_days) ile requested_start'ın GEÇ olanını
    döner -- yani kullanıcının istediği başlangıçtan asla daha erkene gitmez,
    sadece zaten kapsanmış kısmı atlamak için daha geç bir noktadan başlamayı
    sağlar. Cursor yoksa (ilk senkron) requested_start aynen döner."""
    last = _last_synced_dt(state_key)
    if last is None:
        return requested_start
    candidate = last - timedelta(days=overlap_days)
    return max(requested_start, candidate)


def _run_hb_sync(start_dt, end_dt, incremental_ok=False):
    try:
        start_sync_progress(total_steps=1, message="Hepsiburada siparişleri çekiliyor…", marketplace="hepsiburada")

        def report(msg):
            update_sync_progress(message=msg, marketplace="hepsiburada")

        orders_start = (
            _incremental_start(start_dt, "hb_orders_last_synced_end", _ORDERS_INCREMENTAL_OVERLAP_DAYS)
            if incremental_ok else start_dt
        )
        order_count, line_count, failed_detail_count = sync_hb_packages_to_db(orders_start, end_dt, progress_cb=report)
        _mark_synced("hb_orders_last_synced_end", end_dt)

        # Finans senkronu ayrı try/except'te: /transactions endpoint'inin şeması
        # henüz canlı doğrulanmadığı için burada bir hata (401/403/404/alan adı
        # uyuşmazlığı) sipariş senkronunu geçersiz kılmasın -- sadece uyarı olarak
        # rapor edilir, sipariş/satır senkronu her koşulda tamamlanmış sayılır.
        finance_note = ""
        try:
            finance_start = (
                _incremental_start(start_dt, "hb_finance_last_synced_end", _FINANCE_INCREMENTAL_OVERLAP_DAYS)
                if incremental_ok else start_dt
            )
            finance_result = sync_hb_finance_data(finance_start, end_dt, progress_cb=report)
            _mark_synced("hb_finance_last_synced_end", end_dt)
            finance_note = (
                f" Finans: {finance_result['cargo_count']} kargo kalemi, "
                f"{finance_result['stoppage_count']} stopaj kaydı, "
                f"{finance_result['settlement_count']} satış/komisyon/iade kaydı senkronize edildi."
            )
            if finance_result["unmapped_types"]:
                finance_note += f" (Eşlenmeyen tipler: {', '.join(finance_result['unmapped_types'])})"
        except Exception as e:
            finance_note = f" (Finans verisi senkronize edilemedi: {e})"

        failed_detail_note = (
            f" ⚠️ {failed_detail_count} siparişin ürün/fiyat detayı çekilemedi "
            f"(bu paketler eksik satır bilgisiyle kaydedildi, log'da OrderNumber'ları var)."
            if failed_detail_count else ""
        )
        finish_sync_progress(
            message=(
                f"Tamamlandı ({orders_start:%d.%m.%Y} - {end_dt:%d.%m.%Y} tarandı): "
                f"{order_count} sipariş, {line_count} satır senkronize edildi."
                f"{failed_detail_note}{finance_note}"
            ),
            marketplace="hepsiburada",
        )
    except requests.HTTPError as e:
        fail_sync_progress(f"Hepsiburada API hatası: {e}", marketplace="hepsiburada")
    except requests.RequestException as e:
        fail_sync_progress(f"Bağlantı hatası: {e}", marketplace="hepsiburada")
    except Exception as e:
        fail_sync_progress(f"Beklenmeyen hata: {e}", marketplace="hepsiburada")


@app.route("/api/sync-hepsiburada", methods=["POST"])
def sync_hepsiburada():
    """Hepsiburada siparişlerini Flask sürecinin arka plan thread'inde çeker
    (Celery worker gerektirmez, bkz. _run_sync_in_thread notu).
    Parametreler: days=N | start_date=YYYY-MM-DD | full_history=true
    (Trendyol ile aynı mantık)."""
    error = _check_hb_credentials()
    if error:
        return jsonify({"error": error}), 400

    if sync_lock_status("hepsiburada"):
        return jsonify({"error": "Zaten devam eden bir Hepsiburada senkronizasyonu var."}), 409

    start_dt, end_dt = _resolve_sync_range(request.args)
    incremental_ok = _is_incremental_eligible(request.args)

    _run_sync_in_thread("hepsiburada", _run_hb_sync, start_dt, end_dt, incremental_ok=incremental_ok)

    return jsonify({
        "started": True,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    })


@app.route("/api/hb-config-status")
def hb_config_status():
    error = _check_hb_credentials()
    return jsonify({
        "configured": error is None,
        "message": error,
        "env": HB_ENV,
        "merchant_id": HB_MERCHANT_ID if HB_MERCHANT_ID else None,
    })


# ============================================================
# TRENDYOL (mevcut kod, değişmedi)
# ============================================================


def _check_credentials():
    if not (SUPPLIER_ID and API_KEY and API_SECRET):
        return (
            "API bilgileri eksik. Lütfen proje klasöründeki .env dosyasını "
            "TRENDYOL_SUPPLIER_ID, TRENDYOL_API_KEY ve TRENDYOL_API_SECRET "
            "değerleriyle doldurun."
        )
    return None


def trendyol_get(path, params=None, max_retries=5, throttle_seconds=0.35):
    """Trendyol API'ye GET isteği atar (retry/throttle mantığı artık
    http_client.get_json_with_retry'den geliyor, bkz. trendyol_client.py'deki
    aynı fonksiyon).

    NOT (bilinen tekrar, henüz çözülmedi): Bu fonksiyon ve app.py'nin başındaki
    SUPPLIER_ID/API_KEY/API_SECRET/BASE_URL/USER_AGENT sabitleri,
    trendyol_client.py'deki eşdeğerleriyle birebir aynı ama app.py bu modülü
    hiç import etmiyor — iki paralel Trendyol istemcisi var. Retry mantığını
    burada http_client'a taşıdık ama credential kaynağını BİRLEŞTİRMEDİK
    (app.py'yi trendyol_client.py'yi kullanacak şekilde yeniden bağlamak,
    sync akışlarının davranışını değiştirmeme garantisi için ayrı, dikkatli
    bir refactor gerektiriyor — bkz. proje notları).
    """
    return get_json_with_retry(
        f"{BASE_URL}{path}", params=params, headers={"User-Agent": USER_AGENT},
        auth=(API_KEY, API_SECRET), timeout=30, max_retries=max_retries,
        throttle_seconds=throttle_seconds, backoff_mode="exponential", backoff_base_seconds=3,
    )


def fetch_all_orders(start_ts_ms, end_ts_ms, status=None):
    """Belirtilen tarih aralığındaki tüm sipariş paketlerini sayfalayarak çeker.
    Not: Trendyol bu endpoint için maksimum 2 haftalık aralığa izin verir,
    bu yüzden çağıran taraf aralığı 2 haftalık parçalara böler.
    """
    all_orders = []
    page = 0
    size = 200  # Trendyol'un izin verdiği maksimum sayfa boyutu

    while True:
        params = {
            "startDate": start_ts_ms,
            "endDate": end_ts_ms,
            "page": page,
            "size": size,
            "orderByField": "PackageLastModifiedDate",
            "orderByDirection": "DESC",
        }
        if status:
            params["status"] = status

        data = trendyol_get(f"/integration/order/sellers/{SUPPLIER_ID}/orders", params)
        content = data.get("content") or []
        all_orders.extend(content)

        total_pages = data.get("totalPages") or 1
        page += 1
        if page >= total_pages:
            break

    return all_orders


def _date_chunks(start_dt, end_dt, max_days=14):
    """Trendyol'un 2 haftalık aralık kısıtına uymak için tarih aralığını parçalara böler."""
    chunks = []
    cur = start_dt
    while cur < end_dt:
        chunk_end = min(cur + timedelta(days=max_days), end_dt)
        chunks.append((cur, chunk_end))
        cur = chunk_end
    return chunks


def get_daily_returns(days=30, start_dt=None, end_dt=None):
    """getClaims servisinden iade verilerini çekip günlük bazda özetler.
    NOT: Sadece Trendyol claims'i çeker (trendyol_get) -- Hepsiburada iadeleri
    bu endpoint'e dahil değil (HB için ayrı bir claims/returns entegrasyonu yok).
    """
    if start_dt is None or end_dt is None:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)

    all_claims = []
    for chunk_start, chunk_end in _date_chunks(start_dt, end_dt):
        start_ms = int(chunk_start.timestamp() * 1000)
        end_ms = int(chunk_end.timestamp() * 1000)
        page = 0
        size = 200
        while True:
            params = {
                "startDate": start_ms,
                "endDate": end_ms,
                "page": page,
                "size": size,
            }
            data = trendyol_get(f"/integration/order/sellers/{SUPPLIER_ID}/claims", params)
            content = data.get("content") or []
            all_claims.extend(content)
            total_pages = data.get("totalPages") or 1
            page += 1
            if page >= total_pages:
                break

    daily = defaultdict(lambda: {"claim_count": 0, "item_count": 0})
    for c in all_claims:
        claim_date_ms = c.get("claimDate") or c.get("orderDate")
        if not claim_date_ms:
            continue
        day_key = datetime.fromtimestamp(claim_date_ms / 1000).strftime("%Y-%m-%d")
        bucket = daily[day_key]
        bucket["claim_count"] += 1
        items = c.get("claimItems") or c.get("items") or []
        bucket["item_count"] += len(items)

    return [{"date": d, **s} for d, s in sorted(daily.items())]


def sync_orders_to_db(start_dt, end_dt, progress_cb=None):
    """Siparişleri ve satırlarını (barkod, merchantSku dahil) yerel DB'ye yazar.
    profit_engine.py bu tabloyu settlements, cargo_costs ve product_costs ile eşleştirir.
    """
    all_orders = []
    chunks = _date_chunks(start_dt, end_dt)
    for i, (chunk_start, chunk_end) in enumerate(chunks):
        if progress_cb:
            progress_cb(f"Siparişler: {chunk_start:%d.%m.%Y}-{chunk_end:%d.%m.%Y} ({i + 1}/{len(chunks)})")
        start_ms = int(chunk_start.timestamp() * 1000)
        end_ms = int(chunk_end.timestamp() * 1000)
        all_orders.extend(fetch_all_orders(start_ms, end_ms))

    unique = {o["shipmentPackageId"]: o for o in all_orders if "shipmentPackageId" in o}
    orders = list(unique.values())

    order_rows = []
    line_rows = []
    for o in orders:
        spid = o.get("shipmentPackageId")
        order_rows.append({
            "shipment_package_id": spid,
            "marketplace": "trendyol",
            "order_number": o.get("orderNumber"),
            "order_date": o.get("orderDate"),
            "status": o.get("status"),
            "customer": f"{o.get('customerFirstName', '')} {o.get('customerLastName', '')}".strip(),
            "cargo_provider": o.get("cargoProviderName"),
            "gross_amount": o.get("packageGrossAmount"),
            "discount_amount": o.get("packageTotalDiscount"),
            "net_amount": o.get("packageTotalPrice"),
        })
        for line in o.get("lines", []):
            line_rows.append({
                "shipment_package_id": spid,
                "marketplace": "trendyol",
                "barcode": line.get("barcode"),
                "merchant_sku": line.get("merchantSku") or line.get("sku"),
                "product_name": line.get("productName"),
                "quantity": line.get("quantity"),
                "line_unit_price": line.get("lineUnitPrice"),
                "commission_rate": line.get("commission"),
            })

    upsert_orders(order_rows)
    upsert_order_lines(line_rows)
    return len(order_rows), len(line_rows)


def _run_full_sync(start_dt, end_dt, incremental_ok=False):
    """Arka planda çalışır: siparişler + finans verisi + kargo faturaları.
    İlerlemeyi database.sync_progress tablosuna yazar; /api/sync-status bunu okur.
    """
    try:
        start_sync_progress(total_steps=1, message="Siparişler çekiliyor…")

        def report(msg):
            update_sync_progress(message=msg)

        orders_start = (
            _incremental_start(start_dt, "trendyol_orders_last_synced_end", _ORDERS_INCREMENTAL_OVERLAP_DAYS)
            if incremental_ok else start_dt
        )
        order_count, line_count = sync_orders_to_db(orders_start, end_dt, progress_cb=report)
        _mark_synced("trendyol_orders_last_synced_end", end_dt)

        finance_start = (
            _incremental_start(start_dt, "trendyol_finance_last_synced_end", _FINANCE_INCREMENTAL_OVERLAP_DAYS)
            if incremental_ok else start_dt
        )
        result = sync_finance_data(finance_start, end_dt, progress_cb=report)
        _mark_synced("trendyol_finance_last_synced_end", end_dt)

        # NOT (05.08.2026): Trendyol'un otherfinancials endpoint'i bazı (tip,
        # tarih aralığı) parçalarında kendi backend hatası (örn. EUR/Currency
        # enum deserialization) nedeniyle 400 dönebiliyor. fetch_other_financials
        # artık böyle bir parçayı atlayıp devam ediyor (bkz. trendyol_finance.py) —
        # burada sessizce yutulmasın diye kullanıcıya özet olarak raporluyoruz.
        finance_failures_note = ""
        if result.get("other_financial_failures"):
            for f in result["other_financial_failures"]:
                logger.warning(f"[Trendyol otherfinancials] Parça atlandı: {f}")
            finance_failures_note = (
                f" ⚠️ {len(result['other_financial_failures'])} finansal kayıt parçası "
                f"Trendyol API hatası nedeniyle atlandı (detay için worker log'una bakın)."
            )

        finish_sync_progress(
            message=(
                f"Tamamlandı ({orders_start:%d.%m.%Y} - {end_dt:%d.%m.%Y} tarandı): "
                f"{order_count} sipariş, {line_count} satır, "
                f"{result['settlement_count']} settlement, {result['other_financial_count']} diğer finansal kayıt, "
                f"{result['cargo_invoice_count']} kargo faturası ({result['cargo_item_count']} kalem) senkronize edildi."
                f"{finance_failures_note}"
            )
        )
    except requests.HTTPError as e:
        fail_sync_progress(f"Trendyol API hatası: {e}")
    except requests.RequestException as e:
        fail_sync_progress(f"Bağlantı hatası: {e}")
    except Exception as e:
        fail_sync_progress(f"Beklenmeyen hata: {e}")


def _resolve_sync_range(args):
    """/api/sync-finance ve /api/dashboard-summary ortak tarih aralığı çözümlemesi.
    Öncelik: full_history=true > start_date=YYYY-MM-DD > days=N (varsayılan 30).
    """
    end_dt = datetime.now()
    if args.get("full_history", "").lower() == "true":
        return DATA_START_DATE, end_dt
    start_date_str = args.get("start_date")
    if start_date_str:
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
            return start_dt, end_dt
        except ValueError:
            pass
    days = args.get("days", default=30, type=int) if hasattr(args, "get") else 30
    days = max(1, min(days or 30, 3650))
    return end_dt - timedelta(days=days), end_dt


# --- Marketplace bazlı routing (28.07.2026: TEK motor, finance_engine.py) ---
# marketplace='all' | 'trendyol' | 'hepsiburada' — hepsi AYNI compute_profit_summary()
# çağrısından, sadece marketplace_filter parametresiyle süzülüyor. Bu sayede
# 'all' HER ZAMAN 'trendyol' + 'hepsiburada' toplamına birebir eşittir
# (matematiksel garanti — iki ayrı formül yok, bkz. finance_engine.py docstring).

def _dashboard_summary_for_marketplace(start_dt, end_dt, marketplace):
    mp_filter = None if marketplace == "all" else marketplace
    summary = compute_profit_summary(start_dt=start_dt, end_dt=end_dt, marketplace_filter=mp_filter)
    t = summary["totals"]

    return {
        "marketplace": marketplace,
        "totals": {
            # YENİ: gerçek Ciro (brüt satış tutarı, komisyon düşülmeden önce)
            "gross_revenue": t["grossRevenue"],
            # Eski "revenue" alan adı korunuyor (geriye dönük uyumluluk) ama
            # artık HER ZAMAN Net Hakediş anlamına geliyor (Ciro - Komisyon -
            # Hizmet Bedeli) — önceki sürümlerde de aslında bu değeri
            # taşıyordu, sadece "Ciro" diye etiketleniyordu (düzeltildi).
            "revenue": t["netHakedis"],
            "net_hakedis": t["netHakedis"],
            "commission": t["commission"],
            "service_fee": t["serviceFee"],
            "gross_profit": t["grossProfit"],
            "cargo_total": t["cargoTotal"],
            "stoppage": t["stoppage"],
            "platform_service_fee": t["platformServiceFee"],
            "cash_advance_cost": t["cashAdvanceCost"],
            "net_profit": t["netProfit"],  # ARTIK İADE DAHİL (kritik düzeltme)
            "return_amount": t["returnAmount"],
            "return_count": t["returnCount"],
            "payment_order_net": t["paymentOrderNet"],
            "vat_payable_estimate": t["vatPayableEstimate"],
            "net_profit_after_vat_estimate": t["netProfitAfterVatEstimate"],
        },
        "data_quality": summary["data_quality"],
        "lines": summary["lines"],
        "by_marketplace": summary["by_marketplace"],
    }


def _monthly_profit_for_marketplace(start_dt, end_dt, marketplace):
    mp_filter = None if marketplace == "all" else marketplace
    return compute_monthly_profit(start_dt, end_dt, marketplace_filter=mp_filter)


@app.route("/")
def ai_genel_bakis_page():
    return render_template("pages/ai-genel-bakis.html", active_page="ai-genel-bakis")


@app.route("/gosterge-paneli")
def gosterge_paneli_page():
    return render_template("pages/gosterge-paneli.html", active_page="gosterge-paneli")


@app.route("/finans")
def finans_page():
    return render_template("pages/finans.html", active_page="finans")


@app.route("/siparisler")
def siparisler_page():
    return render_template("pages/siparisler.html", active_page="siparisler")


@app.route("/urunler")
def urunler_page():
    return render_template("pages/urunler.html", active_page="urunler")


@app.route("/stok")
def stok_page():
    return render_template("pages/stok.html", active_page="stok")


@app.route("/ayarlar")
def ayarlar_page():
    return render_template("pages/ayarlar.html", active_page="ayarlar")


# Faz 5 öncesi eski linkler için geriye dönük uyumluluk (yer imleri, tarayıcı geçmişi vb.)
@app.route("/dashboard")
def dashboard():
    return redirect("/gosterge-paneli")


@app.route("/orders")
def orders_page():
    return redirect("/siparisler")


@app.route("/api/config-status")
def config_status():
    error = _check_credentials()
    return jsonify({
        "configured": error is None,
        "message": error,
        "env": ENV,
        "supplier_id": SUPPLIER_ID if SUPPLIER_ID else None,
        "data_start_date": DATA_START_DATE.strftime("%Y-%m-%d"),
    })


@app.route("/api/daily-sales")
def daily_sales():
    """DB-tabanlı: orders/order_lines tablolarından, marketplace filtresi olmadan
    (Trendyol + Hepsiburada birlikte) günlük satış özeti üretir. Önceden bu endpoint
    canlı Trendyol API'sini çağırıyordu; bu yüzden DB'ye yazılan Hepsiburada verisi
    hiç görünmüyordu."""
    start_dt, end_dt = _resolve_sync_range(request.args)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    with get_connection() as conn:
        order_rows = conn.execute(
            """SELECT * FROM orders WHERE order_date BETWEEN ? AND ?
               ORDER BY order_date DESC""",
            (start_ms, end_ms),
        ).fetchall()

        pairs = [(r["marketplace"], r["shipment_package_id"]) for r in order_rows]
        item_counts = defaultdict(int)
        commission_by_key = defaultdict(float)
        if pairs:
            placeholders = ",".join("(?,?)" for _ in pairs)
            params = [v for pair in pairs for v in pair]
            qty_rows = conn.execute(
                f"""SELECT shipment_package_id, marketplace,
                           SUM(quantity) AS qty,
                           SUM(COALESCE(line_unit_price, 0) * COALESCE(quantity, 0)
                               * COALESCE(commission_rate, 0) / 100.0) AS est_commission
                    FROM order_lines
                    WHERE (marketplace, shipment_package_id) IN ({placeholders})
                    GROUP BY marketplace, shipment_package_id""",
                params,
            ).fetchall()
            for r in qty_rows:
                item_counts[(r["marketplace"], r["shipment_package_id"])] = r["qty"] or 0
                commission_by_key[(r["marketplace"], r["shipment_package_id"])] = r["est_commission"] or 0

    daily_map = defaultdict(lambda: {
        "order_count": 0, "gross_amount": 0.0, "discount_amount": 0.0,
        "net_amount": 0.0, "commission_amount": 0.0, "item_count": 0, "cancelled_count": 0,
    })
    CANCELLED_STATUSES = {"Cancelled", "İptal Edildi", "Returned", "UnDelivered", "UnPacked"}

    for r in order_rows:
        day_key = datetime.fromtimestamp(r["order_date"] / 1000).strftime("%Y-%m-%d")
        d = daily_map[day_key]
        d["order_count"] += 1
        d["gross_amount"] += r["gross_amount"] or 0
        d["discount_amount"] += r["discount_amount"] or 0
        d["net_amount"] += r["net_amount"] or 0
        d["item_count"] += item_counts.get((r["marketplace"], r["shipment_package_id"]), 0)
        d["commission_amount"] += commission_by_key.get((r["marketplace"], r["shipment_package_id"]), 0)
        if r["status"] in CANCELLED_STATUSES:
            d["cancelled_count"] += 1

    daily = [{"date": k, **v} for k, v in sorted(daily_map.items())]
    for d in daily:
        d["gross_amount"] = round(d["gross_amount"], 2)
        d["discount_amount"] = round(d["discount_amount"], 2)
        d["net_amount"] = round(d["net_amount"], 2)
        d["commission_amount"] = round(d["commission_amount"], 2)

    totals = {
        "order_count": sum(d["order_count"] for d in daily),
        "gross_amount": round(sum(d["gross_amount"] for d in daily), 2),
        "discount_amount": round(sum(d["discount_amount"] for d in daily), 2),
        "net_amount": round(sum(d["net_amount"] for d in daily), 2),
        "commission_amount": round(sum(d["commission_amount"] for d in daily), 2),
        "item_count": sum(d["item_count"] for d in daily),
        "cancelled_count": sum(d["cancelled_count"] for d in daily),
    }

    orders_summary = [
        {
            "orderNumber": r["order_number"],
            "shipmentPackageId": r["shipment_package_id"],
            "marketplace": r["marketplace"],
            "orderDate": r["order_date"],
            "status": r["status"],
            "customer": r["customer"],
            "netAmount": r["net_amount"],
            "cargoProvider": r["cargo_provider"],
        }
        for r in order_rows
    ]

    # "daily" ve "totals" HER ZAMAN tam aralık üzerinden hesaplanır (yukarıda),
    # sadece ham sipariş listesi sayfalanır — büyük aralıklarda payload'ı makul
    # tutmak için, ama sessizce veri kaybettirmeden (eskiden sabit [:2000] kırpma
    # vardı, toplamlar doğruydu ama liste sekmesi büyük aralıklarda eksik
    # görünüyordu).
    page = max(1, request.args.get("page", default=1, type=int) or 1)
    page_size = max(1, min(request.args.get("page_size", default=500, type=int) or 500, 2000))
    total_orders = len(orders_summary)
    offset = (page - 1) * page_size

    return jsonify({
        "daily": daily,
        "totals": totals,
        "orders": orders_summary[offset:offset + page_size],
        "orders_total": total_orders,
        "page": page,
        "page_size": page_size,
    })


@app.route("/api/today-order-count")
def today_order_count():
    """Bugünün (yerel/sunucu tarihine göre) toplam sipariş sayısı — Trendyol +
    Hepsiburada birlikte, marketplace filtresi yok. Kasıtlı olarak sayfadaki
    tarih aralığı filtresinden (range-select) bağımsız çalışır: kullanıcı
    geçmiş bir aralık seçmiş olsa bile her zaman 'bugün'ü gösterir."""
    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)
    start_ms = int(start_of_day.timestamp() * 1000)
    end_ms = int(end_of_day.timestamp() * 1000)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE order_date >= ? AND order_date < ?",
            (start_ms, end_ms),
        ).fetchone()

    return jsonify({"count": row["c"] if row else 0})


@app.route("/api/today-net-profit")
def today_net_profit():
    """AI Genel Bakış hero kartındaki 'Bugünkü Net Kazanç' için: bugünün
    (yerel/sunucu tarihine göre) TAM net kâr rakamı — finance_engine'deki
    netProfit ile birebir aynı formül (komisyon + hizmet bedeli + kargo +
    stopaj + iade + platform bedeli dahil), marketplace filtresi yok.
    today_order_count ile aynı gerekçeyle sayfadaki tarih aralığı
    filtresinden bağımsız, her zaman 'bugün'ü hesaplar.

    Bugün satılan satırlardan herhangi birinin ürün maliyeti (SKU) hiç
    tanımlı değilse netProfit BİLİNÇLİ OLARAK None döner (yarı-doğru bir
    rakam göstermek yerine) — frontend bu durumda '-' gösterir, çünkü o
    satırın kâr/zarar katkısı gerçekte bilinmiyor, netProfit toplamına
    dahil edilmemiş oluyor ve rakam yanıltıcı olurdu.

    06.08.2026 düzeltmesi: include_settlement_only=False geçiliyor. Aksi
    halde compute_profit_summary, bugün settlement kaydı düşen ama SİPARİŞ
    TARİHİ geçmiş bir güne ait satırlar için de sentetik satır ekliyordu —
    bu da 'Bugünkü Net Kazanç'ın, sadece bugün sipariş edilenleri kapsayan
    'Bugünkü Satış' (hero-today-sales / /api/daily-sales) rakamından daha
    büyük çıkmasına (matematiksel olarak imkânsız görünmesine) yol açıyordu.
    Artık ikisi de aynı tarih eksenini (order_date) kullanıyor."""
    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day)
    end_of_day = start_of_day + timedelta(days=1)

    try:
        summary = compute_profit_summary(start_dt=start_of_day, end_dt=end_of_day, marketplace_filter=None,
                                          include_settlement_only=False)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500

    missing_skus = summary["data_quality"]["skus_missing_cost"]
    net_profit = summary["totals"]["netProfit"] if not missing_skus else None

    return jsonify({
        "netProfit": net_profit,
        "hasMissingCost": bool(missing_skus),
        "missingCostCount": len(missing_skus),
    })


@app.route("/api/daily-returns")
def daily_returns():
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400

    start_dt, end_dt = _resolve_sync_range(request.args)

    try:
        daily = get_daily_returns(start_dt=start_dt, end_dt=end_dt)
    except requests.HTTPError as e:
        return jsonify({"error": f"Trendyol API hatası (iadeler): {e}"}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"Bağlantı hatası: {e}"}), 502

    return jsonify({"daily": daily})


@app.route("/api/sync-finance", methods=["POST"])
def sync_finance():
    """Siparişleri + Finans API verisini Flask sürecinin arka plan
    thread'inde çeker (Celery worker gerektirmez, bkz. _run_sync_in_thread
    notu). Hemen döner; ilerleme için /api/sync-status'ü yoklayın (polling).
    Hepsiburada bilgileri .env'de tanımlıysa, aynı tetiklemeyle Hepsiburada
    senkronizasyonu da (ayrı kilit, ayrı thread'de, paralel) başlatılır."""
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400

    if sync_lock_status("trendyol"):
        return jsonify({"error": "Zaten devam eden bir senkronizasyon var."}), 409

    start_dt, end_dt = _resolve_sync_range(request.args)
    incremental_ok = _is_incremental_eligible(request.args)

    _run_sync_in_thread("trendyol", _run_full_sync, start_dt, end_dt, incremental_ok=incremental_ok)

    hb_started = False
    if _check_hb_credentials() is None and not sync_lock_status("hepsiburada"):
        _run_sync_in_thread("hepsiburada", _run_hb_sync, start_dt, end_dt, incremental_ok=incremental_ok)
        hb_started = True

    return jsonify({
        "started": True,
        "hepsiburada_started": hb_started,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),
    })


@app.route("/api/sync-status")
def sync_status():
    marketplace = request.args.get("marketplace", "trendyol")
    return jsonify(get_sync_progress(marketplace=marketplace))


@app.route("/api/dashboard-summary")
def dashboard_summary():
    start_dt, end_dt = _resolve_sync_range(request.args)
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    if marketplace not in ("all", "trendyol", "hepsiburada"):
        marketplace = "all"
    try:
        summary = _dashboard_summary_for_marketplace(start_dt, end_dt, marketplace)
    except Exception as e:
        return jsonify({"error": f"Kâr hesaplama hatası: {e}"}), 500
    if "lines" in summary:
        # KPI toplamları (ciro/kâr/marj) 'lines' listesinden BAĞIMSIZ olarak
        # zaten tam aralık üzerinden hesaplanmış durumda (yukarıda) — burada
        # sadece detay tablosu sayfalanıyor, toplamlar etkilenmiyor.
        page = max(1, request.args.get("lines_page", default=1, type=int) or 1)
        page_size = max(1, min(request.args.get("lines_page_size", default=1000, type=int) or 1000, 5000))
        total_lines = len(summary["lines"])
        offset = (page - 1) * page_size
        summary["lines"] = summary["lines"][offset:offset + page_size]
        summary["lines_total"] = total_lines
        summary["lines_page"] = page
        summary["lines_page_size"] = page_size
    return jsonify(summary)


@app.route("/api/best-sellers")
def api_best_sellers():
    start_dt, end_dt = _resolve_sync_range(request.args)
    limit = request.args.get("limit", default=10, type=int)
    limit = max(1, min(limit, 50))
    # DÜZELTME (28.07.2026): Frontend zaten 'marketplace' parametresini
    # gönderiyordu (rangeQueryParam(), templates/app.html) ama bu route onu
    # hiç okumuyordu — sonuç: pazaryeri sekmesi değiştirilse bile en çok
    # satanlar listesi her zaman TÜM pazaryerlerinin karışık verisini
    # gösteriyordu. Artık okunuyor ve motora iletiliyor.
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    mp_filter = None if marketplace not in ("trendyol", "hepsiburada") else marketplace
    try:
        result = compute_best_sellers(start_dt=start_dt, end_dt=end_dt, limit=limit, marketplace_filter=mp_filter)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500
    return jsonify({"items": result})


@app.route("/api/orders")
def api_orders():
    args = request.args
    end_dt = datetime.now()
    if args.get("full_history", "").lower() == "true":
        start_dt = DATA_START_DATE
    elif args.get("start_date"):
        try:
            start_dt = datetime.strptime(args["start_date"], "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "start_date formatı YYYY-MM-DD olmalı."}), 400
        if args.get("end_date"):
            try:
                end_dt = datetime.strptime(args["end_date"], "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                return jsonify({"error": "end_date formatı YYYY-MM-DD olmalı."}), 400
    else:
        days = args.get("days", default=30, type=int)
        days = max(1, min(days or 30, 3650))
        start_dt = end_dt - timedelta(days=days)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    status = (args.get("status") or "").strip()
    q = (args.get("q") or "").strip()
    page = max(1, args.get("page", default=1, type=int) or 1)
    page_size = max(1, min(args.get("page_size", default=50, type=int) or 50, 200))
    offset = (page - 1) * page_size

    marketplace = (args.get("marketplace") or "").strip().lower()

    where = ["order_date BETWEEN ? AND ?"]
    params = [start_ms, end_ms]
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(order_number LIKE ? OR customer LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if marketplace in ("trendyol", "hepsiburada"):
        where.append("marketplace = ?")
        params.append(marketplace)
    where_sql = " AND ".join(where)

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM orders WHERE {where_sql}", params
        ).fetchone()["c"]

        order_rows = conn.execute(
            f"""SELECT * FROM orders WHERE {where_sql}
                ORDER BY order_date DESC LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()

        pairs = [(r["marketplace"], r["shipment_package_id"]) for r in order_rows]
        lines_by_spid = defaultdict(list)
        if pairs:
            placeholders = ",".join(["(?,?)"] * len(pairs))
            flat_params = [v for pair in pairs for v in pair]
            line_rows = conn.execute(
                f"""SELECT * FROM order_lines WHERE (marketplace, shipment_package_id) IN ({placeholders})""",
                flat_params,
            ).fetchall()
            for ln in line_rows:
                lines_by_spid[(ln["marketplace"], ln["shipment_package_id"])].append({
                    "barcode": ln["barcode"],
                    "merchantSku": ln["merchant_sku"],
                    "productName": ln["product_name"],
                    "quantity": ln["quantity"],
                    "lineUnitPrice": ln["line_unit_price"],
                    "commissionRate": ln["commission_rate"],
                })

    orders = []
    for r in order_rows:
        orders.append({
            "shipmentPackageId": r["shipment_package_id"],
            "orderNumber": r["order_number"],
            "orderDate": r["order_date"],
            "status": r["status"],
            "customer": r["customer"],
            "cargoProvider": r["cargo_provider"],
            "grossAmount": r["gross_amount"],
            "discountAmount": r["discount_amount"],
            "netAmount": r["net_amount"],
            "marketplace": r["marketplace"],
            "lines": lines_by_spid.get((r["marketplace"], r["shipment_package_id"]), []),
        })

    return jsonify({
        "orders": orders,
        "total": total,
        "page": page,
        "page_size": page_size,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": (end_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
    })


@app.route("/api/product-performance")
def api_product_performance():
    start_dt, end_dt = _resolve_sync_range(request.args)
    sort_by = request.args.get("sort_by", "profit")
    order = request.args.get("order", "desc")
    limit = request.args.get("limit", default=500, type=int)
    limit = max(1, min(limit or 500, 2000))
    # DÜZELTME (28.07.2026): best-sellers ile aynı hata burada da vardı —
    # frontend marketplace gönderiyordu ama route hiç okumuyordu.
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    mp_filter = None if marketplace not in ("trendyol", "hepsiburada") else marketplace

    try:
        items = compute_best_sellers(start_dt=start_dt, end_dt=end_dt, limit=100000, marketplace_filter=mp_filter)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500

    with get_connection() as conn:
        cost_skus = {r["sku"] for r in conn.execute("SELECT sku FROM product_costs").fetchall()}
        comm_where = "merchant_sku IS NOT NULL"
        comm_params = []
        if mp_filter:
            comm_where += " AND marketplace = ?"
            comm_params.append(mp_filter)
        comm_rows = conn.execute(
            f"""SELECT merchant_sku AS sku, AVG(commission_rate) AS avg_commission,
                      AVG(line_unit_price) AS avg_price
               FROM order_lines WHERE {comm_where} GROUP BY merchant_sku""",
            comm_params,
        ).fetchall()
    comm_map = {r["sku"]: r for r in comm_rows}

    for it in items:
        it["hasCost"] = it["sku"] in cost_skus
        extra = comm_map.get(it["sku"])
        it["avgCommissionRate"] = round(extra["avg_commission"], 2) if extra and extra["avg_commission"] is not None else None
        it["avgUnitPrice"] = round(extra["avg_price"], 2) if extra and extra["avg_price"] is not None else None

    reverse = order != "asc"
    NEG_INF, POS_INF = -1e18, 1e18
    key_fns = {
        "margin": lambda x: x["margin"] if x["margin"] is not None else (NEG_INF if reverse else POS_INF),
        "profit": lambda x: x["profit"] if x["profit"] is not None else (NEG_INF if reverse else POS_INF),
        "revenue": lambda x: x["revenue"] or 0,
        "quantity": lambda x: x["quantity"] or 0,
    }
    items.sort(key=key_fns.get(sort_by, key_fns["profit"]), reverse=reverse)

    return jsonify({"items": items[:limit], "total": len(items)})


@app.route("/api/monthly-profit")
def api_monthly_profit():
    args = request.args
    end_dt = datetime.now()
    if args.get("start_date"):
        try:
            start_dt = datetime.strptime(args["start_date"], "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "start_date formatı YYYY-MM-DD olmalı."}), 400
    else:
        start_dt = DATA_START_DATE

    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    if marketplace not in ("all", "trendyol", "hepsiburada"):
        marketplace = "all"

    try:
        months = _monthly_profit_for_marketplace(start_dt, end_dt, marketplace)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500

    first_active = next(
        (i for i, m in enumerate(months) if (m.get("revenue") or 0) != 0
         or (m.get("grossProfit") or 0) != 0 or (m.get("netProfit") or 0) != 0),
        0,
    )
    months = months[first_active:]

    return jsonify({"months": months})


@app.route("/api/fixed-expenses", methods=["GET", "POST"])
def api_fixed_expenses():
    """GET: tüm aylık sabit gider kalemlerini döner (opsiyonel ?month=YYYY-MM
    ile tek aya filtrelenebilir). POST: yeni kalem ekler/günceller (body'de
    'id' verilirse günceller). Beklenen JSON: {month, label, amount, note?}
    (month formatı 'YYYY-MM')."""
    if request.method == "GET":
        month = (request.args.get("month") or "").strip() or None
        items = list_fixed_expenses(month=month)
        return jsonify({"items": items})

    data = request.get_json(silent=True) or {}
    month = (data.get("month") or "").strip()
    label = (data.get("label") or "").strip()

    if not month:
        return jsonify({"error": "'month' alanı zorunlu (YYYY-MM formatında)."}), 400
    if not label:
        return jsonify({"error": "'label' alanı zorunlu."}), 400
    try:
        amount = float(data["amount"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "'amount' zorunlu ve sayısal olmalı."}), 400

    row = {
        "id": data.get("id"),
        "month": month,
        "label": label,
        "amount": amount,
        "note": data.get("note") or None,
    }
    try:
        saved = upsert_fixed_expense(row)
    except Exception as e:
        return jsonify({"error": f"Kaydetme hatası: {e}"}), 500

    return jsonify({"ok": True, "item": saved})


@app.route("/api/fixed-expenses/<int:expense_id>", methods=["DELETE"])
def api_delete_fixed_expense(expense_id):
    try:
        delete_fixed_expense(expense_id)
    except Exception as e:
        return jsonify({"error": f"Silme hatası: {e}"}), 500
    return jsonify({"ok": True, "id": expense_id})


if __name__ == "__main__":
    logger.info("Trendyol Satış Paneli başlatılıyor... Ortam: %s", ENV)
    logger.info(
        "'Tüm Zamanlar' senkronizasyonu şu tarihten başlayacak: %s "
        "(.env'de TRENDYOL_DATA_START_DATE=YYYY-MM-DD ile değiştirebilirsiniz)",
        DATA_START_DATE.strftime("%d.%m.%Y"),
    )
    if _check_credentials():
        logger.warning(".env dosyası henüz yapılandırılmadı. Panel açılacak ama veri çekemeyecek.")
    if _check_hb_credentials():
        logger.warning("Hepsiburada .env bilgileri eksik, Hepsiburada senkronizasyonu çalışmayacak.")
    else:
        logger.info("Hepsiburada entegrasyonu yapılandırıldı. Ortam: %s", HB_ENV)
    if _AUTH_ENABLED:
        logger.info("Erişim kontrolü AÇIK (PANEL_USERNAME/PANEL_PASSWORD tanımlı).")
    else:
        logger.warning(
            "Erişim kontrolü KAPALI — panel şifresiz erişilebilir durumda. "
            ".env dosyasına PANEL_USERNAME ve PANEL_PASSWORD ekleyerek koruma altına alın."
        )
    app.run(debug=(ENV != "PROD"), port=5050, threaded=True)