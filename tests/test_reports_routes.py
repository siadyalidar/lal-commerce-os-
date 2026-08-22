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
