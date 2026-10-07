import pytest

import hb_write_client as hbw

M = "MERCH1"


def test_pack_request():
    reqs = hbw.build_requests("pack", M, {"lines": [{"id": "L1", "quantity": 2}]})
    assert reqs == [{
        "method": "POST",
        "path": "/packages/merchantid/MERCH1",
        "body": {"lineItemRequests": [{"id": "L1", "quantity": 2}]},
        "target_id": "L1",
    }]


def test_cargo_line_one_put_per_line():
    reqs = hbw.build_requests("change_cargo_line", M, {"line_ids": ["A1", "B2"], "cargo_short": "YK"})
    assert [r["method"] for r in reqs] == ["PUT", "PUT"]
    assert reqs[0]["path"] == "/lineitems/merchantid/MERCH1/orderlineid/A1/cargocompany"
    assert reqs[1]["body"] == {"CargoCompanyShortName": "YK"}


def test_cargo_package_and_unpack():
    r1 = hbw.build_requests("change_cargo_package", M, {"package_number": "P9", "cargo_short": "AR"})[0]
    assert r1["path"] == "/packages/merchantid/MERCH1/packagenumber/P9/changecargocompany"
    assert r1["body"] == {"CargoCompanyShortName": "AR"}
    r2 = hbw.build_requests("unpack", M, {"package_number": "P9"})[0]
    assert r2["method"] == "POST"
    assert r2["path"].endswith("/packagenumber/P9/unpack")
    assert r2["body"] == {}


@pytest.mark.parametrize("action,payload", [
    ("pack", {"lines": []}),
    ("pack", {"lines": [{"id": "a/b", "quantity": 1}]}),
    ("pack", {"lines": [{"id": "L1", "quantity": 0}]}),
    ("pack", {"lines": [{"id": "L1", "quantity": True}]}),
    ("change_cargo_line", {"line_ids": ["L1"], "cargo_short": ""}),
    ("change_cargo_line", {"line_ids": [], "cargo_short": "YK"}),
    ("change_cargo_package", {"package_number": "P 1", "cargo_short": "YK"}),
    ("unpack", {"package_number": ""}),
    ("delete_everything", {}),
])
def test_rejects_bad_input(action, payload):
    with pytest.raises(ValueError):
        hbw.build_requests(action, M, payload)


def test_options_path_and_normalize():
    assert hbw.options_path(M, line_id="L1").endswith("/orderlineid/L1")
    assert hbw.options_path(M, package_number="P1").endswith("/packagenumber/P1/changablecargocompanies")
    with pytest.raises(ValueError):
        hbw.options_path(M)
    raw = [
        {"ShortName": "YK", "Name": "Yurtiçi Kargo", "IsActive": True},
        {"ShortName": "XX", "Name": "Kapali", "IsActive": False},
        {"Name": "Kisasiz"},
    ]
    assert hbw.normalize_options(raw) == [{"short": "YK", "name": "Yurtiçi Kargo"}]
