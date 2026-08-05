"""
database.py
-----------
SQLite katmanı: siparişler, sipariş satırları, Trendyol Finans API'sinden
gelen settlements / otherfinancials kayıtları, kargo faturası kalemleri,
ürün maliyetleri ve senkronizasyon ilerleme durumu.

Tüm upsert fonksiyonları INSERT OR REPLACE mantığıyla çalışır — aynı ID
tekrar geldiğinde (örn. senkronizasyon tekrar çalıştırıldığında) veriyi
günceller, çift satır oluşturmaz.

ÇOKLU PAZARYERİ NOTU:
orders/order_lines tabloları artık (marketplace, shipment_package_id) bileşik
anahtarı kullanıyor. Bunun nedeni: Trendyol ve Hepsiburada'nın paket ID'leri
aynı sayı aralığında çakışabilir; tek başına shipment_package_id'yi anahtar
yapmak, bir platformdaki siparişin diğerinin üzerine yanlışlıkla yazılmasına
yol açabilirdi. init_db() bu geçişi otomatik ve güvenli şekilde yapar —
uygulamayı yeniden başlattığında (app.py çalıştığında) devreye girer.
"""

import sqlite3
from contextlib import contextmanager

DB_PATH = "trendyol_data.db"


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_exists(conn, table):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ensure_column(conn, table, column, definition):
    """Kolon yoksa ekler (idempotent)."""
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _pk_columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall() if row[5] > 0]


# ============================================================
# MİGRASYON GEÇMİŞİ (schema_migrations)
# ============================================================
# ÖNCEKİ DURUM: Yapısal değişiklikler (örn. _migrate_composite_keys) sadece
# tablo/kolon durumuna bakarak kendi idempotency'sini sağlıyordu — bu
# çalışıyor ama HANGİ migrasyonların ne zaman uygulandığına dair hiçbir
# kayıt yoktu (örn. "bu DB'de composite key migrasyonu gerçekten çalıştı mı,
# yoksa tablo zaten mı böyle oluşturulmuştu?" sorusuna DB'nin kendisine
# bakmadan cevap verilemiyordu).
#
# Tam bir migrasyon framework'üne (Alembic vb.) geçiş şu an İSTENMİYOR —
# mevcut ham SQL + idempotent kontrol yaklaşımı küçük/orta ölçekli bu
# proje için yeterli ve çalışıyor; framework geçişi hem risk hem de SQLAlchemy
# model tanımlarını mevcut ham SQL şemasıyla senkron tutma yükü getirir.
# Bunun yerine HAFİF bir iyileştirme: uygulanan migrasyonları schema_migrations
# tablosuna kaydediyoruz. Bu, (a) "bu DB'de hangi migrasyonlar çalıştı"
# sorusuna doğrudan SQL ile cevap verilebilmesini sağlar, (b) yeni
# migrasyonlar için bir ŞABLON sunar (bkz. _MIGRATIONS listesi) — yeni bir
# yapısal değişiklik eklerken sadece listeye bir (isim, fonksiyon) çifti
# eklemek yeterli olur.
#
# Migrasyon fonksiyonlarının KENDİLERİ hâlâ idempotent olmalı (bu tablo
# sadece bir "zaten çalıştı" ön-kontrolü/kaydı, tek güvenlik katmanı değil)
# — böylece schema_migrations tablosu bir şekilde senkronsuz kalsa bile
# (örn. elle DB müdahalesi) migrasyonlar yine de güvenle tekrar çalıştırılabilir.

def _ensure_schema_migrations_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)


def _run_migrations(conn):
    _ensure_schema_migrations_table(conn)
    applied = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    for name, fn in _MIGRATIONS:
        if name in applied:
            continue
        fn(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, datetime('now', 'localtime'))",
            (name,),
        )
        conn.commit()


def get_applied_migrations():
    """Debug/teşhis için: bu DB'de hangi migrasyonların ne zaman uygulandığını döner."""
    with get_connection() as conn:
        _ensure_schema_migrations_table(conn)
        rows = conn.execute("SELECT name, applied_at FROM schema_migrations ORDER BY applied_at").fetchall()
        return [dict(r) for r in rows]


def _migrate_composite_keys(conn):
    """orders ve order_lines'ı marketplace'i de içeren bileşik anahtara taşır.
    Zaten taşınmışsa hiçbir şey yapmaz (idempotent).

    NOT: order_lines, orders'a FOREIGN KEY ile bağlı. PRAGMA foreign_keys=ON
    iken referans edilen bir tabloyu RENAME/DROP etmek "FOREIGN KEY constraint
    failed" hatası verir (veri zaten mevcutsa ortaya çıkar). Bu yüzden
    migrasyon süresince FK enforcement geçici olarak kapatılır."""

    conn.execute("PRAGMA foreign_keys = OFF")

    if _table_exists(conn, "orders") and _pk_columns(conn, "orders") == ["shipment_package_id"]:
        conn.executescript("""
            ALTER TABLE orders RENAME TO orders_old;
            CREATE TABLE orders (
                shipment_package_id INTEGER,
                marketplace TEXT NOT NULL DEFAULT 'trendyol',
                order_number TEXT,
                order_date INTEGER,
                status TEXT,
                customer TEXT,
                cargo_provider TEXT,
                gross_amount REAL,
                discount_amount REAL,
                net_amount REAL,
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (marketplace, shipment_package_id)
            );
            INSERT INTO orders (shipment_package_id, marketplace, order_number, order_date, status,
                                 customer, cargo_provider, gross_amount, discount_amount, net_amount, updated_at)
            SELECT shipment_package_id, marketplace, order_number, order_date, status,
                   customer, cargo_provider, gross_amount, discount_amount, net_amount, updated_at
            FROM orders_old;
            DROP TABLE orders_old;
        """)

    if _table_exists(conn, "order_lines"):
        existing_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='order_lines'"
        ).fetchone()[0]
        if "UNIQUE (marketplace" not in existing_sql and "UNIQUE(marketplace" not in existing_sql:
            conn.executescript("""
                ALTER TABLE order_lines RENAME TO order_lines_old;
                CREATE TABLE order_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shipment_package_id INTEGER,
                    marketplace TEXT NOT NULL DEFAULT 'trendyol',
                    barcode TEXT,
                    merchant_sku TEXT,
                    product_name TEXT,
                    quantity INTEGER,
                    line_unit_price REAL,
                    commission_rate REAL,
                    FOREIGN KEY (marketplace, shipment_package_id) REFERENCES orders(marketplace, shipment_package_id),
                    UNIQUE (marketplace, shipment_package_id, barcode)
                );
                INSERT INTO order_lines (id, shipment_package_id, marketplace, barcode, merchant_sku,
                                          product_name, quantity, line_unit_price, commission_rate)
                SELECT id, shipment_package_id, marketplace, barcode, merchant_sku,
                       product_name, quantity, line_unit_price, commission_rate
                FROM order_lines_old;
                DROP TABLE order_lines_old;
            """)

    # --- 28.07.2026 mimari düzeltmesi: settlements/other_financials/cargo_costs
    # PK'si eskiden sadece "id" idi. HB kayıtları "hb_" önekiyle namespaced
    # olduğu için pratikte çakışma olmuyordu, ama bu KONVANSİYONA dayanıyordu,
    # şema tarafından GARANTİ EDİLMİYORDU. PK'yi (marketplace, id) yaparak
    # çakışma ihtimalini şema seviyesinde imkansız hale getiriyoruz.
    for table, extra_cols_sql, extra_cols_names in [
        ("settlements", """
            transaction_date INTEGER, barcode TEXT, transaction_type TEXT, raw_transaction_type TEXT,
            receipt_id TEXT, description TEXT, debt REAL, credit REAL, payment_period INTEGER,
            commission_rate REAL, commission_amount REAL, seller_revenue REAL, order_number TEXT,
            payment_order_id INTEGER, payment_date INTEGER, shipment_package_id INTEGER
         """, ["transaction_date", "barcode", "transaction_type", "raw_transaction_type", "receipt_id",
               "description", "debt", "credit", "payment_period", "commission_rate", "commission_amount",
               "seller_revenue", "order_number", "payment_order_id", "payment_date", "shipment_package_id"]),
        ("other_financials", """
            transaction_date INTEGER, barcode TEXT, transaction_type TEXT, raw_transaction_type TEXT,
            transaction_sub_type TEXT, receipt_id TEXT, description TEXT, debt REAL, credit REAL,
            order_number TEXT, payment_order_id INTEGER, payment_date INTEGER, shipment_package_id INTEGER
         """, ["transaction_date", "barcode", "transaction_type", "raw_transaction_type",
               "transaction_sub_type", "receipt_id", "description", "debt", "credit",
               "order_number", "payment_order_id", "payment_date", "shipment_package_id"]),
        ("cargo_costs", """
            invoice_serial_number TEXT, shipment_package_id INTEGER, order_number TEXT,
            barcode TEXT, amount REAL, raw_json TEXT
         """, ["invoice_serial_number", "shipment_package_id", "order_number", "barcode", "amount", "raw_json"]),
    ]:
        if _table_exists(conn, table) and _pk_columns(conn, table) == ["id"]:
            cols_csv = ", ".join(extra_cols_names)
            conn.executescript(f"""
                ALTER TABLE {table} RENAME TO {table}_old;
                CREATE TABLE {table} (
                    id TEXT NOT NULL,
                    marketplace TEXT NOT NULL DEFAULT 'trendyol',
                    {extra_cols_sql},
                    PRIMARY KEY (marketplace, id)
                );
                INSERT INTO {table} (id, marketplace, {cols_csv})
                SELECT id, marketplace, {cols_csv}
                FROM {table}_old;
                DROP TABLE {table}_old;
            """)

    conn.execute("PRAGMA foreign_keys = ON")


# Uygulanacak yapısal migrasyonların sıralı listesi. Yeni bir yapısal
# değişiklik eklerken: (1) idempotent bir fonksiyon yaz, (2) buraya
# (benzersiz_isim, fonksiyon) olarak ekle. _run_migrations sırayla çalıştırır
# ve schema_migrations tablosuna kaydeder.
_MIGRATIONS = [
    ("2026_07_28_composite_marketplace_keys", _migrate_composite_keys),
]


def init_db():
    with get_connection() as conn:
        c = conn.cursor()

        # --- Siparişler (getShipmentPackages'tan) ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            shipment_package_id INTEGER PRIMARY KEY,
            order_number TEXT,
            order_date INTEGER,
            status TEXT,
            customer TEXT,
            cargo_provider TEXT,
            gross_amount REAL,
            discount_amount REAL,
            net_amount REAL,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """)

        # --- Sipariş satırları ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS order_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shipment_package_id INTEGER,
            barcode TEXT,
            merchant_sku TEXT,
            product_name TEXT,
            quantity INTEGER,
            line_unit_price REAL,
            commission_rate REAL,
            FOREIGN KEY (shipment_package_id) REFERENCES orders(shipment_package_id),
            UNIQUE (shipment_package_id, barcode)
        )
        """)

        # --- Finans API: settlements (Sale, Return, vb.) ---
        # NOT (28.07.2026): PK artık (marketplace, id) — bkz. _migrate_composite_keys
        # docstring'i, tek başına "id"ye güvenmek şema seviyesinde bir garanti değildi.
        c.execute("""
        CREATE TABLE IF NOT EXISTS settlements (
            id TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'trendyol',
            transaction_date INTEGER,
            barcode TEXT,
            transaction_type TEXT,
            raw_transaction_type TEXT,
            receipt_id TEXT,
            description TEXT,
            debt REAL,
            credit REAL,
            payment_period INTEGER,
            commission_rate REAL,
            commission_amount REAL,
            seller_revenue REAL,
            order_number TEXT,
            payment_order_id INTEGER,
            payment_date INTEGER,
            shipment_package_id INTEGER,
            PRIMARY KEY (marketplace, id)
        )
        """)

        # --- Finans API: otherfinancials ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS other_financials (
            id TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'trendyol',
            transaction_date INTEGER,
            barcode TEXT,
            transaction_type TEXT,
            raw_transaction_type TEXT,
            transaction_sub_type TEXT,
            receipt_id TEXT,
            description TEXT,
            debt REAL,
            credit REAL,
            order_number TEXT,
            payment_order_id INTEGER,
            payment_date INTEGER,
            shipment_package_id INTEGER,
            PRIMARY KEY (marketplace, id)
        )
        """)

        # --- Kargo faturası kalemleri ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS cargo_costs (
            id TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'trendyol',
            invoice_serial_number TEXT,
            shipment_package_id INTEGER,
            order_number TEXT,
            barcode TEXT,
            amount REAL,
            raw_json TEXT,
            PRIMARY KEY (marketplace, id)
        )
        """)

        # --- Ürün maliyetleri (Excel'den) ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS product_costs (
            sku TEXT PRIMARY KEY,
            product_name TEXT,
            sale_price_incl_vat REAL,
            cost_incl_vat REAL,
            sale_price_excl_vat REAL,
            cost_excl_vat REAL,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """)

        # --- Canlı stok + kullanıcı tanımlı min eşik (Ürün Ayarları /
        #     Düşük Stok Uyarısı). quantity: Trendyol/HB API senkronundan
        #     gelir (bkz. stock_client.py); min_stock_threshold: kullanıcı
        #     elle girer. sku: trendyol -> stockCode (yoksa barcode),
        #     hepsiburada -> merchantSku. ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS product_stock (
            marketplace TEXT NOT NULL,
            sku TEXT NOT NULL,
            barcode TEXT,
            quantity INTEGER,
            min_stock_threshold INTEGER,
            stock_updated_at TEXT,
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (marketplace, sku)
        )
        """)

        # --- Aylık sabit giderler (kira, personel, muhasebe, vb. — sipariş
        #     bazlı değil, ay bazlı sabit tutarlar). Her kalem ayrı satır;
        #     bir aya ait toplam SUM(amount) WHERE month=? ile hesaplanır. ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS fixed_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL,
            label TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """)

        # --- Senkronizasyon durumu (basit key-value) ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # --- Trendyol senkronizasyon ilerlemesi ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS sync_progress (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT,
            current_step INTEGER,
            total_steps INTEGER,
            message TEXT,
            error TEXT,
            started_at TEXT,
            updated_at TEXT
        )
        """)

        # --- Hepsiburada senkronizasyon ilerlemesi (Trendyol'dan bağımsız,
        #     ikisi aynı anda çalışabilir) ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS hb_sync_progress (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT,
            current_step INTEGER,
            total_steps INTEGER,
            message TEXT,
            error TEXT,
            started_at TEXT,
            updated_at TEXT
        )
        """)

        conn.commit()

        # --- Otomatik migrasyon: marketplace desteği ---
        _ensure_column(conn, "orders", "marketplace", "TEXT NOT NULL DEFAULT 'trendyol'")
        _ensure_column(conn, "order_lines", "marketplace", "TEXT NOT NULL DEFAULT 'trendyol'")
        # settlements/other_financials/cargo_costs eskiden sadece Trendyol Finans
        # API'sinden besleniyordu (marketplace kolonu yoktu). Hepsiburada Muhasebe
        # API'si entegre edildiği için buraya da marketplace ekliyoruz; var olan
        # tüm satırlar zaten Trendyol kaynaklı olduğundan varsayılan 'trendyol'.
        _ensure_column(conn, "settlements", "marketplace", "TEXT NOT NULL DEFAULT 'trendyol'")
        _ensure_column(conn, "other_financials", "marketplace", "TEXT NOT NULL DEFAULT 'trendyol'")
        _ensure_column(conn, "cargo_costs", "marketplace", "TEXT NOT NULL DEFAULT 'trendyol'")
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_marketplace ON orders(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_lines_marketplace ON order_lines(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_settlements_marketplace ON settlements(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_other_financials_marketplace ON other_financials(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cargo_costs_marketplace ON cargo_costs(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fixed_expenses_month ON fixed_expenses(month)")
        conn.commit()


def upsert_orders(rows):
    """rows: dict listesi. Her dict 'marketplace' alanı içermeli
    (örn. 'trendyol' veya 'hepsiburada')."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO orders (shipment_package_id, marketplace, order_number, order_date, status,
                                 customer, cargo_provider, gross_amount, discount_amount, net_amount)
            VALUES (:shipment_package_id, :marketplace, :order_number, :order_date, :status,
                    :customer, :cargo_provider, :gross_amount, :discount_amount, :net_amount)
            ON CONFLICT(marketplace, shipment_package_id) DO UPDATE SET
                order_number=excluded.order_number,
                order_date=excluded.order_date,
                status=excluded.status,
                customer=excluded.customer,
                cargo_provider=excluded.cargo_provider,
                gross_amount=excluded.gross_amount,
                discount_amount=excluded.discount_amount,
                net_amount=excluded.net_amount,
                updated_at=datetime('now', 'localtime')
        """, rows)


def upsert_order_lines(rows):
    """rows: dict listesi. Her dict 'marketplace' alanı içermeli."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO order_lines (shipment_package_id, marketplace, barcode, merchant_sku, product_name,
                                      quantity, line_unit_price, commission_rate)
            VALUES (:shipment_package_id, :marketplace, :barcode, :merchant_sku, :product_name,
                    :quantity, :line_unit_price, :commission_rate)
            ON CONFLICT(marketplace, shipment_package_id, barcode) DO UPDATE SET
                merchant_sku=excluded.merchant_sku,
                product_name=excluded.product_name,
                quantity=excluded.quantity,
                line_unit_price=excluded.line_unit_price,
                commission_rate=excluded.commission_rate
        """, rows)


def upsert_settlements(rows):
    """rows: dict listesi. 'marketplace' alanı opsiyoneldir; verilmezse
    geriye dönük uyumluluk için 'trendyol' varsayılır (mevcut trendyol_finance.py
    çağrıları bu alanı göndermiyor)."""
    if not rows:
        return
    for r in rows:
        r.setdefault("marketplace", "trendyol")
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO settlements (id, marketplace, transaction_date, barcode, transaction_type, raw_transaction_type,
                                      receipt_id, description, debt, credit, payment_period, commission_rate,
                                      commission_amount, seller_revenue, order_number,
                                      payment_order_id, payment_date, shipment_package_id)
            VALUES (:id, :marketplace, :transaction_date, :barcode, :transaction_type, :raw_transaction_type,
                    :receipt_id, :description, :debt, :credit, :payment_period, :commission_rate,
                    :commission_amount, :seller_revenue, :order_number,
                    :payment_order_id, :payment_date, :shipment_package_id)
            ON CONFLICT(marketplace, id) DO UPDATE SET
                transaction_type=excluded.transaction_type,
                raw_transaction_type=excluded.raw_transaction_type,
                debt=excluded.debt, credit=excluded.credit,
                seller_revenue=excluded.seller_revenue, commission_amount=excluded.commission_amount,
                payment_order_id=excluded.payment_order_id, payment_date=excluded.payment_date
        """, rows)


def upsert_other_financials(rows):
    """rows: dict listesi. 'marketplace' opsiyonel, verilmezse 'trendyol' varsayılır."""
    if not rows:
        return
    for r in rows:
        r.setdefault("marketplace", "trendyol")
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO other_financials (id, marketplace, transaction_date, barcode, transaction_type, raw_transaction_type,
                                           transaction_sub_type, receipt_id, description, debt, credit,
                                           order_number, payment_order_id, payment_date, shipment_package_id)
            VALUES (:id, :marketplace, :transaction_date, :barcode, :transaction_type, :raw_transaction_type,
                    :transaction_sub_type, :receipt_id, :description, :debt, :credit,
                    :order_number, :payment_order_id, :payment_date, :shipment_package_id)
            ON CONFLICT(marketplace, id) DO UPDATE SET
                transaction_type=excluded.transaction_type,
                raw_transaction_type=excluded.raw_transaction_type,
                debt=excluded.debt, credit=excluded.credit,
                payment_order_id=excluded.payment_order_id, payment_date=excluded.payment_date
        """, rows)


def upsert_cargo_costs(rows):
    """rows: dict listesi. 'marketplace' opsiyonel, verilmezse 'trendyol' varsayılır."""
    if not rows:
        return
    for r in rows:
        r.setdefault("marketplace", "trendyol")
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO cargo_costs (id, marketplace, invoice_serial_number, shipment_package_id,
                                      order_number, barcode, amount, raw_json)
            VALUES (:id, :marketplace, :invoice_serial_number, :shipment_package_id,
                    :order_number, :barcode, :amount, :raw_json)
            ON CONFLICT(marketplace, id) DO UPDATE SET
                amount=excluded.amount,
                shipment_package_id=excluded.shipment_package_id,
                order_number=excluded.order_number,
                barcode=excluded.barcode,
                raw_json=excluded.raw_json
        """, rows)


def upsert_product_costs(rows):
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO product_costs (sku, product_name, sale_price_incl_vat, cost_incl_vat,
                                        sale_price_excl_vat, cost_excl_vat)
            VALUES (:sku, :product_name, :sale_price_incl_vat, :cost_incl_vat,
                    :sale_price_excl_vat, :cost_excl_vat)
            ON CONFLICT(sku) DO UPDATE SET
                product_name=excluded.product_name,
                sale_price_incl_vat=excluded.sale_price_incl_vat,
                cost_incl_vat=excluded.cost_incl_vat,
                sale_price_excl_vat=excluded.sale_price_excl_vat,
                cost_excl_vat=excluded.cost_excl_vat,
                updated_at=datetime('now', 'localtime')
        """, rows)


# --- Canlı stok / düşük stok uyarısı ---

def upsert_product_stock_quantities(rows):
    """rows: [{marketplace, sku, barcode, quantity}] — stock_client.py'den
    (API senkronu) gelir. min_stock_threshold'a DOKUNMAZ (kullanıcı ayrı
    ayarlıyor, bkz. upsert_product_stock_threshold)."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO product_stock (marketplace, sku, barcode, quantity, stock_updated_at, updated_at)
            VALUES (:marketplace, :sku, :barcode, :quantity, datetime('now', 'localtime'), datetime('now', 'localtime'))
            ON CONFLICT(marketplace, sku) DO UPDATE SET
                barcode=excluded.barcode,
                quantity=excluded.quantity,
                stock_updated_at=datetime('now', 'localtime'),
                updated_at=datetime('now', 'localtime')
        """, rows)


def upsert_product_stock_threshold(marketplace, sku, min_stock_threshold):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO product_stock (marketplace, sku, min_stock_threshold, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(marketplace, sku) DO UPDATE SET
                min_stock_threshold=excluded.min_stock_threshold,
                updated_at=datetime('now', 'localtime')
        """, (marketplace, sku, min_stock_threshold))


def list_product_stock(marketplace=None):
    with get_connection() as conn:
        if marketplace:
            rows = conn.execute(
                "SELECT * FROM product_stock WHERE marketplace = ? ORDER BY quantity ASC",
                (marketplace,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM product_stock ORDER BY quantity ASC").fetchall()
        return [dict(r) for r in rows]


# --- Aylık sabit giderler ---

def list_fixed_expenses(month=None):
    """month verilirse ('YYYY-MM') sadece o aya ait kalemler, verilmezse tüm
    kalemler (en yeni ay önce) döner."""
    with get_connection() as conn:
        if month:
            rows = conn.execute(
                "SELECT * FROM fixed_expenses WHERE month = ? ORDER BY id DESC", (month,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM fixed_expenses ORDER BY month DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def upsert_fixed_expense(row):
    """row: {id?, month, label, amount, note?}. 'id' verilmişse günceller,
    verilmemişse yeni kalem ekler. Eklenen/güncellenen satırı dict olarak döner."""
    with get_connection() as conn:
        if row.get("id"):
            conn.execute("""
                UPDATE fixed_expenses
                SET month = :month, label = :label, amount = :amount, note = :note,
                    updated_at = datetime('now', 'localtime')
                WHERE id = :id
            """, row)
            new_id = row["id"]
        else:
            cur = conn.execute("""
                INSERT INTO fixed_expenses (month, label, amount, note)
                VALUES (:month, :label, :amount, :note)
            """, row)
            new_id = cur.lastrowid
        result = conn.execute("SELECT * FROM fixed_expenses WHERE id = ?", (new_id,)).fetchone()
        return dict(result) if result else None


def delete_fixed_expense(expense_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM fixed_expenses WHERE id = ?", (expense_id,))


def get_fixed_expenses_by_month(start_month, end_month):
    """start_month/end_month: 'YYYY-MM' (dahil). Ay -> toplam tutar sözlüğü döner.
    Aylık kâr trendinde gerçek net kârı hesaplamak için kullanılır."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT month, SUM(amount) AS total
            FROM fixed_expenses
            WHERE month BETWEEN ? AND ?
            GROUP BY month
        """, (start_month, end_month)).fetchall()
        return {r["month"]: r["total"] or 0.0 for r in rows}


def get_sync_state(key):
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_sync_state(key, value):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sync_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))


# --- Senkronizasyon ilerlemesi ---
# marketplace='trendyol' -> sync_progress tablosu (mevcut davranış, geriye dönük uyumlu)
# marketplace='hepsiburada' -> hb_sync_progress tablosu

def _progress_table(marketplace):
    return "hb_sync_progress" if marketplace == "hepsiburada" else "sync_progress"


def start_sync_progress(total_steps, message="Başlatılıyor…", marketplace="trendyol"):
    table = _progress_table(marketplace)
    with get_connection() as conn:
        conn.execute(f"""
            INSERT INTO {table} (id, status, current_step, total_steps, message, error, started_at, updated_at)
            VALUES (1, 'running', 0, ?, ?, NULL, datetime('now', 'localtime'), datetime('now', 'localtime'))
            ON CONFLICT(id) DO UPDATE SET
                status='running', current_step=0, total_steps=excluded.total_steps,
                message=excluded.message, error=NULL,
                started_at=datetime('now', 'localtime'), updated_at=datetime('now', 'localtime')
        """, (total_steps, message))


def update_sync_progress(current_step=None, total_steps=None, message=None, marketplace="trendyol"):
    table = _progress_table(marketplace)
    with get_connection() as conn:
        row = conn.execute(f"SELECT current_step, total_steps, message FROM {table} WHERE id = 1").fetchone()
        if row is None:
            return
        cur = current_step if current_step is not None else row["current_step"]
        tot = total_steps if total_steps is not None else row["total_steps"]
        msg = message if message is not None else row["message"]
        conn.execute(f"""
            UPDATE {table} SET current_step = ?, total_steps = ?, message = ?, updated_at = datetime('now', 'localtime')
            WHERE id = 1
        """, (cur, tot, msg))


def finish_sync_progress(message="Tamamlandı", marketplace="trendyol"):
    table = _progress_table(marketplace)
    with get_connection() as conn:
        conn.execute(f"""
            UPDATE {table} SET status = 'done', message = ?, updated_at = datetime('now', 'localtime') WHERE id = 1
        """, (message,))


def fail_sync_progress(error_message, marketplace="trendyol"):
    table = _progress_table(marketplace)
    with get_connection() as conn:
        conn.execute(f"""
            UPDATE {table} SET status = 'error', error = ?, updated_at = datetime('now', 'localtime') WHERE id = 1
        """, (error_message,))


def get_sync_progress(marketplace="trendyol"):
    table = _progress_table(marketplace)
    with get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = 1").fetchone()
        return dict(row) if row else {
            "status": "idle", "current_step": 0, "total_steps": 0,
            "message": None, "error": None, "started_at": None, "updated_at": None,
        }


if __name__ == "__main__":
    init_db()
    print(f"Veritabanı hazır: {DB_PATH}")