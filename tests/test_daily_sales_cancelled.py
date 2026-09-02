"""
tests/test_daily_sales_cancelled.py
------------------------------------
B1 (Faz 0 audit): /api/daily-sales içinde iptal edilen siparişler
cancelled_count'a sayılıyor AMA gross_amount/discount_amount/net_amount
toplamlarından çıkarılmıyordu. Bu test, gerçekten hiç gerçekleşmemiş
(NEVER_FULFILLED_STATUSES) siparişlerin toplam satış rakamlarını
şişirmediğini kanıtlar.

B1 AMENDMENT (B2 audit ile birlikte): "Returned" ilk B1 fix'inde
yanlışlıkla "Cancelled" ile aynı kefeye konup toplamdan tamamen
siliniyordu. Ama muhasebe kuralımıza göre (bkz. finance_engine.py
NEVER_FULFILLED_STATUSES + memory: "sale revenue stays in the sale's
period, return reversal goes to the return's period") bir "Returned"
sipariş GERÇEK bir satıştı — kargoya çıktı, sonra iade edildi. Satış
GÜNÜNDEKİ rakamından silinmemeli; iade ayrıca /api/daily-returns'te
görünür. Bu yüzden order_routes.py artık kendi statü listesini
tutmuyor, finance_engine.NEVER_FULFILLED_STATUSES'ı TEK KAYNAK olarak
import ediyor (B2'nin kök nedeni: iki dosyada iki farklı liste vardı).
"""

from datetime import datetime, timedelta

from database import upsert_order_lines, upsert_orders
from tests.conftest import auth_headers


def _seed_order(marketplace, shipment_package_id, order_number, status,
                 gross_amount, net_amount, order_date=None):
    order_date = order_date or datetime.now()
    upsert_orders([{
        "shipment_package_id": shipment_package_id,
        "marketplace": marketplace,
        "order_number": order_number,
        "order_date": int(order_date.timestamp() * 1000),
        "status": status,
        "customer": "Test Müşteri",
        "cargo_provider": "Test Kargo",
        "gross_amount": gross_amount,
        "discount_amount": 0.0,
        "net_amount": net_amount,
    }])
    upsert_order_lines([{
        "shipment_package_id": shipment_package_id,
        "marketplace": marketplace,
        "barcode": f"BC-{shipment_package_id}",
        "merchant_sku": f"SKU-{shipment_package_id}",
        "product_name": "Test Ürün",
        "quantity": 1,
        "line_unit_price": gross_amount,
        "commission_rate": 10.0,
    }])


def test_cancelled_order_excluded_from_daily_sales_totals(client, db):
    # end_dt = datetime.now() (bkz. _resolve_sync_range), o yüzden seed
    # tarihi gelecekte olmasın diye "az önce" kullanıyoruz.
    same_day = datetime.now() - timedelta(hours=1)

    _seed_order("trendyol", 5001, "TY-ORD-DELIVERED", "Delivered",
                gross_amount=100.0, net_amount=100.0, order_date=same_day)
    _seed_order("trendyol", 5002, "TY-ORD-CANCELLED", "Cancelled",
                gross_amount=50.0, net_amount=50.0, order_date=same_day)

    resp = client.get("/api/daily-sales?full_history=true", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()

    totals = data["totals"]
    # İptal edilen sipariş sayılmalı (cancelled_count) ama TUTARI toplama
    # dahil edilmemeli — bugün ana bug budur: fix öncesi net_amount 150.0
    # olur (yanlış), fix sonrası 100.0 olmalı (doğru).
    assert totals["cancelled_count"] == 1
    assert totals["gross_amount"] == 100.0
    assert totals["net_amount"] == 100.0

    day_key = same_day.strftime("%Y-%m-%d")
    daily_row = next(d for d in data["daily"] if d["date"] == day_key)
    assert daily_row["cancelled_count"] == 1
    assert daily_row["gross_amount"] == 100.0
    assert daily_row["net_amount"] == 100.0


def test_unsupplied_order_excluded_from_daily_sales_totals(client, db):
    # finance_engine.NEVER_FULFILLED_STATUSES ile paritenin kanıtı:
    # "UnSupplied" order_routes.py'nin eski CANCELLED_STATUSES'ında YOKTU
    # (yanlışlıkla) — hizalama sonrası bu da hariç tutulmalı.
    same_day = datetime.now() - timedelta(hours=1)

    _seed_order("trendyol", 5003, "TY-ORD-DELIVERED-2", "Delivered",
                gross_amount=300.0, net_amount=300.0, order_date=same_day)
    _seed_order("trendyol", 5004, "TY-ORD-UNSUPPLIED", "UnSupplied",
                gross_amount=40.0, net_amount=40.0, order_date=same_day)

    resp = client.get("/api/daily-sales?full_history=true", headers=auth_headers())
    assert resp.status_code == 200
    totals = resp.get_json()["totals"]

    assert totals["cancelled_count"] == 1
    assert totals["gross_amount"] == 300.0
    assert totals["net_amount"] == 300.0


def test_returned_order_included_in_daily_sales_totals(client, db):
    # AMENDMENT: "Returned" bir sipariş GERÇEK bir satıştı (kargoya çıktı,
    # sonra iade edildi) — satış GÜNÜNDEKİ toplamdan silinmemeli.
    # finance_engine.py ile PARİTE: NEVER_FULFILLED_STATUSES'ta "Returned"
    # yok, o yüzden burada da hariç tutulmamalı ve cancelled_count'a da
    # sayılmamalı (iade ayrı /api/daily-returns'te izleniyor).
    same_day = datetime.now() - timedelta(hours=1)

    _seed_order("hepsiburada", 6001, "HB-ORD-DELIVERED", "Delivered",
                gross_amount=200.0, net_amount=200.0, order_date=same_day)
    _seed_order("hepsiburada", 6002, "HB-ORD-RETURNED", "Returned",
                gross_amount=80.0, net_amount=80.0, order_date=same_day)

    resp = client.get("/api/daily-sales?full_history=true", headers=auth_headers())
    assert resp.status_code == 200
    totals = resp.get_json()["totals"]

    assert totals["cancelled_count"] == 0
    assert totals["gross_amount"] == 280.0
    assert totals["net_amount"] == 280.0
