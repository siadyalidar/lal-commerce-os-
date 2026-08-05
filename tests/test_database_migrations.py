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
