import logging
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

from database import get_connection
from cargo_label_service import get_labels_for_orders

with get_connection() as conn:
    ty_row = conn.execute("""
        SELECT shipment_package_id, order_number, status, cargo_tracking_number
        FROM orders
        WHERE marketplace = 'trendyol'
          AND status IN ('Picking', 'Invoiced', 'Shipped')
          AND cargo_tracking_number IS NOT NULL
        ORDER BY order_date DESC
        LIMIT 1
    """).fetchone()

    hb_row = conn.execute("""
        SELECT shipment_package_id, order_number, status
        FROM orders
        WHERE marketplace = 'hepsiburada'
        ORDER BY order_date DESC
        LIMIT 1
    """).fetchone()

print("======================================================================")
if ty_row:
    print("[TRENDYOL] Secilen siparis:", dict(ty_row))
    ty_results = get_labels_for_orders([("trendyol", ty_row["shipment_package_id"])])
    print("[TRENDYOL] Sonuc:")
    for r in ty_results:
        r2 = dict(r)
        if r2.get("labelData"):
            r2["labelData"] = r2["labelData"][:300] + "...(kirpildi)"
        print(" ", r2)
else:
    print("[TRENDYOL] Uygun statude ve cargo_tracking_number dolu bir Trendyol siparisi bulunamadi.")

print("======================================================================")
if hb_row:
    print("[HEPSIBURADA] Secilen siparis:", dict(hb_row))
    hb_results = get_labels_for_orders([("hepsiburada", hb_row["shipment_package_id"])])
    print("[HEPSIBURADA] Sonuc:")
    for r in hb_results:
        r2 = dict(r)
        if r2.get("labelData"):
            r2["labelData"] = r2["labelData"][:1000] + "...(kirpildi)"
        print(" ", r2)
else:
    print("[HEPSIBURADA] Hic Hepsiburada siparisi bulunamadi.")
print("======================================================================")
