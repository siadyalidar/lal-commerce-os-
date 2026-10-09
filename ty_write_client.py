import re

_CARGO_RE = re.compile(r"^[A-Z]{2,24}$")

ACTIONS = ("picking", "change_cargo")


def _pos_int(value, label):
    if isinstance(value, bool):
        raise ValueError("Geçersiz %s" % label)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or value < 1:
        raise ValueError("Geçersiz %s" % label)
    return value


def build_requests(action, seller_id, p):
    if action not in ACTIONS:
        raise ValueError("Geçersiz işlem")
    seller = _pos_int(seller_id, "satıcı no")
    package_id = _pos_int(p.get("package_id"), "paket no")
    base = "/integration/order/sellers/%d/shipment-packages/%d" % (seller, package_id)

    if action == "picking":
        lines = p.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValueError("Kalem listesi boş")
        out = []
        for ln in lines:
            if not isinstance(ln, dict):
                raise ValueError("Geçersiz kalem")
            out.append({
                "lineId": _pos_int(ln.get("id"), "kalem no"),
                "quantity": _pos_int(ln.get("quantity"), "adet"),
            })
        return [{
            "method": "PUT",
            "path": base,
            "body": {"lines": out, "params": {}, "status": "Picking"},
            "target_id": str(package_id),
        }]

    code = p.get("cargo_provider")
    if not isinstance(code, str) or not _CARGO_RE.match(code):
        raise ValueError("Geçersiz kargo kodu")
    return [{
        "method": "PUT",
        "path": base + "/cargo-providers",
        "body": {"cargoProvider": code},
        "target_id": str(package_id),
    }]


def send(core, req):
    import requests
    url = core.BASE_URL + req["path"]
    headers = {
        "User-Agent": core.USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.request(
            req["method"], url, json=req["body"], headers=headers,
            auth=(core.API_KEY, core.API_SECRET), timeout=30,
            allow_redirects=False,
        )
        return resp.status_code, resp.text[:2000]
    except requests.RequestException as exc:
        return 0, str(exc)[:300]
