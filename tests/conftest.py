"""
conftest.py
-----------
Her test için izole, geçici bir SQLite dosyası kurar. database.DB_PATH'i
monkeypatch ile geçici dosyaya yönlendiriyoruz — böylece testler gerçek
trendyol_data.db dosyasına asla dokunmaz ve birbirinden bağımsız çalışır.

NOT: database.get_connection() içindeki sqlite3.connect(DB_PATH, ...)
çağrısı DB_PATH'i modül seviyesinde her çağrıda okuduğu için, monkeypatch
database.DB_PATH yapmak yeterlidir (finance_engine.py DB_PATH'i kendi
içine import etmiyor, get_connection üzerinden dolaylı kullanıyor).
"""

import base64
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Temiz, izole bir DB kurar ve database modülünü ona yönlendirir."""
    db_path = tmp_path / "test_trendyol.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return database


# ============================================================
# app.py route testleri için ortak fixture'lar
# ============================================================
# app.py, modül import edilir edilmez iki yan etki yapıyor:
#   1) TRENDYOL_ENV=PROD (varsayılan) iken PANEL_USERNAME/PANEL_PASSWORD
#      ortam değişkenlerinde tanımlı değilse RuntimeError fırlatıyor (bkz.
#      app.py'deki "GÜVENLİK DÜZELTMESİ" notu) — panel şifresiz kalmasın diye.
#   2) init_db() çağırıyor; bu da o an database.DB_PATH neyi gösteriyorsa
#      oraya yazıyor. Gerçek trendyol_data.db dosyasına dokunmamak için
#      import'tan ÖNCE DB_PATH'i geçici bir dosyaya yönlendirmemiz gerekiyor.
# app.py Python modül önbelleği yüzünden sadece BİR KEZ import edilebiliyor,
# bu yüzden bu iki koşulu session scope'unda, ilk (ve tek) import'tan önce
# kuruyoruz. Test başına gerçek izolasyonu yukarıdaki `db` fixture'ı sağlıyor:
# her testte DB_PATH'i ayrı bir geçici dosyaya yönlendiriyor ve get_connection()
# bunu her çağrıda modül seviyesinde okuduğu için app.py'deki route'lar da
# otomatik olarak o testin izole DB'sini kullanıyor.
PANEL_USERNAME = "test-panel-user"
PANEL_PASSWORD = "test-panel-pass"


@pytest.fixture(scope="session")
def flask_app(tmp_path_factory):
    """app.py'yi test süreci başına bir kez import eder ve Flask app'i döner."""
    os.environ["PANEL_USERNAME"] = PANEL_USERNAME
    os.environ["PANEL_PASSWORD"] = PANEL_PASSWORD

    import_db_path = tmp_path_factory.mktemp("app-import") / "import_only.db"
    database.DB_PATH = str(import_db_path)

    import app as app_module  # noqa: E402  (env değişkenleri ayarlandıktan sonra import edilmeli)

    app_module.app.config.update(TESTING=True)
    return app_module


@pytest.fixture()
def client(flask_app, db):
    """Test client'ı, `db` fixture'ının o test için kurduğu izole DB'ye
    yönlendirilmiş haldeyken döner (bkz. yukarıdaki not)."""
    return flask_app.app.test_client()


def auth_headers(username=PANEL_USERNAME, password=PANEL_PASSWORD):
    """Panel Basic Auth için Authorization header'ı üretir."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}
