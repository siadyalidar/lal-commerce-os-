"""
test_database_migrations.py
------------------------------
schema_migrations tablosunun doğru şekilde kayıt tuttuğunu ve init_db()'nin
tekrar tekrar (idempotent) çalıştırılabildiğini doğrular.
"""

import database


def test_init_db_records_applied_migrations(db):
    applied = database.get_applied_migrations()
    names = {m["name"] for m in applied}
    assert "2026_07_28_composite_marketplace_keys" in names


def test_init_db_is_idempotent(db):
    """init_db() ikinci kez çağrıldığında hata vermemeli ve migrasyon
    tekrar 'uygulanmamalı' (schema_migrations'ta tek satır kalmalı)."""
    database.init_db()
    database.init_db()
    applied = database.get_applied_migrations()
    names = [m["name"] for m in applied]
    assert names.count("2026_07_28_composite_marketplace_keys") == 1


def test_orders_table_has_composite_primary_key(db):
    with database.get_connection() as conn:
        pk_cols = database._pk_columns(conn, "orders")
    assert set(pk_cols) == {"marketplace", "shipment_package_id"}


def test_orders_table_has_hb_discount_breakdown_columns(db):
    """B3 (Faz 0 audit): totalHBDiscount/totalMerchantDiscount ekonomik
    ayrımı korunabilsin diye orders'a eklenen kolonlar mevcut mu."""
    with database.get_connection() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
    assert "hb_discount_amount" in cols
    assert "merchant_discount_amount" in cols
