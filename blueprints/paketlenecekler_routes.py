import time
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("paketlenecekler_routes", __name__)

_CACHE = {"ts": 0.0, "data": None}
_CACHE_TTL = 60
_DUE_KEYS = (
    "dueDate", "deliveryDueDate", "shipmentDueDate",
    "shippingDueDate", "maxShippingDate", "cargoDueDate",
)


def _core():
    import sync_core
    return sync_core


def _hb_iso_to_ms(value):
    if not value:
        return None
    return _core()._hb_iso_to_epoch_ms(value)


def _amount(value):
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_due(objs, iso_to_ms):
    for obj in objs:
        for key in _DUE_KEYS:
            ms = iso_to_ms(obj.get(key))
            if ms:
                return ms
    return None


def hb_unpacked(items, iso_to_ms):
    grouped = {}
    for it in items:
        if it.get("packageNumber"):
            continue
        if (it.get("status") or "").lower() != "open":
            continue
        order_number = it.get("orderNumber")
        if not order_number:
            continue
        grouped.setdefault(order_number, []).append(it)
    rows = []
    for order_number, lines in grouped.items():
        first = lines[0]
        rows.append({
            "marketplace": "hepsiburada",
            "state": "unpacked",
            "order_number": str(order_number),
            "package_id": None,
            "customer": first.get("customerName") or first.get("recipientName") or "",
            "order_date_ms": iso_to_ms(first.get("orderDate")),
            "due_ms": _first_due(lines, iso_to_ms),
            "cargo": first.get("cargoCompany") or first.get("cargoCompanyName") or "",
            "total": sum((_amount(ln.get("totalPrice")) or 0) for ln in lines),
            "lines": [
                {
                    "name": ln.get("name") or ln.get("productName") or "",
                    "sku": ln.get("merchantSKU") or ln.get("merchantSku") or "",
                    "qty": ln.get("quantity") or 1,
                }
                for ln in lines
            ],
        })
    return rows


def hb_packed(pkgs, iso_to_ms):
    rows = []
    for p in pkgs:
        if (p.get("status") or "").lower() != "open":
            continue
        items = p.get("items") or []
        first = items[0] if items else {}
        rows.append({
            "marketplace": "hepsiburada",
            "state": "packed",
            "order_number": str(first.get("orderNumber") or p.get("orderNumber") or ""),
            "package_id": p.get("packageNumber") or p.get("id"),
            "customer": p.get("customerName") or "",
            "order_date_ms": iso_to_ms(p.get("orderDate")),
            "due_ms": _first_due([p] + items, iso_to_ms),
            "cargo": p.get("cargoCompany") or "",
            "total": _amount(p.get("totalPrice")),
            "lines": [
                {
                    "name": it.get("productName") or it.get("name") or "",
                    "sku": it.get("merchantSku") or it.get("merchantSKU") or "",
                    "qty": it.get("quantity") or 1,
                }
                for it in items
            ],
        })
    return rows


def ty_created(pkgs, fix_ms):
    rows = []
    for p in pkgs:
        status = p.get("status") or p.get("shipmentPackageStatus") or ""
        if status and status.lower() != "created":
            continue
        customer = " ".join(
            x for x in (p.get("customerFirstName"), p.get("customerLastName")) if x
        )
        rows.append({
            "marketplace": "trendyol",
            "state": "created",
            "order_number": str(p.get("orderNumber") or ""),
            "package_id": p.get("id"),
            "customer": customer,
            "order_date_ms": fix_ms(p.get("orderDate")),
            "due_ms": fix_ms(p.get("agreedDeliveryDate")),
            "cargo": p.get("cargoProviderName") or "",
            "total": _amount(p.get("packageGrandTotalPrice") or p.get("packageTotalPrice")),
            "lines": [
                {
                    "name": ln.get("productName") or "",
                    "sku": ln.get("merchantSku") or ln.get("barcode") or "",
                    "qty": ln.get("quantity") or 1,
                }
                for ln in (p.get("lines") or [])
            ],
        })
    return rows


def _ty_fixer(core):
    fn = getattr(core, "normalize_trendyol_epoch_ms", None)

    def fix(value):
        if value in (None, ""):
            return None
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        if fn:
            try:
                return fn(value)
            except Exception:
                return value
        return value

    return fix


def _hb_fetch_orders(core):
    items = []
    offset = 0
    limit = 100
    for _ in range(30):
        data = core.hepsiburada_get(core.HB_ORDERS_PATH, {"offset": offset, "limit": limit})
        page = (data or {}).get("items") or []
        total = (data or {}).get("totalCount", 0)
        items.extend(page)
        offset += limit
        if offset >= total or not page:
            break
    return items


def _sort_key(r):
    due = r.get("due_ms")
    return (due is None, due or 0, r.get("order_date_ms") or 0)


def _collect(debug=False):
    core = _core()
    now = datetime.now()
    rows = []
    errors = {}
    raw = {}

    try:
        start_ms = int((now - timedelta(days=13)).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
        pkgs = core.fetch_all_orders(start_ms, end_ms, status="Created")
        if debug and pkgs:
            raw["trendyol_created"] = pkgs[0]
        rows += ty_created(pkgs, _ty_fixer(core))
    except Exception as exc:
        errors["trendyol"] = str(exc)[:300]

    try:
        cred_error = core._check_hb_credentials()
        if cred_error:
            raise RuntimeError(cred_error)
        items = _hb_fetch_orders(core)
        unpacked = hb_unpacked(items, _hb_iso_to_ms)
        if debug:
            open_items = [i for i in items if not i.get("packageNumber")
                          and (i.get("status") or "").lower() == "open"]
            if open_items:
                raw["hb_order_item"] = open_items[0]
        rows += unpacked
        pkgs = core.fetch_all_hb_packages(now - timedelta(days=14), now)
        packed = hb_packed(pkgs, _hb_iso_to_ms)
        if debug and pkgs:
            raw["hb_package"] = pkgs[0]
        rows += packed
    except Exception as exc:
        errors["hepsiburada"] = str(exc)[:300]

    rows.sort(key=_sort_key)
    out = {"rows": rows, "errors": errors, "generated_ms": int(time.time() * 1000)}
    if debug:
        out["raw_samples"] = raw
    return out


@bp.route("/paketlenecekler")
def paketlenecekler_page():
    return render_template("pages/paketlenecekler.html", active_page="paketlenecekler")


@bp.route("/api/paketlenecekler")
def api_paketlenecekler():
    debug = request.args.get("debug") == "1"
    force = request.args.get("refresh") == "1"
    now = time.time()
    if not debug and not force and _CACHE["data"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return jsonify(_CACHE["data"])
    data = _collect(debug)
    if not debug:
        _CACHE["ts"] = now
        _CACHE["data"] = data
    return jsonify(data)
