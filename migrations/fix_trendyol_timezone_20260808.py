"""
migrations/fix_trendyol_timezone_20260808.py
-----------------------------------------------
TEK SEFERLİK migration. 08.08.2026'da bulunan timezone bug'ını mevcut
veritabanındaki geçmiş Trendyol satırları için düzeltir.

Bug: Trendyol API'sinin epoch-ms tarih alanları (orderDate, transactionDate,
paymentDate) gerçek UTC değil -- Europe/Istanbul yerel saatini doğrudan UTC
epoch gibi kodluyor. sync_core.normalize_trendyol_epoch_ms() bu koddan sonraki
YENİ senkronizasyonlarda otomatik düzeltiyor; bu script GEÇMİŞTE zaten yazılmış
satırları düzeltir (marketplace='trendyol' olanlar, HB'ye DOKUNULMAZ).

KULLANIM:
    # 1) Önce mutlaka yedek al:
    cp trendyol_data.db trendyol_data.db.bak-$(date +%Y%m%d-%H%M%S)

    # 2) Neyin değişeceğini önce SADECE görmek için (DB'ye yazmaz):
    python3 migrations/fix_trendyol_timezone_20260808.py --dry-run

    # 3) Uygula:
    python3 migrations/fix_trendyol_timezone_20260808.py --apply

Not: Bu script'i çalıştırırken Flask uygulamasını (app.py) KAPATIN — aynı
SQLite dosyasına eşzamanlı yazım çakışmasını önlemek için.
"""
import argparse
import sqlite3
import sys

DB_PATH = "trendyol_data.db"
OFFSET_MS = 3 * 60 * 60 * 1000  # Europe/Istanbul = UTC+3

# (tablo, düzeltilecek sütun, marketplace filtresi var mı)
TARGETS = [
    ("orders", "order_date", True),
    ("settlements", "transaction_date", True),
    ("settlements", "payment_date", True),
    ("other_financials", "transaction_date", True),
    ("other_financials", "payment_date", True),
]


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Değişiklikleri gerçekten yaz")
    parser.add_argument("--dry-run", action="store_true", help="Sadece kaç satır etkilenecek göster")
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        print("Ya --dry-run ya da --apply belirtmelisiniz. Örnek:")
        print("  python3 migrations/fix_trendyol_timezone_20260808.py --dry-run")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total_affected = 0
    for table, column, marketplace_filtered in TARGETS:
        if not _table_exists(conn, table):
            print(f"[atlandı] Tablo yok: {table}")
            continue
        if not _column_exists(conn, table, column):
            print(f"[atlandı] Sütun yok: {table}.{column}")
            continue

        where = f"WHERE {column} IS NOT NULL"
        if marketplace_filtered and _column_exists(conn, table, "marketplace"):
            where += " AND marketplace = 'trendyol'"

        count_row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} {where}").fetchone()
        count = count_row["c"]
        total_affected += count
        print(f"{table}.{column}: {count} satır etkilenecek (-{OFFSET_MS // 3600000} saat)")

        if args.apply and count > 0:
            conn.execute(
                f"UPDATE {table} SET {column} = {column} - ? {where}",
                (OFFSET_MS,),
            )

    if args.apply:
        conn.commit()
        print(f"\nTamamlandı. Toplam {total_affected} satır güncellendi.")
    else:
        print(f"\n[DRY-RUN] Hiçbir şey yazılmadı. Toplam {total_affected} satır etkilenecekti.")
        print("Uygulamak için: python3 migrations/fix_trendyol_timezone_20260808.py --apply")

    conn.close()


if __name__ == "__main__":
    main()
