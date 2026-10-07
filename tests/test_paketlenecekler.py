import blueprints.paketlenecekler_routes as pk


def _iso(value):
    return 1000 if value else None


def test_hb_unpacked_groups_and_skips_packaged():
    items = [
        {"orderNumber": "1", "status": "Open", "packageNumber": None, "name": "A",
         "merchantSKU": "S1", "quantity": 1, "totalPrice": {"amount": 100.5}, "orderDate": "x"},
        {"orderNumber": "1", "status": "Open", "packageNumber": "", "name": "B",
         "merchantSKU": "S2", "quantity": 2, "totalPrice": {"amount": 50}, "orderDate": "x"},
        {"orderNumber": "2", "status": "Open", "packageNumber": "P9", "name": "C"},
        {"orderNumber": "3", "status": "Cancelled", "packageNumber": None},
    ]
    rows = pk.hb_unpacked(items, _iso)
    assert len(rows) == 1
    assert rows[0]["order_number"] == "1"
    assert rows[0]["total"] == 150.5
    assert len(rows[0]["lines"]) == 2


def test_hb_packed_only_open():
    pkgs = [
        {"status": "Open", "packageNumber": "P1", "cargoCompany": "hepsiJET",
         "totalPrice": {"amount": 10}, "items": [{"orderNumber": "7", "productName": "X", "quantity": 1}]},
        {"status": "Shipped", "packageNumber": "P2", "items": []},
    ]
    rows = pk.hb_packed(pkgs, _iso)
    assert len(rows) == 1
    assert rows[0]["order_number"] == "7"
    assert rows[0]["package_id"] == "P1"


def test_trendyol_created_rows():
    pkgs = [
        {"id": 11, "orderNumber": 99, "status": "Created", "orderDate": 5,
         "agreedDeliveryDate": 9, "cargoProviderName": "Trendyol Express",
         "customerFirstName": "A", "customerLastName": "B", "packageGrandTotalPrice": 12.5,
         "lines": [{"productName": "X", "merchantSku": "S", "quantity": 2}]},
        {"id": 12, "status": "Shipped"},
    ]
    rows = pk.ty_created(pkgs, lambda v: v)
    assert len(rows) == 1
    assert rows[0]["due_ms"] == 9
    assert rows[0]["customer"] == "A B"
    assert rows[0]["lines"][0]["qty"] == 2
