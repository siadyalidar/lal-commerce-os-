"""
test_external_payout_db.py
----------------------------
Credential şifreleme katmanının doğrulanması:
1) DB'de düz metin olarak SAKLANMADIĞI (fiziksel satırın kendisi kontrol edilir)
2) get_credential() ile doğru şekilde çözüldüğü
3) Şifreleme öncesi (düz metin) eski kayıtların hâlâ okunabildiği (geriye dönük uyumluluk)
"""

import os

import pytest

import external_payout_db as epdb
from database import get_connection


@pytest.fixture(autouse=True)
def _isolated_key(tmp_path, monkeypatch):
    """Her testte taze bir Fernet anahtarı/cache kullan (testler birbirini etkilemesin)."""
    monkeypatch.setattr(epdb, "_fernet", None)
    monkeypatch.setattr(epdb, "_KEY_FILE", str(tmp_path / ".payout_credential.key"))
    monkeypatch.delenv(epdb._KEY_ENV_VAR, raising=False)
    yield
    monkeypatch.setattr(epdb, "_fernet", None)


def test_credential_not_stored_as_plaintext(db):
    epdb.set_credential("trendyol", "Bearer eyJsecret.token.value")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM external_credentials WHERE marketplace = ?", ("trendyol",)
        ).fetchone()
    assert "eyJsecret" not in row["value"]
    assert row["value"] != "Bearer eyJsecret.token.value"


def test_credential_roundtrip_decrypts_correctly(db):
    epdb.set_credential("hepsiburada", "Cookie: session=abc123; other=xyz")
    result = epdb.get_credential("hepsiburada")
    assert result["value"] == "Cookie: session=abc123; other=xyz"


def test_credential_status_never_exposes_value(db):
    epdb.set_credential("trendyol", "Bearer secret-value")
    status = epdb.credential_status("trendyol")
    assert status["configured"] is True
    assert "value" not in status
    assert "secret-value" not in str(status)


def test_legacy_plaintext_credential_still_readable(db):
    """Şifreleme güncellemesinden ÖNCE düz metin yazılmış bir satır, yeni
    _decrypt() ile hâlâ (bozulmadan) okunabilmeli."""
    with get_connection() as conn:
        epdb._ensure_tables(conn)
        conn.execute("""
            INSERT INTO external_credentials (marketplace, value, updated_at)
            VALUES ('trendyol', 'PLAINTEXT-OLD-TOKEN', datetime('now'))
        """)
    result = epdb.get_credential("trendyol")
    assert result["value"] == "PLAINTEXT-OLD-TOKEN"


def test_key_file_created_with_restrictive_permissions(db, tmp_path, monkeypatch):
    key_file = tmp_path / "subdir_key" / ".payout_credential.key"
    key_file.parent.mkdir()
    monkeypatch.setattr(epdb, "_KEY_FILE", str(key_file))
    monkeypatch.setattr(epdb, "_fernet", None)

    epdb.set_credential("trendyol", "some-value")

    assert key_file.exists()
    mode = oct(os.stat(key_file).st_mode)[-3:]
    assert mode == "600"
