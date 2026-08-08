"""
blueprints/order_routes.py
----------------------------
Sipariş listeleme, günlük satış özeti, iade ve ürün performansı route'ları.
Kod app.py'den BİREBİR taşındı (davranış/URL değişmedi); iş mantığı
(kredensiyel kontrolü, senkron aralığı çözümleme, Trendyol iade çekme)
sync_core.py'de kalıyor.

cost_routes.py / stock_routes.py / payout_routes.py ile aynı desen.
app.py'de kayıt: app.register_blueprint(order_routes.bp)
"""

from collections import defaultdict
from datetime import datetime, timedelta

import requests
from flask import Blueprint, jsonify, request

from database import get_connection
from finance_engine import best_sellers as compute_best_sellers
from sync_core import DATA_START_DATE, _check_credentials, _resolve_sync_range, get_daily_returns

bp = Blueprint("order_routes", __name__)


@bp.route("/api/daily-sales")
def daily_sales():
    """DB-tabanlı: orders/order_lines tablolarından, marketplace filtresi olmadan
    (Trendyol + Hepsiburada birlikte) günlük satış özeti üretir. Önceden bu endpoint
    canlı Trendyol API'sini çağırıyordu; bu yüzden DB'ye yazılan Hepsiburada verisi
    hiç görünmüyordu."""
    start_dt, end_dt = _resolve_sync_range(request.args)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    with get_connection() as conn:
        order_rows = conn.execute(
            """SELECT * FROM orders WHERE order_date BETWEEN ? AND ?
               ORDER BY order_date DESC""",
            (start_ms, end_ms),
        ).fetchall()

        pairs = [(r["marketplace"], r["shipment_package_id"]) for r in order_rows]
        item_counts = defaultdict(int)
        commission_by_key = defaultdict(float)
        if pairs:
            placeholders = ",".join("(?,?)" for _ in pairs)
            params = [v for pair in pairs for v in pair]
            qty_rows = conn.execute(
                f"""SELECT shipment_package_id, marketplace,
                           SUM(quantity) AS qty,
                           SUM(COALESCE(line_unit_price, 0) * COALESCE(quantity, 0)
                               * COALESCE(commission_rate, 0) / 100.0) AS est_commission
                    FROM order_lines
                    WHERE (marketplace, shipment_package_id) IN ({placeholders})
                    GROUP BY marketplace, shipment_package_id""",
                params,
            ).fetchall()
            for r in qty_rows:
                item_counts[(r["marketplace"], r["shipment_package_id"])] = r["qty"] or 0
                commission_by_key[(r["marketplace"], r["shipment_package_id"])] = r["est_commission"] or 0

    daily_map = defaultdict(lambda: {
        "order_count": 0, "gross_amount": 0.0, "discount_amount": 0.0,
        "net_amount": 0.0, "commission_amount": 0.0, "item_count": 0, "cancelled_count": 0,
    })
    CANCELLED_STATUSES = {"Cancelled", "İptal Edildi", "Returned", "UnDelivered", "UnPacked"}

    for r in order_rows:
        day_key = datetime.fromtimestamp(r["order_date"] / 1000).strftime("%Y-%m-%d")
        d = daily_map[day_key]
        d["order_count"] += 1
        d["gross_amount"] += r["gross_amount"] or 0
        d["discount_amount"] += r["discount_amount"] or 0
        d["net_amount"] += r["net_amount"] or 0
        d["item_count"] += item_counts.get((r["marketplace"], r["shipment_package_id"]), 0)
        d["commission_amount"] += commission_by_key.get((r["marketplace"], r["shipment_package_id"]), 0)
        if r["status"] in CANCELLED_STATUSES:
            d["cancelled_count"] += 1

    daily = [{"date": k, **v} for k, v in sorted(daily_map.items())]
    for d in daily:
        d["gross_amount"] = round(d["gross_amount"], 2)
        d["discount_amount"] = round(d["discount_amount"], 2)
        d["net_amount"] = round(d["net_amount"], 2)
        d["commission_amount"] = round(d["commission_amount"], 2)

    totals = {
        "order_count": sum(d["order_count"] for d in daily),
        "gross_amount": round(sum(d["gross_amount"] for d in daily), 2),
        "discount_amount": round(sum(d["discount_amount"] for d in daily), 2),
        "net_amount": round(sum(d["net_amount"] for d in daily), 2),
        "commission_amount": round(sum(d["commission_amount"] for d in daily), 2),
        "item_count": sum(d["item_count"] for d in daily),
        "cancelled_count": sum(d["cancelled_count"] for d in daily),
    }

    orders_summary = [
        {
            "orderNumber": r["order_number"],
            "shipmentPackageId": r["shipment_package_id"],
            "marketplace": r["marketplace"],
            "orderDate": r["order_date"],
            "status": r["status"],
            "customer": r["customer"],
            "netAmount": r["net_amount"],
            "cargoProvider": r["cargo_provider"],
        }
        for r in order_rows
    ]

    # "daily" ve "totals" HER ZAMAN tam aralık üzerinden hesaplanır (yukarıda),
    # sadece ham sipariş listesi sayfalanır — büyük aralıklarda payload'ı makul
    # tutmak için, ama sessizce veri kaybettirmeden (eskiden sabit [:2000] kırpma
    # vardı, toplamlar doğruydu ama liste sekmesi büyük aralıklarda eksik
    # görünüyordu).
    page = max(1, request.args.get("page", default=1, type=int) or 1)
    page_size = max(1, min(request.args.get("page_size", default=500, type=int) or 500, 2000))
    total_orders = len(orders_summary)
    offset = (page - 1) * page_size

    return jsonify({
        "daily": daily,
        "totals": totals,
        "orders": orders_summary[offset:offset + page_size],
        "orders_total": total_orders,
        "page": page,
        "page_size": page_size,
    })

@bp.route("/api/daily-returns")
def daily_returns():
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400

    start_dt, end_dt = _resolve_sync_range(request.args)

    try:
        daily = get_daily_returns(start_dt=start_dt, end_dt=end_dt)
    except requests.HTTPError as e:
        return jsonify({"error": f"Trendyol API hatası (iadeler): {e}"}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"Bağlantı hatası: {e}"}), 502

    return jsonify({"daily": daily})

@bp.route("/api/orders")
def api_orders():
    args = request.args
    end_dt = datetime.now()
    if args.get("full_history", "").lower() == "true":
        start_dt = DATA_START_DATE
    elif args.get("start_date"):
        try:
            start_dt = datetime.strptime(args["start_date"], "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "start_date formatı YYYY-MM-DD olmalı."}), 400
        if args.get("end_date"):
            try:
                end_dt = datetime.strptime(args["end_date"], "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                return jsonify({"error": "end_date formatı YYYY-MM-DD olmalı."}), 400
    else:
        days = args.get("days", default=30, type=int)
        days = max(1, min(days or 30, 3650))
        start_dt = end_dt - timedelta(days=days)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    status = (args.get("status") or "").strip()
    q = (args.get("q") or "").strip()
    page = max(1, args.get("page", default=1, type=int) or 1)
    page_size = max(1, min(args.get("page_size", default=50, type=int) or 50, 200))
    offset = (page - 1) * page_size

    marketplace = (args.get("marketplace") or "").strip().lower()

    where = ["order_date BETWEEN ? AND ?"]
    params = [start_ms, end_ms]
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        where.append("(order_number LIKE ? OR customer LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if marketplace in ("trendyol", "hepsiburada"):
        where.append("marketplace = ?")
        params.append(marketplace)
    where_sql = " AND ".join(where)

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM orders WHERE {where_sql}", params
        ).fetchone()["c"]

        order_rows = conn.execute(
            f"""SELECT * FROM orders WHERE {where_sql}
                ORDER BY order_date DESC LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()

        pairs = [(r["marketplace"], r["shipment_package_id"]) for r in order_rows]
        lines_by_spid = defaultdict(list)
        if pairs:
            placeholders = ",".join(["(?,?)"] * len(pairs))
            flat_params = [v for pair in pairs for v in pair]
            line_rows = conn.execute(
                f"""SELECT * FROM order_lines WHERE (marketplace, shipment_package_id) IN ({placeholders})""",
                flat_params,
            ).fetchall()
            for ln in line_rows:
                lines_by_spid[(ln["marketplace"], ln["shipment_package_id"])].append({
                    "barcode": ln["barcode"],
                    "merchantSku": ln["merchant_sku"],
                    "productName": ln["product_name"],
                    "quantity": ln["quantity"],
                    "lineUnitPrice": ln["line_unit_price"],
                    "commissionRate": ln["commission_rate"],
                })

    orders = []
    for r in order_rows:
        orders.append({
            "shipmentPackageId": r["shipment_package_id"],
            "orderNumber": r["order_number"],
            "orderDate": r["order_date"],
            "status": r["status"],
            "customer": r["customer"],
            "cargoProvider": r["cargo_provider"],
            "grossAmount": r["gross_amount"],
            "discountAmount": r["discount_amount"],
            "netAmount": r["net_amount"],
            "marketplace": r["marketplace"],
            "lines": lines_by_spid.get((r["marketplace"], r["shipment_package_id"]), []),
        })

    return jsonify({
        "orders": orders,
        "total": total,
        "page": page,
        "page_size": page_size,
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": (end_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
    })

@bp.route("/api/product-performance")
def api_product_performance():
    start_dt, end_dt = _resolve_sync_range(request.args)
    sort_by = request.args.get("sort_by", "profit")
    order = request.args.get("order", "desc")
    limit = request.args.get("limit", default=500, type=int)
    limit = max(1, min(limit or 500, 2000))
    # DÜZELTME (28.07.2026): best-sellers ile aynı hata burada da vardı —
    # frontend marketplace gönderiyordu ama route hiç okumuyordu.
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    mp_filter = None if marketplace not in ("trendyol", "hepsiburada") else marketplace

    try:
        items = compute_best_sellers(start_dt=start_dt, end_dt=end_dt, limit=100000, marketplace_filter=mp_filter)
    except Exception as e:
        return jsonify({"error": f"Hesaplama hatası: {e}"}), 500

    with get_connection() as conn:
        cost_skus = {r["sku"] for r in conn.execute("SELECT sku FROM product_costs").fetchall()}
        comm_where = "merchant_sku IS NOT NULL"
        comm_params = []
        if mp_filter:
            comm_where += " AND marketplace = ?"
            comm_params.append(mp_filter)
        comm_rows = conn.execute(
            f"""SELECT merchant_sku AS sku, AVG(commission_rate) AS avg_commission,
                      AVG(line_unit_price) AS avg_price
               FROM order_lines WHERE {comm_where} GROUP BY merchant_sku""",
            comm_params,
        ).fetchall()
    comm_map = {r["sku"]: r for r in comm_rows}

    for it in items:
        it["hasCost"] = it["sku"] in cost_skus
        extra = comm_map.get(it["sku"])
        it["avgCommissionRate"] = round(extra["avg_commission"], 2) if extra and extra["avg_commission"] is not None else None
        it["avgUnitPrice"] = round(extra["avg_price"], 2) if extra and extra["avg_price"] is not None else None

    reverse = order != "asc"
    NEG_INF, POS_INF = -1e18, 1e18
    key_fns = {
        "margin": lambda x: x["margin"] if x["margin"] is not None else (NEG_INF if reverse else POS_INF),
        "profit": lambda x: x["profit"] if x["profit"] is not None else (NEG_INF if reverse else POS_INF),
        "revenue": lambda x: x["revenue"] or 0,
        "quantity": lambda x: x["quantity"] or 0,
    }
    items.sort(key=key_fns.get(sort_by, key_fns["profit"]), reverse=reverse)

    return jsonify({"items": items[:limit], "total": len(items)})
