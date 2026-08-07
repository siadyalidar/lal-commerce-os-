"""
tests/test_app_routes.py
--------------------------
app.py içindeki Flask route'ları için uçtan uca testler (Flask test client ile).

Kapsam:
- Panel Basic Auth: kimliksiz/yanlış kimlikli istek 401, doğru kimlikli istek
  geçiyor mu.
- Kritik API uç noktaları (/api/dashboard-summary, /api/daily-sales,
  /api/monthly-profit): 200 dönüyor mu ve beklenen JSON anahtarlarını taşıyor mu.
- /api/sync-finance ve /api/sync-hepsiburada: Trendyol/Hepsiburada API
  kimlik bilgileri (.env) tanımlı değilken davranışı.

NOT (05.08.2026 mimari düzeltmesiyle güncel davranış): Bu iki senkron
endpoint'i artık Celery worker'a bağımlı DEĞİL — manuel tetiklemeler Flask
süreci içindeki bir arka plan thread'inde çalışıyor (bkz. app.py içindeki
_run_sync_in_thread notu). Dolayısıyla "worker yokken 503" diye bir davranış
artık yok: worker kurulu olsun ya da olmasın, endpoint'ler API kimlik bilgisi
eksikse 400, senkron zaten devam ediyorsa 409, aksi halde 200 + {"started":
true} döner. Aşağıdaki testler kimlik bilgisi eksik senaryosunu (varsayılan
test ortamında SUPPLIER_ID/API_KEY/API_SECRET tanımlı değil) doğruluyor.
"""

from tests.conftest import auth_headers


# ============================================================
# Panel Basic Auth
# ============================================================

def test_protected_route_without_auth_returns_401(client):
    resp = client.get("/api/dashboard-summary")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_protected_route_with_wrong_credentials_returns_401(client):
    resp = client.get(
        "/api/dashboard-summary", headers=auth_headers("wrong", "wrong")
    )
    assert resp.status_code == 401


def test_protected_route_with_correct_credentials_passes(client):
    resp = client.get("/api/dashboard-summary", headers=auth_headers())
    assert resp.status_code == 200


# ============================================================
# Kritik API uç noktaları — 200 + beklenen JSON şekli
# ============================================================

def test_dashboard_summary_shape(client):
    resp = client.get("/api/dashboard-summary", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["marketplace"] == "all"
    assert "totals" in data
    for key in (
        "gross_revenue",
        "revenue",
        "net_hakedis",
        "commission",
        "net_profit",
        "return_amount",
    ):
        assert key in data["totals"]
    assert "lines" in data
    assert "data_quality" in data
    assert "by_marketplace" in data


def test_daily_sales_shape(client):
    resp = client.get("/api/daily-sales", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    for key in ("daily", "totals", "orders", "orders_total", "page", "page_size"):
        assert key in data
    assert isinstance(data["daily"], list)
    assert isinstance(data["orders"], list)
    for key in (
        "order_count",
        "gross_amount",
        "discount_amount",
        "net_amount",
        "commission_amount",
        "item_count",
        "cancelled_count",
    ):
        assert key in data["totals"]


def test_monthly_profit_shape(client):
    resp = client.get("/api/monthly-profit", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert "months" in data
    assert isinstance(data["months"], list)


def test_monthly_profit_invalid_start_date_returns_400(client):
    resp = client.get(
        "/api/monthly-profit?start_date=not-a-date", headers=auth_headers()
    )
    assert resp.status_code == 400


# ============================================================
# /api/sync-finance ve /api/sync-hepsiburada — kimlik bilgisi eksikken
# ============================================================
# ÖNEMLİ: Geliştiricinin yerel .env dosyasında GERÇEK Trendyol/Hepsiburada API
# bilgileri tanımlı olabilir. Testin bu duruma bağımlı olması (yani sadece
# "ortamda kimlik bilgisi yoksa" varsayımıyla yazılması) TEHLİKELİ: kimlik
# bilgileri tanımlıysa bu istek gerçekten arka planda CANLI API'ye senkron
# başlatır. Bunun yerine app modülündeki kimlik bilgisi değişkenlerini
# monkeypatch ile açıkça boşaltıyoruz — testler .env'de ne olursa olsun
# deterministik ve YAN ETKİSİZ (gerçek ağ isteği atmadan) çalışır.

def test_sync_finance_without_credentials_returns_400(client, flask_app, monkeypatch):
    monkeypatch.setattr(flask_app, "SUPPLIER_ID", "")
    monkeypatch.setattr(flask_app, "API_KEY", "")
    monkeypatch.setattr(flask_app, "API_SECRET", "")
    resp = client.post("/api/sync-finance", headers=auth_headers())
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_sync_hepsiburada_without_credentials_returns_400(client, flask_app, monkeypatch):
    monkeypatch.setattr(flask_app, "HB_MERCHANT_ID", "")
    monkeypatch.setattr(flask_app, "HB_USERNAME", "")
    monkeypatch.setattr(flask_app, "HB_PASSWORD", "")
    resp = client.post("/api/sync-hepsiburada", headers=auth_headers())
    assert resp.status_code == 400
    assert "error" in resp.get_json()
