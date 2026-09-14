"""
test_cargo_reconciliation.py
-----------------------------
14.09.2026 kok neden duzeltmesi icin red-first testler.
Bu dosya yazildigi anda ASAGIDAKI testler BASARISIZ olmalidir (henuz
_cargo_items_to_rows / reconcile_cargo_costs / record_cargo_sync_failure /
cargo_sync_failures tablosu koda eklenmedi). Once kirmizi, sonra yesil.
"""
import json
import sys
import pytest

sys.path.insert(0, '.')

import database
import trendyol_finance as tf


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Her test icin izole, gecici bir SQLite DB. Production trendyol_data.db'ye
    KESINLIKLE dokunulmaz."""
    db_path = tmp_path / "test_cargo.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return database


def _insert_other_financial(conn, invoice_id, description="Kargo Faturasi"):
    conn.execute("""
        INSERT INTO other_financials
            (id, marketplace, transaction_date, transaction_type, raw_transaction_type, description)
        VALUES (?, 'trendyol', 0, 'DeductionInvoices', 'Kargo Fatura', ?)
    """, (invoice_id, description))


def _cargo_rows(db):
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cargo_costs WHERE marketplace='trendyol'"
        ).fetchall()]


# ---------------------------------------------------------------
# 1) delayed invoice: fatura hemen yok, sonra other_financials'a girer
# ---------------------------------------------------------------
def test_delayed_invoice_not_yet_discoverable(db, monkeypatch):
    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: (_ for _ in ()).throw(AssertionError("cagrilmamali")))
    n_invoices, n_items = tf.sync_cargo_costs()
    assert n_invoices == 0
    assert n_items == 0
    assert _cargo_rows(db) == []


def test_invoice_initially_missing_then_appears(db, monkeypatch):
    # ilk tur: other_financials'ta kargo faturasi yok -> hic islenmez
    n_invoices, _ = tf.sync_cargo_costs()
    assert n_invoices == 0

    # sonra fatura "gelir" (reconcile_cargo_costs'un fetch_other_financials
    # genis pencereyle cektigini simule ediyoruz)
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_DELAYED")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "999", "shipmentPackageId": 111, "barcode": "B1",
         "amount": 25.0, "parcelUniqueId": "P1"}
    ])
    n_invoices, n_items = tf.sync_cargo_costs()
    assert n_invoices == 1
    assert n_items == 1
    rows = _cargo_rows(db)
    assert len(rows) == 1
    assert rows[0]["order_number"] == "999"
    assert rows[0]["amount"] == 25.0


# ---------------------------------------------------------------
# 2) ayni siparis icin birden fazla kargo kalemi (RC1 collision fix)
# ---------------------------------------------------------------
def test_multiple_items_same_order_with_parcel_id(db, monkeypatch):
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_MULTI")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "555", "shipmentPackageId": 222, "barcode": "B1",
         "amount": 30.0, "parcelUniqueId": "GIDIS"},
        {"orderNumber": "555", "shipmentPackageId": 222, "barcode": "B1",
         "amount": 12.5, "parcelUniqueId": "IADE"},
    ])
    tf.sync_cargo_costs()
    rows = _cargo_rows(db)
    assert len(rows) == 2, "iki farkli kargo hareketi BIRBIRINI EZMEMELI"
    amounts = sorted(r["amount"] for r in rows)
    assert amounts == [12.5, 30.0]
    assert len(set(r["id"] for r in rows)) == 2


def test_same_barcode_order_number_missing_parcel_id_no_overwrite(db, monkeypatch):
    """RC1'in ta kendisi: parcelUniqueId YOK, ayni barcode/order/spid, farkli
    tutar. Eski kod bunlari AYNI id'ye dusurup birini eziyordu."""
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_COLLIDE")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "777", "shipmentPackageId": 333, "barcode": "B9", "amount": 40.0},
        {"orderNumber": "777", "shipmentPackageId": 333, "barcode": "B9", "amount": 18.0},
    ])
    tf.sync_cargo_costs()
    rows = _cargo_rows(db)
    assert len(rows) == 2, "collision-safe ID olmadan bu 1 satira dusup biri kaybolurdu"
    amounts = sorted(r["amount"] for r in rows)
    assert amounts == [18.0, 40.0]


def test_missing_parcel_unique_id_fallback_still_works(db, monkeypatch):
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_NOPARCEL")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "888", "shipmentPackageId": 444, "barcode": "B2", "amount": 55.0},
    ])
    tf.sync_cargo_costs()
    rows = _cargo_rows(db)
    assert len(rows) == 1
    assert rows[0]["order_number"] == "888"
    assert rows[0]["amount"] == 55.0
    assert rows[0]["raw_json"]  # ham JSON her zaman saklanmali


# ---------------------------------------------------------------
# 3) idempotency: tekrar sync duplicate uretmemeli
# ---------------------------------------------------------------
def test_repeated_sync_is_idempotent(db, monkeypatch):
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_IDEMP")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "111", "shipmentPackageId": 1, "barcode": "B1", "amount": 10.0, "parcelUniqueId": "P1"},
        {"orderNumber": "112", "shipmentPackageId": 2, "barcode": "B2", "amount": 20.0},
    ])
    tf.sync_cargo_costs()
    rows_first = _cargo_rows(db)
    tf.sync_cargo_costs()
    tf.sync_cargo_costs()
    rows_after = _cargo_rows(db)
    assert len(rows_after) == len(rows_first) == 2, "tekrar sync duplicate satir uretmemeli"
    assert sorted(r["id"] for r in rows_first) == sorted(r["id"] for r in rows_after)


def test_multiple_cargo_movements_not_double_counted(db, monkeypatch):
    """Ayni siparis icin 3 ayri kargo hareketi (bolunmus paket) -- toplamda
    hepsi ayri satir olarak durmali, hicbiri kaybolmamali/cift sayilmamali."""
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_MOVES")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "321", "shipmentPackageId": 9, "barcode": "B5", "amount": 10.0, "parcelUniqueId": "A"},
        {"orderNumber": "321", "shipmentPackageId": 9, "barcode": "B5", "amount": 10.0, "parcelUniqueId": "B"},
        {"orderNumber": "321", "shipmentPackageId": 9, "barcode": "B5", "amount": 10.0, "parcelUniqueId": "C"},
    ])
    tf.sync_cargo_costs()
    rows = _cargo_rows(db)
    assert len(rows) == 3
    assert sum(r["amount"] for r in rows) == 30.0


# ---------------------------------------------------------------
# 4) hata durumlari: sessizce yutulmamali
# ---------------------------------------------------------------
def test_invoice_api_error_is_recorded_not_swallowed(db, monkeypatch):
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_ERR")
        _insert_other_financial(conn, "INV_OK")

    def fake_fetch(inv):
        if inv == "INV_ERR":
            raise RuntimeError("Trendyol 500")
        return [{"orderNumber": "1", "shipmentPackageId": 1, "barcode": "B", "amount": 5.0}]

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", fake_fetch)
    n_invoices, n_items = tf.sync_cargo_costs()

    assert n_invoices == 2, "hata veren fatura DIGERLERINI durdurmamali"
    assert n_items == 1, "sadece basarili faturanin kalemleri sayilmali"

    with db.get_connection() as conn:
        failures = [dict(r) for r in conn.execute("SELECT * FROM cargo_sync_failures").fetchall()]
    assert len(failures) == 1
    assert failures[0]["invoice_serial_number"] == "INV_ERR"
    assert "500" in failures[0]["last_error"]


def test_failure_cleared_after_later_success(db, monkeypatch):
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_RETRY")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: (_ for _ in ()).throw(RuntimeError("gecici hata")))
    tf.sync_cargo_costs()
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM cargo_sync_failures").fetchone()["c"] == 1

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "1", "shipmentPackageId": 1, "barcode": "B", "amount": 5.0}
    ])
    tf.sync_cargo_costs()
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM cargo_sync_failures").fetchone()["c"] == 0, \
            "basarili tekrar denemeden sonra hata kaydi temizlenmeli"


def test_partial_invoice_response_missing_fields(db, monkeypatch):
    """API bazi alanlari (orderNumber, barcode) eksik dondurse bile crash
    olmamali, ham JSON her zaman saklanmali."""
    with db.get_connection() as conn:
        _insert_other_financial(conn, "INV_PARTIAL")

    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"amount": 7.5},  # orderNumber, barcode, shipmentPackageId hicbiri yok
    ])
    tf.sync_cargo_costs()
    rows = _cargo_rows(db)
    assert len(rows) == 1
    assert rows[0]["amount"] == 7.5
    assert rows[0]["order_number"] is None
    assert json.loads(rows[0]["raw_json"])["amount"] == 7.5


# ---------------------------------------------------------------
# 5) collision testi (dogrudan fonksiyon seviyesinde)
# ---------------------------------------------------------------
def test_cargo_items_to_rows_collision_direct():
    items = [
        {"orderNumber": "42", "shipmentPackageId": 7, "barcode": "X", "amount": 100.0},
        {"orderNumber": "42", "shipmentPackageId": 7, "barcode": "X", "amount": 200.0},
    ]
    rows = tf._cargo_items_to_rows(items, "INVX")
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == 2, "farkli icerikli iki kalem AYNI id'yi almamali"
    assert sorted(r["amount"] for r in rows) == [100.0, 200.0]


def test_cargo_items_to_rows_identical_content_still_two_rows_but_stable_ids():
    """Ayni faturada TAM AYNI icerikli iki kalem (nadir ama API tekrar edebilir)
    -- occurrence sayaciyla ikisi de KORUNUR (veri kaybi yok), ama tekrar
    sync edildiginde AYNI id'ler uretilip idempotent kalir (duplicate artmaz)."""
    items = [
        {"orderNumber": "1", "shipmentPackageId": 1, "barcode": "B", "amount": 5.0},
        {"orderNumber": "1", "shipmentPackageId": 1, "barcode": "B", "amount": 5.0},
    ]
    rows1 = tf._cargo_items_to_rows(items, "INVY")
    rows2 = tf._cargo_items_to_rows(items, "INVY")
    assert len(rows1) == 2
    assert sorted(r["id"] for r in rows1) == sorted(r["id"] for r in rows2)


# ---------------------------------------------------------------
# 6) reconcile_cargo_costs: genis pencereli backfill
# ---------------------------------------------------------------
def test_reconcile_cargo_costs_widens_discovery_window(db, monkeypatch):
    captured = {}

    def fake_fetch_other_financials(start_dt, end_dt, transaction_types=None, progress_cb=None):
        captured["start_dt"] = start_dt
        captured["end_dt"] = end_dt
        captured["types"] = transaction_types
        with db.get_connection() as conn:
            _insert_other_financial(conn, "INV_RECON")
        return 1, []

    monkeypatch.setattr(tf, "fetch_other_financials", fake_fetch_other_financials)
    monkeypatch.setattr(tf, "fetch_cargo_invoice_items", lambda inv: [
        {"orderNumber": "1", "shipmentPackageId": 1, "barcode": "B", "amount": 9.0}
    ])

    result = tf.reconcile_cargo_costs(lookback_days=200)

    assert captured["types"] == ["DeductionInvoices"]
    delta_days = (captured["end_dt"] - captured["start_dt"]).days
    assert delta_days >= 199
    assert result["cargo_invoice_count"] == 1
    assert result["cargo_item_count"] == 1
    assert _cargo_rows(db)[0]["order_number"] == "1"
