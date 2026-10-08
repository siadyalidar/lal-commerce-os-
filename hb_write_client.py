import re

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHORT_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")

ACTIONS = ("pack", "change_cargo_line", "change_cargo_package", "unpack")


def _clean_id(value, label):
    text = "" if value is None else str(value).strip()
    if not _ID_RE.match(text):
        raise ValueError("Geçersiz " + label)
    return text


def _clean_short(value):
    text = "" if value is None else str(value).strip()
    if not _SHORT_RE.match(text):
        raise ValueError("Kargo firması seçilmedi veya geçersiz")
    return text


def options_path(merchant_id, line_id=None, package_number=None):
    m = _clean_id(merchant_id, "merchantId")
    if line_id and not package_number:
        return "/delivery/changeablecargocompanies/merchantid/%s/orderlineid/%s" % (
            m, _clean_id(line_id, "kalem no"))
    if package_number and not line_id:
        return "/packages/merchantid/%s/packagenumber/%s/changablecargocompanies" % (
            m, _clean_id(package_number, "paket no"))
    raise ValueError("Kalem no veya paket no verilmeli")


def normalize_options(raw):
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        short = item.get("ShortName") or item.get("shortName")
        if not short or item.get("IsActive") is False:
            continue
        name = item.get("Name") or item.get("name") or short
        out.append({"short": str(short), "name": str(name)})
    return out


def build_requests(action, merchant_id, p):
    if action not in ACTIONS:
        raise ValueError("Geçersiz işlem")
    m = _clean_id(merchant_id, "merchantId")

    if action == "pack":
        lines = p.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValueError("Kalem listesi boş")
        reqs = []
        for ln in lines:
            if not isinstance(ln, dict):
                raise ValueError("Geçersiz kalem")
            qty = ln.get("quantity")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
                raise ValueError("Geçersiz adet")
            reqs.append({"id": _clean_id(ln.get("id"), "kalem no"), "quantity": qty})
        return [{
            "method": "POST",
            "path": "/packages/merchantid/%s" % m,
            "body": {"lineItemRequests": reqs},
            "target_id": ",".join(r["id"] for r in reqs),
        }]

    if action == "change_cargo_line":
        ids = p.get("line_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("Kalem listesi boş")
        short = _clean_short(p.get("cargo_short"))
        out = []
        for raw_id in ids:
            line_id = _clean_id(raw_id, "kalem no")
            out.append({
                "method": "PUT",
                "path": "/lineitems/merchantid/%s/orderlineid/%s/cargocompany" % (m, line_id),
                "body": {"CargoCompanyShortName": short},
                "target_id": line_id,
            })
        return out

    package_number = _clean_id(p.get("package_number"), "paket no")

    if action == "change_cargo_package":
        short = _clean_short(p.get("cargo_short"))
        return [{
            "method": "PUT",
            "path": "/packages/merchantid/%s/packagenumber/%s/changecargocompany" % (m, package_number),
            "body": {"CargoCompanyShortName": short},
            "target_id": package_number,
        }]

    return [{
        "method": "POST",
        "path": "/packages/merchantid/%s/packagenumber/%s/unpack" % (m, package_number),
        "body": {},
        "target_id": package_number,
    }]


def send(core, req):
    import requests
    url = core.HB_BASE_URL + req["path"]
    headers = {
        "User-Agent": core.HB_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.request(
            req["method"], url, json=req["body"], headers=headers,
            auth=(core.HB_USERNAME, core.HB_PASSWORD), timeout=30,
            allow_redirects=False,
        )
        return resp.status_code, resp.text[:2000]
    except requests.RequestException as exc:
        return 0, str(exc)[:300]


def label_path(merchant_id, package_number):
    m = _clean_id(merchant_id, "merchant_id")
    pn = _clean_id(package_number, "package_number")
    return "/packages/merchantid/%s/packagenumber/%s/labels" % (m, pn)
