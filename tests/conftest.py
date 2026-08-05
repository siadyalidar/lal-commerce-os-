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
