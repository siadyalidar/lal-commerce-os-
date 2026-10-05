"""LAL bildirim merkezi.

Uc tur bildirim uretir: yeni siparis, yeni yorum ve net karin kesinlesmesi
(kargo faturasi gelip tahmin yerine gercek tutar kullanilan siparisler).
Sync kodundan bagimsizdir: veritabanini tarar, daha once gorulmeyenleri
bildirime cevirir. Ilk calistirmada mevcut her seyi sessizce gorulmus isaretler.
"""

import json

import database

MAX_INDIVIDUAL = 5
MAX_DETAIL_ITEMS = 200
MP_LABELS = {"trendyol": "Trendyol", "hepsiburada": "Hepsiburada"}

_ORDERS_SQL = (
    "SELECT marketplace, shipment_package_id, order_number, net_amount FROM orders"
)
_REVIEWS_SQL = (
    "SELECT marketplace, external_review_id, product_sku, star, content "
    "FROM review_contents"
)
_FINAL_SQL = """
SELECT o.marketplace, o.shipment_package_id, o.order_number
FROM orders o
WHERE EXISTS (
    SELECT 1 FROM cargo_costs c
    WHERE c.marketplace = o.marketplace
      AND (c.shipment_package_id = o.shipment_package_id
           OR c.order_number = o.order_number)
)
AND EXISTS (
    SELECT 1 FROM order_lines l
    WHERE l.shipment_package_id = o.shipment_package_id
      AND l.marketplace = o.marketplace
)
AND NOT EXISTS (
    SELECT 1 FROM order_lines l
    LEFT JOIN product_costs p
      ON p.sku = COALESCE(NULLIF(l.merchant_sku, ''), l.barcode)
    WHERE l.shipment_package_id = o.shipment_package_id
      AND l.marketplace = o.marketplace
      AND (p.sku IS NULL OR p.cost_incl_vat IS NULL)
)
"""


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            detail_json TEXT,
            marketplace TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            read_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_seen (
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            PRIMARY KEY (kind, key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_state (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    """)


def _try(value):
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    s = "{:,.2f}".format(v)
    return "₺" + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _mp(marketplace):
    return MP_LABELS.get(marketplace, marketplace or "")


def _claim(conn, kind, key):
    cur = conn.execute(
        "INSERT OR IGNORE INTO notification_seen (kind, key) VALUES (?, ?)",
        (kind, key),
    )
    return cur.rowcount == 1


def _add(conn, kind, title, body, detail, marketplace=None):
    conn.execute(
        "INSERT INTO notifications (kind, title, body, detail_json, marketplace) "
        "VALUES (?, ?, ?, ?, ?)",
        (kind, title, body, json.dumps(detail, ensure_ascii=False), marketplace),
    )


def _order_item(r):
    return {
        "marketplace": r["marketplace"],
        "order_number": r["order_number"],
        "net_amount": r["net_amount"],
    }


def _emit_orders(conn, rows):
    if not rows:
        return 0
    if len(rows) <= MAX_INDIVIDUAL:
        for r in rows:
            _add(
                conn, "order", "Yeni sipariş",
                "%s · %s · %s" % (_mp(r["marketplace"]), r["order_number"], _try(r["net_amount"])),
                {"orders": [_order_item(r)]}, r["marketplace"],
            )
        return len(rows)
    total = sum((r["net_amount"] or 0) for r in rows)
    _add(
        conn, "order", "%d yeni sipariş" % len(rows), "Toplam %s" % _try(total),
        {"orders": [_order_item(r) for r in rows[:MAX_DETAIL_ITEMS]], "total": len(rows)},
        None,
    )
    return 1


def _review_item(r):
    return {
        "marketplace": r["marketplace"],
        "sku": r["product_sku"],
        "star": r["star"],
        "content": (r["content"] or "")[:300],
    }


def _emit_reviews(conn, rows):
    if not rows:
        return 0
    if len(rows) <= MAX_INDIVIDUAL:
        for r in rows:
            snippet = (r["content"] or "").strip().replace("\n", " ")[:140]
            _add(
                conn, "review", "Yeni yorum · %s★" % (r["star"] if r["star"] is not None else "?"),
                snippet or "(yorum metni yok)",
                {"reviews": [_review_item(r)]}, r["marketplace"],
            )
        return len(rows)
    _add(
        conn, "review", "%d yeni yorum" % len(rows), "Detayda listelendi.",
        {"reviews": [_review_item(r) for r in rows[:MAX_DETAIL_ITEMS]], "total": len(rows)},
        None,
    )
    return 1


def _emit_final(conn, rows):
    if not rows:
        return 0
    items = [
        {"marketplace": r["marketplace"], "order_number": r["order_number"]}
        for r in rows[:MAX_DETAIL_ITEMS]
    ]
    _add(
        conn, "profit_final", "%d siparişin net kârı kesinleşti" % len(rows),
        "Kargo faturası geldi, tahmin yerine gerçek kargo tutarı kullanılıyor.",
        {"orders": items, "total": len(rows)}, None,
    )
    return 1


def detect_and_notify():
    with database.get_connection() as conn:
        ensure_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        first_run = conn.execute(
            "SELECT 1 FROM notification_state WHERE name = 'baseline_done'"
        ).fetchone() is None

        new_orders = [
            r for r in conn.execute(_ORDERS_SQL).fetchall()
            if _claim(conn, "order", "%s:%s" % (r["marketplace"], r["shipment_package_id"]))
        ]
        new_reviews = [
            r for r in conn.execute(_REVIEWS_SQL).fetchall()
            if _claim(conn, "review", "%s:%s" % (r["marketplace"], r["external_review_id"]))
        ]
        new_final = [
            r for r in conn.execute(_FINAL_SQL).fetchall()
            if _claim(conn, "profit_final", "%s:%s" % (r["marketplace"], r["shipment_package_id"]))
        ]

        if first_run:
            conn.execute(
                "INSERT OR IGNORE INTO notification_state (name, value) "
                "VALUES ('baseline_done', '1')"
            )
            return {
                "baseline": True,
                "orders": len(new_orders),
                "reviews": len(new_reviews),
                "profit_final": len(new_final),
            }

        created = (
            _emit_orders(conn, new_orders)
            + _emit_reviews(conn, new_reviews)
            + _emit_final(conn, new_final)
        )
        conn.execute(
            "DELETE FROM notifications "
            "WHERE id <= (SELECT MAX(id) FROM notifications) - 500"
        )
        return {"baseline": False, "created": created}


def _row_to_item(r):
    try:
        detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
    except ValueError:
        detail = {}
    return {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "body": r["body"],
        "detail": detail,
        "marketplace": r["marketplace"],
        "created_at": r["created_at"],
        "read": r["read_at"] is not None,
    }


def list_notifications(after_id=0, limit=30):
    with database.get_connection() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, kind, title, body, detail_json, marketplace, created_at, read_at "
            "FROM notifications WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after_id, limit),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
        ).fetchone()[0]
    return {"items": [_row_to_item(r) for r in rows], "unread": unread}


def mark_read(ids=None):
    with database.get_connection() as conn:
        ensure_schema(conn)
        if ids is None:
            conn.execute(
                "UPDATE notifications SET read_at = datetime('now', 'localtime') "
                "WHERE read_at IS NULL"
            )
        elif ids:
            marks = ",".join("?" for _ in ids)
            conn.execute(
                "UPDATE notifications SET read_at = datetime('now', 'localtime') "
                "WHERE read_at IS NULL AND id IN (%s)" % marks,
                list(ids),
            )
