#!/usr/bin/env python3
"""
apply_growth_patch.py
----------------------
"Gelir Kaçakları" ekranını çalıştırmak için gereken database.py, sync_core.py
ve app.py değişikliklerini OTOMATİK uygular.

KULLANIM:
    cd ~/Desktop/lal-commerce-os-guncel
    python3 apply_growth_patch.py

Her dosyanın önce .bak_growth uzantılı bir yedeğini alır. Bir anchor metni
dosyada bulunamazsa (yani dosya beklenenden farklıysa) o dosyayı DEĞİŞTİRMEDEN
durur ve tam olarak ne bulamadığını yazar — böylece yanlışlıkla dosyayı
bozmaz. Script'i tekrar tekrar çalıştırmak güvenlidir (idempotent): bir
değişiklik zaten uygulanmışsa o adımı atlar.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak_growth")
    if not bak.exists():
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  yedek alındı: {bak.name}")


def fail(filename, what):
    print(f"\n❌ DURDURULDU: {filename} içinde beklenen metin bulunamadı: {what}")
    print("   Dosya değiştirilmedi. Bu satırı Claude'a gönder, birlikte düzeltelim.")
    sys.exit(1)


def patch_database_py():
    path = ROOT / "database.py"
    if not path.exists():
        fail("database.py", "dosyanın kendisi (proje kökünde çalıştırdığından emin ol)")
    text = path.read_text(encoding="utf-8")
    changed = False

    # --- 1) Migration fonksiyonunu ekle (init_db'den hemen önce) ---
    if "_migrate_growth_columns" not in text:
        anchor = "def init_db():"
        if text.count(anchor) != 1:
            fail("database.py", f"'{anchor}' tam olarak 1 kez bulunmalıydı, {text.count(anchor)} kez bulundu")
        new_func = '''def _migrate_growth_columns(conn):
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


'''
        text = text.replace(anchor, new_func + anchor, 1)
        changed = True
        print("  + _migrate_growth_columns fonksiyonu eklendi")
    else:
        print("  = _migrate_growth_columns zaten mevcut, atlandı")

    # --- 2) MIGRATIONS listesine kaydı ekle ---
    if "2026_08_09_growth_columns" not in text:
        anchor = '("2026_07_28_composite_marketplace_keys", _migrate_composite_keys),'
        if text.count(anchor) != 1:
            fail("database.py", f"'{anchor}' tam olarak 1 kez bulunmalıydı, {text.count(anchor)} kez bulundu")
        new_line = anchor + '\n    ("2026_08_09_growth_columns", _migrate_growth_columns),'
        text = text.replace(anchor, new_line, 1)
        changed = True
        print("  + MIGRATIONS listesine kayıt eklendi")
    else:
        print("  = MIGRATIONS kaydı zaten mevcut, atlandı")

    # --- 3) upsert_order_lines fonksiyonunu güncelle ---
    old_upsert = '''def upsert_order_lines(rows):
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
        """, rows)'''
    new_upsert = '''def upsert_order_lines(rows):
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
        """, rows)'''
    if "sales_campaign_id=excluded.sales_campaign_id" in text:
        print("  = upsert_order_lines zaten güncellenmiş, atlandı")
    else:
        if text.count(old_upsert) != 1:
            fail("database.py", "upsert_order_lines fonksiyonunun beklenen orijinal metni (satır satır eşleşmiyor)")
        text = text.replace(old_upsert, new_upsert, 1)
        changed = True
        print("  + upsert_order_lines güncellendi")

    if changed:
        backup(path)
        path.write_text(text, encoding="utf-8")
        print("✅ database.py yazıldı")
    else:
        print("✅ database.py zaten güncel, değişiklik yapılmadı")


def patch_sync_core_py():
    path = ROOT / "sync_core.py"
    if not path.exists():
        fail("sync_core.py", "dosyanın kendisi (proje kökünde çalıştırdığından emin ol)")
    text = path.read_text(encoding="utf-8")

    if '"sales_campaign_id": line.get("salesCampaignId")' in text:
        print("✅ sync_core.py zaten güncel, değişiklik yapılmadı")
        return

    old_block = '''        for line in o.get("lines", []):
            line_rows.append({
                "shipment_package_id": spid,
                "marketplace": "trendyol",
                "barcode": line.get("barcode"),
                "merchant_sku": line.get("merchantSku") or line.get("sku"),
                "product_name": line.get("productName"),
                "quantity": line.get("quantity"),
                "line_unit_price": line.get("lineUnitPrice"),
                "commission_rate": line.get("commission"),
            })'''
    new_block = '''        for line in o.get("lines", []):
            line_rows.append({
                "shipment_package_id": spid,
                "marketplace": "trendyol",
                "barcode": line.get("barcode"),
                "merchant_sku": line.get("merchantSku") or line.get("sku"),
                "product_name": line.get("productName"),
                "quantity": line.get("quantity"),
                "line_unit_price": line.get("lineUnitPrice"),
                "commission_rate": line.get("commission"),
                "sales_campaign_id": line.get("salesCampaignId"),
                "seller_discount": line.get("discount"),
                "marketplace_discount": line.get("tyDiscount"),
            })'''
    if text.count(old_block) != 1:
        fail("sync_core.py", "sync_orders_to_db içindeki line_rows.append bloğunun beklenen orijinal metni")
    text = text.replace(old_block, new_block, 1)
    backup(path)
    path.write_text(text, encoding="utf-8")
    print("  + sync_orders_to_db güncellendi (salesCampaignId/discount/tyDiscount alanları)")
    print("✅ sync_core.py yazıldı")


def patch_app_py():
    path = ROOT / "app.py"
    if not path.exists():
        fail("app.py", "dosyanın kendisi (proje kökünde çalıştırdığından emin ol)")
    text = path.read_text(encoding="utf-8")
    changed = False

    # --- import satırını mevcut bir blueprint import'undan türet ---
    if "growth_routes_bp" not in text:
        import_line_match = None
        for line in text.splitlines():
            if "dashboard_routes_bp" in line and "import" in line and "register_blueprint" not in line:
                import_line_match = line
                break
        if not import_line_match:
            fail("app.py", "dashboard_routes_bp içeren bir import satırı bulunamadı (blueprint import pattern'i tespit edilemedi)")
        new_import = import_line_match.replace("dashboard_routes", "growth_routes")
        text = text.replace(import_line_match, import_line_match + "\n" + new_import, 1)
        changed = True
        print(f"  + import satırı eklendi: {new_import.strip()}")
    else:
        print("  = growth_routes_bp import zaten mevcut")

    # --- register_blueprint çağrısını ekle ---
    if "app.register_blueprint(growth_routes_bp)" not in text:
        anchor = "app.register_blueprint(reports_routes_bp)"
        if text.count(anchor) != 1:
            fail("app.py", f"'{anchor}' tam olarak 1 kez bulunmalıydı, {text.count(anchor)} kez bulundu")
        text = text.replace(anchor, anchor + "\napp.register_blueprint(growth_routes_bp)", 1)
        changed = True
        print("  + app.register_blueprint(growth_routes_bp) eklendi")
    else:
        print("  = register_blueprint(growth_routes_bp) zaten mevcut")

    if changed:
        backup(path)
        path.write_text(text, encoding="utf-8")
        print("✅ app.py yazıldı")
    else:
        print("✅ app.py zaten güncel, değişiklik yapılmadı")


def main():
    print("== database.py yamalanıyor ==")
    patch_database_py()
    print("\n== sync_core.py yamalanıyor ==")
    patch_sync_core_py()
    print("\n== app.py yamalanıyor ==")
    patch_app_py()

    print("\n" + "=" * 60)
    print("TAMAMLANDI. Şimdi şunu çalıştır:")
    print("  python3 app.py")
    print("Hata çıkarsa (özellikle ImportError) terminal çıktısının")
    print("tamamını Claude'a yapıştır.")
    print("=" * 60)


if __name__ == "__main__":
    main()
