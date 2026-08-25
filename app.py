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

NOT (blueprint refaktörü): Bu dosya artık SADECE Flask app kurulumu, erişim
kontrolü (Basic Auth) ve blueprint kaydını içeriyor. Trendyol/Hepsiburada
senkron config'i + iş mantığı sync_core.py'ye, route'lar ise
blueprints/dashboard_routes.py, blueprints/order_routes.py ve
blueprints/finance_routes.py dosyalarına taşındı (cost_routes.py/
stock_routes.py/payout_routes.py ile aynı desen). Route davranışı ve
URL'ler DEĞİŞMEDİ — sadece dosya organizasyonu.
"""

import logging
import os
import secrets

from dotenv import load_dotenv
from flask import Flask, Response, request

from database import init_db

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("trendyol_satis")

# sync_core.py de aynı değişkeni kendi içinde bağımsız olarak okuyor (BASE_URL
# seçimi için). Burada AYRICA tanımlıyoruz çünkü aşağıdaki PROD auth kontrolü
# sync_core.py'yi import etmeden (blueprint'lerden önce, dairesel import'a
# girmeden) çalışabilmeli. os.getenv yan etkisiz olduğu için iki bağımsız
# okuma sorun yaratmaz.
ENV = os.getenv("TRENDYOL_ENV", "PROD").strip().upper()  # PROD veya STAGE

app = Flask(__name__)
init_db()

from blueprints.cost_routes import bp as cost_routes_bp  # noqa: E402 (init_db'den sonra olmalı)
app.register_blueprint(cost_routes_bp)

from blueprints.supplier_routes import bp as supplier_routes_bp  # noqa: E402
app.register_blueprint(supplier_routes_bp)

from blueprints.stock_routes import bp as stock_routes_bp  # noqa: E402
app.register_blueprint(stock_routes_bp)

from blueprints.payout_routes import bp as payout_routes_bp  # noqa: E402
app.register_blueprint(payout_routes_bp)

from blueprints.dashboard_routes import bp as dashboard_routes_bp  # noqa: E402
from blueprints.growth_routes import bp as growth_routes_bp  # noqa: E402
app.register_blueprint(dashboard_routes_bp)

from blueprints.order_routes import bp as order_routes_bp  # noqa: E402
app.register_blueprint(order_routes_bp)

from blueprints.finance_routes import bp as finance_routes_bp  # noqa: E402
app.register_blueprint(finance_routes_bp)

from blueprints.reports_routes import bp as reports_routes_bp  # noqa: E402
app.register_blueprint(reports_routes_bp)

from blueprints.reviews_routes import bp as reviews_routes_bp  # noqa: E402
app.register_blueprint(reviews_routes_bp)
app.register_blueprint(growth_routes_bp)

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


if __name__ == "__main__":
    from sync_core import DATA_START_DATE, HB_ENV, _check_credentials, _check_hb_credentials

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
