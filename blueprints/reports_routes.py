"""Raporlar ekranı ve seçili tarih/pazaryeri için özet API'si."""

from collections import defaultdict
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from database import list_product_stock
from finance_engine import best_sellers, compute_profit_summary
from sync_core import _resolve_sync_range

bp = Blueprint("reports_routes", __name__)


def _marketplace_arg():
    marketplace = (request.args.get("marketplace") or "all").strip().lower()
    return marketplace if marketplace in ("all", "trendyol", "hepsiburada") else "all"


@bp.route("/raporlar")
def reports_page():
    return render_template("pages/raporlar.html", active_page="raporlar")


@bp.route("/api/reports/overview")
def reports_overview():
    """Satış, kârlılık, ürün ve stok verisini tek rapor payload'ında döner.

    Kâr hesapları mevcut finance_engine'i kullanır; böylece dashboard ile aynı
    settlement/komisyon/kargo/maliyet mantığı korunur. Tarih ekseni sipariş
    tarihidir; settlement-only kayıtların tahmin durumu quality alanında açıkça
    raporlanır.
    """
    start_dt, end_dt = _resolve_sync_range(request.args)
    marketplace = _marketplace_arg()
    mp_filter = None if marketplace == "all" else marketplace

    try:
        summary = compute_profit_summary(
            start_dt=start_dt, end_dt=end_dt, marketplace_filter=mp_filter
        )
        products = best_sellers(
            start_dt=start_dt, end_dt=end_dt, limit=8, marketplace_filter=mp_filter
        )
    except Exception as exc:
        return jsonify({"error": f"Rapor hesaplama hatası: {exc}"}), 500

    daily = defaultdict(lambda: {"revenue": 0.0, "profit": 0.0, "quantity": 0, "profitLines": 0})
    for line in summary["lines"]:
        order_date = line.get("orderDate")
        if not order_date:
            continue
        day = datetime.fromtimestamp(order_date / 1000).strftime("%Y-%m-%d")
        item = daily[day]
        item["revenue"] += line.get("grossRevenue") or 0
        item["quantity"] += line.get("quantity") or 0
        if line.get("profit") is not None:
            item["profit"] += line["profit"]
            item["profitLines"] += 1

    daily_rows = [
        {
            "date": day,
            "revenue": round(values["revenue"], 2),
            "profit": round(values["profit"], 2) if values["profitLines"] else None,
            "quantity": values["quantity"],
        }
        for day, values in sorted(daily.items())
    ]

    stock_rows = list_product_stock(mp_filter)
    low_stock = [
        row for row in stock_rows
        if row.get("quantity") is not None and row.get("min_stock_threshold") is not None
        and row["quantity"] <= row["min_stock_threshold"]
    ]
    low_stock.sort(key=lambda row: (row.get("quantity") or 0, row.get("sku") or ""))

    return jsonify({
        "marketplace": marketplace,
        "startDate": start_dt.strftime("%Y-%m-%d"),
        "endDate": end_dt.strftime("%Y-%m-%d"),
        "totals": summary["totals"],
        "byMarketplace": summary["by_marketplace"],
        "quality": summary["data_quality"],
        "daily": daily_rows,
        "products": products,
        "stock": {"lowCount": len(low_stock), "items": low_stock[:10]},
    })
