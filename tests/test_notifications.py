import pytest

import database
import notifications


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "notif_test.db"))
    database.init_db()
    return database


def _add_order(spid, number, marketplace="trendyol", sku="SKU1"):
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO orders (shipment_package_id, marketplace, order_number, "
            "order_date, status, net_amount) VALUES (?, ?, ?, ?, ?, ?)",
            (spid, marketplace, number, 1759600000000, "Delivered", 100.0),
        )
        conn.execute(
            "INSERT INTO order_lines (shipment_package_id, marketplace, "
            "merchant_sku, quantity) VALUES (?, ?, ?, ?)",
            (spid, marketplace, sku, 1),
        )


def _add_cargo(spid, number, marketplace="trendyol"):
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO cargo_costs (id, marketplace, shipment_package_id, "
            "order_number, amount) VALUES (?, ?, ?, ?, ?)",
            ("c%s" % spid, marketplace, spid, number, 40.0),
        )


def _add_cost(sku="SKU1"):
    with database.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO product_costs (sku, product_name, cost_incl_vat) "
            "VALUES (?, ?, ?)",
            (sku, "Test", 50.0),
        )


def _kinds():
    return [i["kind"] for i in notifications.list_notifications(limit=100)["items"]]


def test_first_run_is_silent_baseline(db):
    _add_order(1, "A1")
    result = notifications.detect_and_notify()
    assert result["baseline"] is True
    assert _kinds() == []


def test_new_order_notifies_once(db):
    notifications.detect_and_notify()
    _add_order(2, "A2")
    notifications.detect_and_notify()
    notifications.detect_and_notify()
    assert _kinds() == ["order"]


def test_profit_final_needs_cargo_and_cost(db):
    notifications.detect_and_notify()
    _add_order(3, "A3")
    _add_cargo(3, "A3")
    notifications.detect_and_notify()
    assert _kinds() == ["order"]
    _add_cost("SKU1")
    notifications.detect_and_notify()
    notifications.detect_and_notify()
    assert sorted(_kinds()) == ["order", "profit_final"]


def test_mark_read(db):
    notifications.detect_and_notify()
    _add_order(4, "A4")
    notifications.detect_and_notify()
    assert notifications.list_notifications()["unread"] == 1
    notifications.mark_read(None)
    assert notifications.list_notifications()["unread"] == 0
