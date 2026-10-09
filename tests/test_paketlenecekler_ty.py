import pytest
import ty_write_client as ty


def test_picking_body_and_path():
    r = ty.build_requests("picking", 1166645, {
        "package_id": "123456", "lines": [{"id": 9001, "quantity": 2}]})
    assert r == [{
        "method": "PUT",
        "path": "/integration/order/sellers/1166645/shipment-packages/123456",
        "body": {"lines": [{"lineId": 9001, "quantity": 2}], "params": {}, "status": "Picking"},
        "target_id": "123456",
    }]


def test_change_cargo():
    r = ty.build_requests("change_cargo", 1166645, {"package_id": 5, "cargo_provider": "ARASMP"})
    assert r[0]["path"].endswith("/shipment-packages/5/cargo-providers")
    assert r[0]["body"] == {"cargoProvider": "ARASMP"}


@pytest.mark.parametrize("action,p", [
    ("bogus", {"package_id": 1}),
    ("picking", {"package_id": 1, "lines": []}),
    ("picking", {"package_id": True, "lines": [{"id": 1, "quantity": 1}]}),
    ("picking", {"package_id": 1, "lines": [{"id": 1, "quantity": 0}]}),
    ("picking", {"package_id": 1, "lines": [{"id": "x", "quantity": 1}]}),
    ("picking", {"package_id": "../1", "lines": [{"id": 1, "quantity": 1}]}),
    ("change_cargo", {"package_id": 1, "cargo_provider": "ark/../x"}),
    ("change_cargo", {"package_id": 1}),
])
def test_invalid_inputs(action, p):
    with pytest.raises(ValueError):
        ty.build_requests(action, 1166645, p)
