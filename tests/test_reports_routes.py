"""
tests/test_reports_routes.py
------------------------------
Raporlar sayfası ve /api/reports/* uç noktaları için testler.

Kapsam:
- /raporlar sayfası: auth olmadan 401, auth ile 200.
- /api/reports/overview: totals içinde returnAmount/netRevenue/cogsReversalTotal
  dahil finance_engine'in tüm alanlarının bulunduğunu doğrular (regresyon --
  frontend'in bu alanları render etmesi için backend'in bunları sızdırmaya
  devam ettiğinden emin olunur).
- /api/reports/export: CSV export İKİNCİ BİR HESAPLAMA MOTORU KULLANMAZ --
  compute_profit_summary çıktısını doğrudan seri hale getirir. Bu testler
  export'taki rakamların /api/reports/overview ile birebir aynı olduğunu
  doğrular.
"""

import csv
import io

import pytest

from tests.conftest import auth_headers


def test_reports_page_requires_auth(client):
    resp = client.get("/raporlar")
    assert resp.status_code == 401


def test_reports_page_available_with_auth(client):
    resp = client.get("/raporlar", headers=auth_headers())
    assert resp.status_code == 200


def test_reports_overview_requires_auth(client):
    resp = client.get("/api/reports/overview")
    assert resp.status_code == 401


def test_reports_overview_shape(client):
    resp = client.get("/api/reports/overview", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()

    assert "totals" in data
    for key in (
        "grossRevenue", "returnAmount", "netRevenue", "netHakedis",
        "commission", "serviceFee", "grossProfit", "cargoTotal",
        "cogsReversalTotal", "stoppage", "platformServiceFee",
        "cashAdvanceCost", "returnCount", "overheadTotal", "netProfit",
        "vatOnSales", "vatOnPurchases", "vatPayableEstimate",
        "netProfitAfterVatEstimate", "paymentOrderNet",
    ):
        assert key in data["totals"], f"totals içinde '{key}' eksik"

    assert "byMarketplace" in data
    assert "quality" in data
    assert "daily" in data
    assert "products" in data
    assert "stock" in data


def test_reports_overview_daily_chart_reflects_return_in_return_month(client):
    """KRİTİK REGRESYON: bir sipariş Temmuz'da satılıp Eylül'de iade edilmişse,
    /api/reports/overview'un 'daily' kırılımında EYLÜL günü negatif etkilenmeli
    (ciro/kâr düşmeli) — önceki davranışta 'daily' iadeyi HİÇ hesaba katmıyordu
    (sadece grossRevenue/line-profit toplanıyordu, returnAmount/cogsReversal
    hiç kullanılmıyordu)."""
    from datetime import datetime

    from database import upsert_orders, upsert_order_lines, upsert_product_costs, upsert_settlements

    sale_dt = datetime(2026, 7, 2, 9, 0, 0)
    return_dt = datetime(2026, 9, 4, 5, 0, 0)
    sale_ms = int(sale_dt.timestamp() * 1000)
    return_ms = int(return_dt.timestamp() * 1000)

    upsert_orders([{
        "shipment_package_id": 900, "marketplace": "trendyol", "order_number": "ONDAILY1",
        "order_date": sale_ms, "status": "Returned", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 700.0, "discount_amount": 0.0, "net_amount": 700.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 900, "marketplace": "trendyol", "barcode": "SKU-DAILY1",
        "merchant_sku": "SKU-DAILY1", "product_name": "Ürün", "quantity": 1,
        "line_unit_price": 700.0, "commission_rate": 10.0,
    }])
    upsert_product_costs([{
        "sku": "SKU-DAILY1", "product_name": "Ürün",
        "sale_price_incl_vat": 700.0, "sale_price_excl_vat": 700.0 / 1.2,
        "cost_incl_vat": 300.0, "cost_excl_vat": 300.0 / 1.1,
    }])
    upsert_settlements([
        {"id": "daily-sale", "marketplace": "trendyol", "transaction_date": sale_ms,
         "barcode": "SKU-DAILY1", "transaction_type": "Sale", "raw_transaction_type": "Satış",
         "receipt_id": None, "description": None, "debt": None, "credit": 700.0,
         "payment_period": None, "commission_rate": None, "commission_amount": 70.0,
         "seller_revenue": 630.0, "order_number": "ONDAILY1", "payment_order_id": None,
         "payment_date": None, "shipment_package_id": 900},
        {"id": "daily-ret", "marketplace": "trendyol", "transaction_date": return_ms,
         "barcode": "SKU-DAILY1", "transaction_type": "Return", "raw_transaction_type": "İade",
         "receipt_id": None, "description": None, "debt": 700.0, "credit": 0.0,
         "payment_period": None, "commission_rate": None, "commission_amount": None,
         "seller_revenue": None, "order_number": "ONDAILY1", "payment_order_id": None,
         "payment_date": None, "shipment_package_id": 900},
    ])

    resp = client.get(
        "/api/reports/overview?full_history=true&marketplace=trendyol",
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    daily_by_date = {row["date"]: row for row in data["daily"]}

    assert "2026-09-04" in daily_by_date, "iade günü grafikte hiç görünmüyor"
    assert daily_by_date["2026-09-04"]["revenue"] < 0, "iade günü ciro düşmeli (negatif etki)"
    assert daily_by_date["2026-09-04"]["profit"] < 0, "iade günü kâr düşmeli (negatif etki)"

    # Temmuz'un satış günü hâlâ tam olarak görünmeli (iadeden etkilenmemeli)
    assert daily_by_date["2026-07-02"]["revenue"] == pytest.approx(700.0)


def test_reports_export_requires_auth(client):
    resp = client.get("/api/reports/export")
    assert resp.status_code == 401


def test_reports_export_returns_csv(client):
    resp = client.get("/api/reports/export", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers.get("Content-Disposition", "")

    text = resp.get_data(as_text=True)
    assert "Net Kâr" in text
    assert "İade Tutarı" in text
    assert "Pazaryeri Kırılımı" in text


def test_reports_export_matches_overview_totals(client):
    """CSV export ile /api/reports/overview AYNI compute_profit_summary
    çağrısından geçmeli -- rakamlar birebir eşleşmeli (ikinci bir hesaplama
    motoru yok)."""
    overview_resp = client.get("/api/reports/overview", headers=auth_headers())
    overview_totals = overview_resp.get_json()["totals"]

    export_resp = client.get("/api/reports/export", headers=auth_headers())
    text = export_resp.get_data(as_text=True).lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    label_to_key = {
        "Ciro (Brüt Gelir)": "grossRevenue",
        "İade Tutarı": "returnAmount",
        "Net Ciro": "netRevenue",
        "Net Kâr": "netProfit",
        "COGS İade Geri Alımı": "cogsReversalTotal",
    }
    csv_values = {}
    for row in rows:
        if len(row) == 2 and row[0] in label_to_key:
            csv_values[label_to_key[row[0]]] = row[1]

    for key in ("grossRevenue", "returnAmount", "netRevenue", "netProfit", "cogsReversalTotal"):
        assert key in csv_values, f"CSV'de '{key}' satırı bulunamadı"
        assert float(csv_values[key]) == overview_totals[key], (
            f"{key}: CSV={csv_values[key]} overview={overview_totals[key]} -- "
            "export ikinci bir hesaplama motoru kullanıyor olabilir"
        )
