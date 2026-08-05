"""
external_payout_db.py
----------------------
Trendyol/Hepsiburada'nın resmi API'lerinde bulunmayan "gelecek hakediş"
(future/upcoming payments) verisini, tarayıcıdan elle kopyalanan bir kimlik
bilgisiyle (Trendyol: Authorization Bearer JWT, Hepsiburada: Cookie) tarayıcı
oturumunu taklit ederek çeken external_payout_scraper.py için destek
tablolarını tanımlar.

Üç tablo:
  external_credentials     -> pazaryeri başına TEK satır, o anki header değeri
  external_scrape_status    -> pazaryeri başına TEK satır, son çekim sonucu
  external_payout_estimates -> çekilen (gün, bölge) bazlı tahmini tutarlar

✅ ENTEGRASYON TAMAMLANDI (güncellendi): finance_engine.payout_calendar()
artık bu tabloyu okuyor — get_estimates() sonucu "official" bucket'ı olarak
günlük hakediş takvimine ekleniyor ve varsa o (gün, pazaryeri) hücresindeki
estimated/lagEstimated tahminlerinin YERİNE geçiyor (aynı parayı iki kez
saymamak için, bkz. finance_engine.py payout_calendar() adım 4). Bu dosya
hâlâ sadece çekilen veriyi SAKLAMAKTAN sorumlu; okuma/birleştirme mantığı
finance_engine.py'de.

🔒 credential value'lar (JWT / Cookie) artık DÜZ METİN DEĞİL — DB'ye
yazılmadan önce Fernet (simetrik, AES tabanlı) ile şifreleniyor, okunurken
çözülüyor (bkz. _encrypt/_decrypt). Şifreleme anahtarı:
  1) PAYOUT_CREDENTIAL_KEY ortam değişkeninden (.env) okunur — ÖNERİLEN yol,
     prod'da mutlaka bunu kullanın.
  2) Tanımlı değilse, ilk çalıştırmada proje kökünde .payout_credential.key
     dosyası (izin: 0600) otomatik oluşturulur ve kullanılır — bu, panel
     restart olsa bile eski kayıtların çözülebilmesini sağlar. Bu dosya asla
     git'e eklenmemeli (.gitignore'a ekleyin) ve yedeklenmelidir: dosya
     kaybolursa DB'deki şifreli credential'lar KALICI OLARAK çözülemez hale
     gelir (kullanıcı sadece paneldeki formdan yeniden girer, veri kaybı
     olmaz — sadece elle tekrar girme gerekir).
Bu, panelin hâlâ tek kullanıcılı/yerel bir araç olduğu varsayımını
değiştirmiyor (bkz. payout_routes.py başındaki uyarı) — sadece DB dosyasının
kendisi bir şekilde sızarsa (yedek, yanlışlıkla paylaşım vb.) credential'ların
doğrudan okunabilir olmasını engelliyor.
"""

import os
import stat

from cryptography.fernet import Fernet, InvalidToken

from database import get_connection

_KEY_ENV_VAR = "PAYOUT_CREDENTIAL_KEY"
_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".payout_credential.key")

_fernet = None


def _load_or_create_key():
    env_key = os.getenv(_KEY_ENV_VAR, "").strip()
    if env_key:
        return env_key.encode()

    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            return f.read().strip()

    # İlk çalıştırma: yeni anahtar üret, sıkı izinlerle kaydet.
    key = Fernet.generate_key()
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    print(
        f"[external_payout_db] UYARI: {_KEY_FILE} içinde yeni bir şifreleme "
        f"anahtarı oluşturuldu. Kalıcılık/taşınabilirlik için bu değeri "
        f".env dosyanıza {_KEY_ENV_VAR}=<anahtar> olarak kopyalamanız ve "
        f".payout_credential.key dosyasını .gitignore'a eklemeniz önerilir."
    )
    return key


def _get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def _encrypt(value):
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(value):
    """Şifreliyse çözer. Eski (şifreleme öncesi) düz metin kayıtlarla geriye
    dönük uyumluluk için: çözme başarısız olursa (InvalidToken) değeri
    olduğu gibi döner — böylece bu güncelleme öncesi girilmiş credential'lar
    bir sonraki set_credential çağrısına kadar bozulmadan çalışmaya devam eder."""
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return value


def _ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS external_credentials (
            marketplace TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS external_scrape_status (
            marketplace TEXT PRIMARY KEY,
            ok INTEGER,
            error TEXT,
            last_attempt_at TEXT,
            last_success_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS external_payout_estimates (
            marketplace TEXT NOT NULL,
            region_code TEXT NOT NULL,
            payment_date INTEGER NOT NULL,
            amount REAL,
            fetched_at TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (marketplace, region_code, payment_date)
        )
    """)


# --- Kimlik bilgisi (credential) ---

def set_credential(marketplace, value):
    encrypted = _encrypt(value)
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute("""
            INSERT INTO external_credentials (marketplace, value, updated_at)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(marketplace) DO UPDATE SET
                value = excluded.value, updated_at = datetime('now', 'localtime')
        """, (marketplace, encrypted))


def get_credential(marketplace):
    """Tam header değerini (çözülmüş) döner (Bearer JWT ya da Cookie) —
    sadece external_payout_scraper.py içeriden kullanmalı, panele/response'a
    asla ham olarak geri verilmemeli."""
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT value, updated_at FROM external_credentials WHERE marketplace = ?",
            (marketplace,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["value"] = _decrypt(result["value"])
        return result


def credential_status(marketplace):
    """Panelde 'kimlik bilgisi tanımlı mı, ne zaman girildi' göstermek için.
    Değerin kendisini DÖNMEZ (sadece var/yok + tarih)."""
    row = get_credential(marketplace)
    return {
        "configured": row is not None,
        "updatedAt": row["updated_at"] if row else None,
    }


# --- Çekim durumu (scrape status) ---

def record_scrape_attempt(marketplace, ok, error=None):
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute("""
            INSERT INTO external_scrape_status (marketplace, ok, error, last_attempt_at, last_success_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'), CASE WHEN ? THEN datetime('now', 'localtime') ELSE NULL END)
            ON CONFLICT(marketplace) DO UPDATE SET
                ok = excluded.ok,
                error = excluded.error,
                last_attempt_at = datetime('now', 'localtime'),
                last_success_at = CASE WHEN ? THEN datetime('now', 'localtime')
                                       ELSE external_scrape_status.last_success_at END
        """, (marketplace, 1 if ok else 0, error, ok, ok))


def get_scrape_status():
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute("SELECT * FROM external_scrape_status").fetchall()
        return {r["marketplace"]: dict(r) for r in rows}


# --- Tahmini ödemeler (estimates) ---

def save_estimates(marketplace, estimates):
    """estimates: [{"region_code": "TR", "payment_date": <ms epoch>, "amount": float}, ...]

    Bu pazaryeri için eski tüm satırları silip yenileriyle DEĞİŞTİRİR (upsert
    değil, tam replace) — çünkü kaynak API her seferinde 'şu andan itibaren
    tüm gelecek ödemeler' listesini döndürüyor; artık listede olmayan bir gün
    gerçekten iptal/birleşmiş olabilir, eski satırı DB'de bırakmak yanlış
    tahmine yol açar."""
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute("DELETE FROM external_payout_estimates WHERE marketplace = ?", (marketplace,))
        if estimates:
            conn.executemany("""
                INSERT INTO external_payout_estimates
                    (marketplace, region_code, payment_date, amount, fetched_at)
                VALUES (:marketplace, :region_code, :payment_date, :amount, datetime('now', 'localtime'))
            """, [{**e, "marketplace": marketplace} for e in estimates])


def get_estimates(marketplace=None):
    with get_connection() as conn:
        _ensure_tables(conn)
        if marketplace:
            rows = conn.execute(
                "SELECT * FROM external_payout_estimates WHERE marketplace = ? ORDER BY payment_date",
                (marketplace,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM external_payout_estimates ORDER BY marketplace, payment_date"
            ).fetchall()
        return [dict(r) for r in rows]
