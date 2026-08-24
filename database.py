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
def _migrate_growth_columns(conn):
    """order_lines'a kampanya/indirim kolonlarını ekler (Gelir Kaçakları
    ekranı için). Nullable -- gecmis satirlarda NULL kalir, yeni senkronda
    dolar. apply_growth_patch.py tarafindan otomatik eklendi."""
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(order_lines)")}
    if "sales_campaign_id" not in existing_cols:
        conn.execute("ALTER TABLE order_lines ADD COLUMN sales_campaign_id TEXT")
    if "seller_discount" not in existing_cols:
        conn.execute("ALTER TABLE order_lines ADD COLUMN seller_discount REAL")
    if "marketplace_discount" not in existing_cols:
        conn.execute("ALTER TABLE order_lines ADD COLUMN marketplace_discount REAL")


def _migrate_supplier_debt_v2(conn):
    """Toptancı borcu düzeltmeleri (11.08.2026):
    1) tedarikci_borc_hareketleri.tip CHECK kısıtına 'duzeltme' eklenir —
       elle bakiye düzeltme/sıfırlama 'odeme'den ayrı tutulur, böylece
       son ödeme tarihi sadece gerçek ödemeleri yansıtır.
    2) Faturayla (31.07.2026 tarihli tedarikçi faturası) karşılaştırılıp
       KDV HARİÇ yazıldığı tespit edilen 4 SKU'nun cost_incl_vat değeri
       KDV dahile düzeltilir.
    3) SKU'su ve adı olmayan bozuk product_costs satırı silinir.
    4) Her tedarikçi için o ana kadar birikmiş bakiye, dengeleyici bir
       'duzeltme' hareketiyle sıfırlanır (geçmiş hareketler SİLİNMEZ,
       sadece bakiye kapatılır). 03.08.2026 sonrası satışlar normal
       şekilde 'satis' hareketleriyle birikmeye devam eder."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tedarikci_borc_hareketleri'"
    ).fetchone()
    if row and "'duzeltme'" not in row[0]:
        conn.executescript("""
            ALTER TABLE tedarikci_borc_hareketleri RENAME TO tedarikci_borc_hareketleri_old;
            CREATE TABLE tedarikci_borc_hareketleri (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tedarikci_id INTEGER NOT NULL REFERENCES tedarikciler(id),
                tip TEXT NOT NULL CHECK(tip IN ('satis','odeme','duzeltme')),
                tutar REAL NOT NULL,
                order_line_key TEXT,
                aciklama TEXT,
                tarih TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(order_line_key, tip)
            );
            INSERT INTO tedarikci_borc_hareketleri
                (id, tedarikci_id, tip, tutar, order_line_key, aciklama, tarih)
            SELECT id, tedarikci_id, tip, tutar, order_line_key, aciklama, tarih
            FROM tedarikci_borc_hareketleri_old;
            DROP TABLE tedarikci_borc_hareketleri_old;
        """)

    kdv_duzeltmeleri = {
        "SH-TDS-01": 215.00,
        "SH-PHM-001": 215.00,
        "SH-8IN1-METER": 1215.00,
        "SH-10TF-BG": 2575.00,
        'SH-10"TF-BG': 2575.00,
    }
    for sku, dogru_tutar in kdv_duzeltmeleri.items():
        conn.execute("UPDATE product_costs SET cost_incl_vat = ? WHERE sku = ?", (dogru_tutar, sku))

    conn.execute("DELETE FROM product_costs WHERE sku IS NULL AND product_name IS NULL")

    for t in conn.execute("SELECT id FROM tedarikciler").fetchall():
        bakiye = conn.execute("""
            SELECT COALESCE(SUM(CASE
                WHEN tip='satis' THEN tutar
                WHEN tip='odeme' THEN -tutar
                WHEN tip='duzeltme' THEN tutar
                ELSE 0 END), 0)
            FROM tedarikci_borc_hareketleri WHERE tedarikci_id = ?
        """, (t["id"],)).fetchone()[0]
        if round(bakiye, 2) != 0:
            conn.execute("""
                INSERT INTO tedarikci_borc_hareketleri (tedarikci_id, tip, tutar, aciklama)
                VALUES (?, 'duzeltme', ?, ?)
            """, (t["id"], -round(bakiye, 2), "Gecmis borc sifirlama (11.08.2026) - 03.08.2026 oncesi odendi"))


_MIGRATIONS = [
    ("2026_07_28_composite_marketplace_keys", _migrate_composite_keys),
    ("2026_08_09_growth_columns", _migrate_growth_columns),
    ("2026_08_11_supplier_debt_v2", _migrate_supplier_debt_v2),
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

        # --- Toptancı borcu: tedarikçiler + borç defteri (satış borcu birikir,
        #     ödeme girişiyle düşer). product_costs.tedarikci_id ile bir SKU'nun
        #     hangi tedarikçiden geldiği işaretlenir; o SKU satıldıkça
        #     (KDV dahil alış fiyatı x adet) kadar 'satis' hareketi otomatik
        #     eklenir (bkz. _sync_supplier_debt, upsert_order_lines içinden
        #     çağrılır). 11.08.2026 eklendi. ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS tedarikciler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS tedarikci_borc_hareketleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tedarikci_id INTEGER NOT NULL REFERENCES tedarikciler(id),
            tip TEXT NOT NULL CHECK(tip IN ('satis','odeme')),
            tutar REAL NOT NULL,
            order_line_key TEXT,
            aciklama TEXT,
            tarih TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(order_line_key, tip)
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

        # --- Ürün görselleri (şu an sadece Trendyol — "Product Filter -
        #     Approved Product v2" servisinden gelen images[0].url). sku:
        #     product_stock/product_costs ile aynı kavram (trendyol ->
        #     stockCode, yoksa barcode). Ayrı tabloda tutuluyor çünkü
        #     product_stock her stok senkronunda upsert ediliyor ve farklı,
        #     daha sık çalışan bir endpoint kullanıyor (inventory-and-price,
        #     images DÖNMÜYOR) — ikisini karıştırmamak için ayrı. ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS product_images (
            marketplace TEXT NOT NULL,
            sku TEXT NOT NULL,
            image_url TEXT,
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (marketplace, sku)
        )
        """)

        # --- Hepsiburada ürün yorumları (review_contents) — 23.08.2026 Faz 0
        #     denetiminde doğrulanan ApprovedUserContents API'sinden gelir.
        #     PK (marketplace, external_review_id) — settlements/cargo_costs ile
        #     AYNI (marketplace, id) bileşik-anahtar deseni: dış kaynaklı ID'yi
        #     doğrudan PK yapıyoruz, ayrı bir internal UUID tutmuyoruz (bkz.
        #     HB_Review_Scraper_Audit_Mimari_Raporu.md Bölüm E).
        #     review.content NULL OLABİLİR (Faz 0: 538 review'ın 335'i null) —
        #     kolon NOT NULL DEĞİL, "no silent data absence" prensibiyle NULL
        #     açıkça NULL kalır, boş string'e çevrilmez. ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS review_contents (
            external_review_id TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'hepsiburada',
            product_sku TEXT,
            product_url TEXT,
            star INTEGER,
            content TEXT,
            created_at TEXT,
            merchant_id TEXT,
            merchant_name TEXT,
            is_purchase_verified INTEGER,
            synced_at TEXT DEFAULT (datetime('now', 'localtime')),
            raw_json TEXT,
            PRIMARY KEY (marketplace, external_review_id)
        )
        """)

        # --- Hepsiburada review "family" keşif eşlemesi — aynı ürünün farklı
        #     varyant SKU'larının (sibling) AYNI review havuzunu paylaştığı
        #     Faz 0'da doğrulandı (bkz. rapor Bölüm 3). Her order_lines.barcode
        #     için API'yi tekrar tekrar sorgulamamak amacıyla, bir barkod bir
        #     kez sorgulanıp response'taki product.sku seti "aile" olarak
        #     kaydedilir; ailenin representative_sku'su (deterministik: en
        #     küçük sku, alfabetik) sonraki senkronlarda TEK başına sorgulanır.
        #     family_skus: virgülle ayrılmış, debug/izlenebilirlik için (ayrı
        #     bir review_families/review_family_members join tablosu YERİNE
        #     tek düz tablo tercih edildi — product_stock/product_images'daki
        #     "(marketplace, key) PK'li tek tablo" konvansiyonuyla tutarlı,
        #     projede hiçbir yerde ayrı bir ilişki/join tablosu deseni yok). ---
        c.execute("""
        CREATE TABLE IF NOT EXISTS hb_review_family_map (
            marketplace TEXT NOT NULL DEFAULT 'hepsiburada',
            barcode TEXT NOT NULL,
            representative_sku TEXT NOT NULL,
            family_skus TEXT,
            discovered_at TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (marketplace, barcode)
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
        # 11.08.2026: toptancı borcu — bir SKU'nun hangi tedarikçiden geldiğini işaretler.
        _ensure_column(conn, "product_costs", "tedarikci_id", "INTEGER REFERENCES tedarikciler(id)")
        conn.commit()

        _run_migrations(conn)
        conn.commit()

        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_marketplace ON orders(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_lines_marketplace ON order_lines(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_settlements_marketplace ON settlements(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_other_financials_marketplace ON other_financials(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cargo_costs_marketplace ON cargo_costs(marketplace)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fixed_expenses_month ON fixed_expenses(month)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review_contents_product_sku ON review_contents(product_sku)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review_contents_marketplace ON review_contents(marketplace)")
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


# Bu durumlarda sipariş iptal/iade olmuş sayılır, borç hareketi YAZILMAZ.
# Trendyol: Cancelled/Returned. Hepsiburada: ClaimCreated (iade/şikayet
# talebi oluşturulmuş). 11.08.2026 eklendi.
_EXCLUDED_ORDER_STATUSES = {
    "trendyol": {"Cancelled", "Returned"},
    "hepsiburada": {"ClaimCreated"},
}


def _sync_supplier_debt(conn, rows):
    """order_lines upsert edilirken çağrılır: tedarikçisi atanmış SKU'lar için
    (product_costs.cost_incl_vat x adet) kadar 'satis' borç hareketi ekler.
    İptal/iade olmuş siparişler hariç tutulur (bkz. _EXCLUDED_ORDER_STATUSES).

    SKU eşleşmesi: COALESCE(merchant_sku, barcode) = product_costs.sku —
    product_costs/product_stock'ta zaten kullanılan aynı konvansiyon (trendyol
    -> stockCode/barcode, hepsiburada -> merchantSku).

    İdempotent: (order_line_key, tip='satis') UNIQUE olduğu için aynı satır
    (marketplace, shipment_package_id, barcode) tekrar senkronize edilirse
    borç ikinci kez eklenmez (INSERT OR IGNORE). NOT: bir satırın miktarı
    sonradan değişirse (ör. Trendyol/HB tarafında düzeltme) borç kaydı
    GÜNCELLENMEZ, çünkü ilk senkronda zaten yazılmış olur — bu istisnai bir
    durum, fark edilirse elle bir 'odeme' veya 'duzeltme' hareketiyle telafi
    edilmesi gerekir. NOT 2: bir sipariş 'satis' borcu yazıldıktan SONRA
    iptal/iade olursa, bu hareket otomatik geri alınmaz (aynı istisnai durum)."""
    tedarikcili_skus = {r[0] for r in conn.execute(
        "SELECT sku FROM product_costs WHERE tedarikci_id IS NOT NULL"
    ).fetchall()}
    if not tedarikcili_skus:
        return

    placeholders = ",".join("?" for _ in tedarikcili_skus)
    cost_map = {
        row["sku"]: (row["cost_incl_vat"], row["tedarikci_id"])
        for row in conn.execute(
            f"SELECT sku, cost_incl_vat, tedarikci_id FROM product_costs WHERE sku IN ({placeholders})",
            list(tedarikcili_skus),
        ).fetchall()
        if row["cost_incl_vat"] is not None
    }
    if not cost_map:
        return

    # İlgili siparişlerin durumlarını çek (iptal/iade filtresi için)
    pkg_ids = {(r.get("marketplace"), r.get("shipment_package_id")) for r in rows}
    status_map = {}
    for mp in {p[0] for p in pkg_ids}:
        ids = [p[1] for p in pkg_ids if p[0] == mp]
        if not ids:
            continue
        id_placeholders = ",".join("?" for _ in ids)
        for row in conn.execute(
            f"""SELECT shipment_package_id, status FROM orders
                WHERE marketplace = ? AND shipment_package_id IN ({id_placeholders})""",
            [mp, *ids],
        ).fetchall():
            status_map[(mp, row["shipment_package_id"])] = row["status"]

    debt_rows = []
    for r in rows:
        sku = r.get("merchant_sku") or r.get("barcode")
        if sku not in cost_map:
            continue
        adet = r.get("quantity") or 0
        if not adet:
            continue
        mp = r.get("marketplace")
        status = status_map.get((mp, r.get("shipment_package_id")))
        if status in _EXCLUDED_ORDER_STATUSES.get(mp, set()):
            continue
        cost_incl_vat, tedarikci_id = cost_map[sku]
        line_key = f"{r.get('marketplace')}:{r.get('shipment_package_id')}:{r.get('barcode')}"
        debt_rows.append({
            "tedarikci_id": tedarikci_id,
            "tutar": round(cost_incl_vat * adet, 2),
            "order_line_key": line_key,
            "aciklama": f"Satis - {sku} x{adet}",
        })
    if not debt_rows:
        return

    conn.executemany("""
        INSERT OR IGNORE INTO tedarikci_borc_hareketleri
            (tedarikci_id, tip, tutar, order_line_key, aciklama)
        VALUES (:tedarikci_id, 'satis', :tutar, :order_line_key, :aciklama)
    """, debt_rows)


def upsert_order_lines(rows):
    """rows: dict listesi. Her dict 'marketplace' alanı içermeli."""
    if not rows:
        return
    for r in rows:
        r.setdefault("sales_campaign_id", None)
        r.setdefault("seller_discount", None)
        r.setdefault("marketplace_discount", None)
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO order_lines (shipment_package_id, marketplace, barcode, merchant_sku, product_name,
                                      quantity, line_unit_price, commission_rate,
                                      sales_campaign_id, seller_discount, marketplace_discount)
            VALUES (:shipment_package_id, :marketplace, :barcode, :merchant_sku, :product_name,
                    :quantity, :line_unit_price, :commission_rate,
                    :sales_campaign_id, :seller_discount, :marketplace_discount)
            ON CONFLICT(marketplace, shipment_package_id, barcode) DO UPDATE SET
                merchant_sku=excluded.merchant_sku,
                product_name=excluded.product_name,
                quantity=excluded.quantity,
                line_unit_price=excluded.line_unit_price,
                commission_rate=excluded.commission_rate,
                sales_campaign_id=excluded.sales_campaign_id,
                seller_discount=excluded.seller_discount,
                marketplace_discount=excluded.marketplace_discount
        """, rows)
        _sync_supplier_debt(conn, rows)


def delete_hb_placeholder_orders(order_numbers):
    """/orders'tan (henüz paketlenmemiş) yazılan negatif shipment_package_id'li
    placeholder satırları, sipariş gerçekten paketlenip /packages'ta (pozitif,
    gerçek packageNumber ile) göründüğünde siler -- aksi halde aynı sipariş
    hem placeholder hem gerçek paket olarak DB'de durur ve gelir/kâr
    raporlarında ÇİFT SAYILIR.
    order_numbers: hepsiburada order_number değerlerinden oluşan liste/set."""
    order_numbers = [n for n in order_numbers if n]
    if not order_numbers:
        return
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in order_numbers)
        stale_ids = [
            row[0] for row in conn.execute(
                f"""SELECT shipment_package_id FROM orders
                    WHERE marketplace = 'hepsiburada'
                      AND shipment_package_id < 0
                      AND order_number IN ({placeholders})""",
                order_numbers,
            ).fetchall()
        ]
        if not stale_ids:
            return
        id_placeholders = ",".join("?" for _ in stale_ids)
        conn.execute(
            f"DELETE FROM order_lines WHERE marketplace='hepsiburada' AND shipment_package_id IN ({id_placeholders})",
            stale_ids,
        )
        conn.execute(
            f"DELETE FROM orders WHERE marketplace='hepsiburada' AND shipment_package_id IN ({id_placeholders})",
            stale_ids,
        )


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


# --- Toptancı borcu ---

def list_suppliers():
    """Her tedarikçi için ad + güncel bakiye (satış borcu - ödeme + düzeltme)
    + son ödeme tarihi (sadece tip='odeme' bakar, 'duzeltme' hariç -- böylece
    manuel bakiye düzeltmeleri "ödeme yaptım" gibi görünmez)."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT t.id, t.ad,
                   COALESCE(SUM(CASE WHEN h.tip='satis' THEN h.tutar ELSE 0 END),0) AS toplam_satis,
                   COALESCE(SUM(CASE WHEN h.tip='odeme' THEN h.tutar ELSE 0 END),0) AS toplam_odeme,
                   COALESCE(SUM(CASE WHEN h.tip='duzeltme' THEN h.tutar ELSE 0 END),0) AS toplam_duzeltme,
                   MAX(CASE WHEN h.tip='odeme' THEN h.tarih ELSE NULL END) AS son_odeme_tarihi
            FROM tedarikciler t
            LEFT JOIN tedarikci_borc_hareketleri h ON h.tedarikci_id = t.id
            GROUP BY t.id, t.ad
            ORDER BY t.ad
        """).fetchall()
    return [
        {
            "id": r["id"],
            "ad": r["ad"],
            "bakiye": round(r["toplam_satis"] - r["toplam_odeme"] + r["toplam_duzeltme"], 2),
            "son_odeme_tarihi": r["son_odeme_tarihi"],
        }
        for r in rows
    ]


def create_supplier(ad):
    with get_connection() as conn:
        cur = conn.execute("INSERT INTO tedarikciler (ad) VALUES (?)", (ad,))
        return cur.lastrowid


def delete_supplier(tedarikci_id):
    with get_connection() as conn:
        conn.execute("UPDATE product_costs SET tedarikci_id = NULL WHERE tedarikci_id = ?", (tedarikci_id,))
        conn.execute("DELETE FROM tedarikci_borc_hareketleri WHERE tedarikci_id = ?", (tedarikci_id,))
        conn.execute("DELETE FROM tedarikciler WHERE id = ?", (tedarikci_id,))


def assign_supplier_to_sku(sku, tedarikci_id):
    """tedarikci_id=None verilirse SKU'nun tedarikçi ataması kaldırılır."""
    with get_connection() as conn:
        conn.execute("UPDATE product_costs SET tedarikci_id = ? WHERE sku = ?", (tedarikci_id, sku))


def add_supplier_payment(tedarikci_id, tutar, aciklama=""):
    """'Ödeme Ekle' butonu: gerçek bir ödeme kaydı. Bakiyeyi düşürür VE
    son_odeme_tarihi'ni günceller."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO tedarikci_borc_hareketleri (tedarikci_id, tip, tutar, aciklama)
            VALUES (?, 'odeme', ?, ?)
        """, (tedarikci_id, tutar, aciklama))


def add_supplier_adjustment(tedarikci_id, tutar, aciklama=""):
    """'Borç Ekle' / manuel bakiye düzeltme butonu: tutar pozitifse bakiyeyi
    artırır (elle borç ekleme), negatifse azaltır (düzeltme/sıfırlama).
    Gerçek bir ödeme SAYILMAZ -- son_odeme_tarihi'ni etkilemez."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO tedarikci_borc_hareketleri (tedarikci_id, tip, tutar, aciklama)
            VALUES (?, 'duzeltme', ?, ?)
        """, (tedarikci_id, tutar, aciklama))


def reset_supplier_debt(tedarikci_id, aciklama="Bakiye sifirlama"):
    """Tedarikçinin o anki bakiyesini dengeleyici bir 'duzeltme' hareketiyle
    sıfırlar. Geçmiş hareketler silinmez, sadece bakiye kapatılır."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(CASE
                WHEN tip='satis' THEN tutar
                WHEN tip='odeme' THEN -tutar
                WHEN tip='duzeltme' THEN tutar
                ELSE 0 END), 0) AS bakiye
            FROM tedarikci_borc_hareketleri WHERE tedarikci_id = ?
        """, (tedarikci_id,)).fetchone()
        bakiye = round(row["bakiye"], 2)
        if bakiye != 0:
            conn.execute("""
                INSERT INTO tedarikci_borc_hareketleri (tedarikci_id, tip, tutar, aciklama)
                VALUES (?, 'duzeltme', ?, ?)
            """, (tedarikci_id, -bakiye, aciklama))
        return -bakiye


def list_supplier_ledger(tedarikci_id, limit=200):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, tip, tutar, aciklama, tarih FROM tedarikci_borc_hareketleri
            WHERE tedarikci_id = ? ORDER BY tarih DESC, id DESC LIMIT ?
        """, (tedarikci_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_total_supplier_debt():
    return round(sum(s["bakiye"] for s in list_suppliers()), 2)


def list_sales_since(since_date_str, exclude_cancelled=True):
    """since_date_str: 'YYYY-MM-DD' (yerel tarih, ör. '2026-08-03'). O tarih
    00:00'dan itibaren (yerel saat) satılan tüm order_lines satırlarını
    döner. exclude_cancelled=True ise iptal/iade siparişler hariç tutulur
    (bkz. _EXCLUDED_ORDER_STATUSES). order_date epoch ms olarak tutulur,
    Trendyol/HB API konvansiyonu.

    Excel/PDF'e aktarım için frontend'de kullanılmak üzere düz bir liste
    döner: marketplace, tarih, sipariş no, durum, sku, ürün adı, adet,
    birim fiyat, tutar."""
    import datetime as _dt
    dt = _dt.datetime.strptime(since_date_str, "%Y-%m-%d")
    since_epoch_ms = int(dt.timestamp() * 1000)

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT o.marketplace, o.order_number, o.order_date, o.status,
                   ol.barcode, ol.merchant_sku, ol.product_name, ol.quantity, ol.line_unit_price
            FROM order_lines ol
            JOIN orders o ON o.marketplace = ol.marketplace
                          AND o.shipment_package_id = ol.shipment_package_id
            WHERE o.order_date >= ?
            ORDER BY o.order_date ASC
        """, (since_epoch_ms,)).fetchall()

    result = []
    for r in rows:
        if exclude_cancelled and r["status"] in _EXCLUDED_ORDER_STATUSES.get(r["marketplace"], set()):
            continue
        adet = r["quantity"] or 0
        birim = r["line_unit_price"] or 0
        result.append({
            "marketplace": r["marketplace"],
            "order_number": r["order_number"],
            "order_date": r["order_date"],
            "status": r["status"],
            "sku": r["merchant_sku"] or r["barcode"],
            "product_name": r["product_name"],
            "quantity": adet,
            "unit_price": birim,
            "total": round(adet * birim, 2),
        })
    return result


def backfill_supplier_debt():
    """Var olan TÜM order_lines satırlarını tarar ve _sync_supplier_debt ile
    aynı mantığı uygular. Bir SKU'ya YENİ tedarikçi atadığında, o SKU'nun
    atamadan ÖNCE satılmış geçmiş satırları için borç otomatik yazılmaz
    (çünkü sadece upsert_order_lines çağrıldığında -- yani yeni bir senkron
    sırasında -- tetiklenir). Bu fonksiyon geçmişe dönük telafi için: tüm
    order_lines'ı yeniden tarar, sadece o an tedarikçisi atanmış SKU'lar için
    eksik olan 'satis' hareketlerini ekler. İdempotent (aynı UNIQUE kısıt),
    istediğin kadar tekrar çağrılabilir."""
    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT marketplace, shipment_package_id, barcode, merchant_sku, quantity
            FROM order_lines
        """).fetchall()]
        _sync_supplier_debt(conn, rows)
    return len(rows)


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


def upsert_product_images(rows):
    """rows: [{marketplace, sku, image_url}] — stock_client.py'den
    (fetch_trendyol_product_images) gelir. image_url None ise de yazılır
    (görseli kaldırılan/kaybolan bir ürünü eski görselle göstermemek için)."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO product_images (marketplace, sku, image_url, updated_at)
            VALUES (:marketplace, :sku, :image_url, datetime('now', 'localtime'))
            ON CONFLICT(marketplace, sku) DO UPDATE SET
                image_url=excluded.image_url,
                updated_at=datetime('now', 'localtime')
        """, rows)


def list_product_images():
    """{sku: image_url} eşlemesi döner (marketplace ayrımı yapmadan — aynı
    SKU birden fazla pazaryerinde satılıyorsa herhangi birinin görseli
    yeterli, kart tasarımında tek görsel gösteriliyor)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT sku, image_url FROM product_images WHERE image_url IS NOT NULL"
        ).fetchall()
    return {r["sku"]: r["image_url"] for r in rows}


def list_hb_review_barcodes():
    """order_lines'daki tüm benzersiz Hepsiburada barcode'larını döner —
    hb_review_sync_tasks.py'nin tarayacağı barkod listesi budur."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT barcode FROM order_lines
            WHERE marketplace='hepsiburada' AND barcode IS NOT NULL AND barcode != ''
        """).fetchall()
    return [r["barcode"] for r in rows]


def get_review_family_map():
    """{barcode: representative_sku} — daha önce keşfedilmiş aile eşlemesi.
    Boş dict dönerse hiçbir barkod için discovery henüz yapılmamış demektir."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT barcode, representative_sku FROM hb_review_family_map
            WHERE marketplace='hepsiburada'
        """).fetchall()
    return {r["barcode"]: r["representative_sku"] for r in rows}


def upsert_review_family_map(rows):
    """rows: [{marketplace, barcode, representative_sku, family_skus}].
    Aynı barcode tekrar geldiğinde representative_sku güncellenir (örn. aile
    keşfi genişlerse) — INSERT yerine UPSERT."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO hb_review_family_map (marketplace, barcode, representative_sku, family_skus, discovered_at)
            VALUES (:marketplace, :barcode, :representative_sku, :family_skus, datetime('now', 'localtime'))
            ON CONFLICT(marketplace, barcode) DO UPDATE SET
                representative_sku=excluded.representative_sku,
                family_skus=excluded.family_skus,
                discovered_at=datetime('now', 'localtime')
        """, rows)


def upsert_review_contents(rows):
    """rows: [{external_review_id, marketplace, product_sku, product_url, star,
    content, created_at, merchant_id, merchant_name, is_purchase_verified, raw_json}].

    review_id (external_review_id) CANONICAL PK'dir — aynı review birden fazla
    sibling SKU sorgusundan veya tekrarlanan bir sync'ten tekrar gelirse
    (Faz 0'da CONFIRMED: sibling'ler aynı review havuzunu döndürüyor), ikinci
    bir satır OLUŞMAZ, mevcut satır güncellenir (idempotent UPSERT)."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany("""
            INSERT INTO review_contents (external_review_id, marketplace, product_sku, product_url,
                                          star, content, created_at, merchant_id, merchant_name,
                                          is_purchase_verified, synced_at, raw_json)
            VALUES (:external_review_id, :marketplace, :product_sku, :product_url,
                    :star, :content, :created_at, :merchant_id, :merchant_name,
                    :is_purchase_verified, datetime('now', 'localtime'), :raw_json)
            ON CONFLICT(marketplace, external_review_id) DO UPDATE SET
                product_sku=excluded.product_sku,
                product_url=excluded.product_url,
                star=excluded.star,
                content=excluded.content,
                created_at=excluded.created_at,
                merchant_id=excluded.merchant_id,
                merchant_name=excluded.merchant_name,
                is_purchase_verified=excluded.is_purchase_verified,
                synced_at=datetime('now', 'localtime'),
                raw_json=excluded.raw_json
        """, rows)


def list_reviews(marketplace="hepsiburada"):
    """Panel/rapor geliştirmesi henüz yapılmıyor (bkz. Faz 0 sonrası onay),
    ama backend'in bu veriyi sorgulayabildiğini doğrulamak ve ileride
    raporlama için kullanılmak üzere basit bir okuma fonksiyonu."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM review_contents WHERE marketplace = ? ORDER BY synced_at DESC
        """, (marketplace,)).fetchall()
    return [dict(r) for r in rows]


def get_known_product_url(sku):
    """review_contents'te bu sku (product_sku) için daha önce kaydedilmiş
    GERÇEK bir HB ürün URL'i varsa döner, yoksa None. DEFAULT_REFERER
    fallback'ine düşmeden önce denenecek ilk kaynak — DEFAULT_REFERER riski
    çözümü, 23.08.2026 (bkz. hb_review_sync_tasks._resolve_referer).
    Yeni bir API/keşif YAPMAZ, sadece bu sync sisteminin kendi topladığı
    veriye bakar."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT product_url FROM review_contents
            WHERE marketplace='hepsiburada' AND product_sku=? AND product_url IS NOT NULL
            ORDER BY synced_at DESC LIMIT 1
        """, (sku,)).fetchone()
    return row["product_url"] if row else None


def get_any_known_hb_product_url():
    """review_contents'te (herhangi bir sku için) kayıtlı GERÇEK bir HB
    ürün URL'i varsa döner. Faz 0'da farklı sibling sorgularında AYNI
    Referer'ın başarıyla çalıştığı gözlemlendiği için tam sku eşleşmesi
    şart değil — herhangi bir gerçek HB ürün sayfası, generic ana sayfadan
    daha güvenilir bir Referer adayıdır."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT product_url FROM review_contents
            WHERE marketplace='hepsiburada' AND product_url IS NOT NULL
            ORDER BY synced_at DESC LIMIT 1
        """).fetchone()
    return row["product_url"] if row else None


def get_existing_review_ids(external_review_ids, marketplace="hepsiburada"):
    """Verilen id listesinden HANGİLERİ review_contents'te ZATEN mevcut,
    onları set olarak döner. upsert'ten ÖNCE çağrılmalı -- production
    logging'de "inserted" (yeni) vs "updated" (zaten vardı) review
    sayısını ayırt etmek için kullanılır (bkz. hb_review_sync_tasks.py
    Aşama 7)."""
    external_review_ids = [i for i in external_review_ids if i]
    if not external_review_ids:
        return set()
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in external_review_ids)
        rows = conn.execute(
            f"""SELECT external_review_id FROM review_contents
                WHERE marketplace=? AND external_review_id IN ({placeholders})""",
            [marketplace, *external_review_ids],
        ).fetchall()
    return {r["external_review_id"] for r in rows}


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