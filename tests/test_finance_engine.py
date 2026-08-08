"""
test_finance_engine.py
-----------------------
finance_engine.py'nin en riskli/kırılgan noktalarını hedefler:

1) _categorize(): TY/HB raw_transaction_type -> kategori eşlemesi
2) _load_settlement_lines() ile _payout_row_delta()'nın AYNI formülü
   uyguladığının doğrulanması — dosyanın kendi docstring'i bunun
   "bilinçli tekrar" olduğunu söylüyor; biri değişip diğeri unutulursa
   payout_calendar() ile compute_profit_summary() sessizce ıraksar.
3) Kargo maliyeti işaret kuralı (TY: her zaman gider / HB: işaretli)
4) payout_calendar(): official verinin estimated/lagEstimated'ın YERİNE
   geçmesi (aynı parayı iki kez saymama garantisi)
5) VAT (KDV) hesabının uçtan uca doğruluğu
"""

from datetime import datetime, timedelta

import pytest

import finance_engine as fe
from database import (
    get_connection,
    upsert_cargo_costs,
    upsert_orders,
    upsert_order_lines,
    upsert_other_financials,
    upsert_product_costs,
    upsert_settlements,
)


def _settlement_row(**overrides):
    row = {
        "id": "s1", "marketplace": "trendyol", "transaction_date": None,
        "barcode": "BC1", "transaction_type": "Sale", "raw_transaction_type": "Satış",
        "receipt_id": None, "description": None, "debt": None, "credit": None,
        "payment_period": None, "commission_rate": None, "commission_amount": None,
        "seller_revenue": None, "order_number": "ON1", "payment_order_id": None,
        "payment_date": None, "shipment_package_id": 1,
    }
    row.update(overrides)
    return row


# ============================================================
# 1) _categorize
# ============================================================

@pytest.mark.parametrize("raw,expected", [
    ("Satış", "sale"), ("Sale", "sale"),
    ("İade", "return"), ("Return", "return"),
    ("DeductionInvoices", "other"),
])
def test_categorize_trendyol(raw, expected):
    assert fe._categorize("trendyol", raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Payment", "sale"),
    ("Commission", "commission"),
    ("CommissionRefund", "commission"),
    ("PaymentServiceCostReflection", "service_fee"),
    ("ProcessingFeeExpense", "service_fee"),
    ("Return", "return"),
    ("Stoppage", "other"),
])
def test_categorize_hepsiburada(raw, expected):
    assert fe._categorize("hepsiburada", raw) == expected


# ============================================================
# 2) _load_settlement_lines vs _payout_row_delta tutarlılığı
# ============================================================

def test_settlement_totals_match_payout_row_delta_trendyol(db):
    """TY: credit=Ciro, commission_amount ayrıca düşülüyor (Sale satırı içinde)."""
    rows = [
        _settlement_row(id="s1", raw_transaction_type="Satış",
                         credit=120.0, commission_amount=12.0, seller_revenue=108.0),
        _settlement_row(id="s2", raw_transaction_type="İade",
                         debt=20.0, credit=5.0),
    ]
    upsert_settlements([dict(r) for r in rows])

    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    key = ("trendyol", 1, "BC1")
    t = totals[key]

    # _load_settlement_lines'ın ürettiği toplamlardan net_hakedis
    net_hakedis_from_totals = t["gross_revenue"] - t["commission"] - t["service_fee"]

    # Aynı satırları _payout_row_delta ile tek tek işleyip topla
    gr = comm = svc = ret = 0.0
    for r in rows:
        d = fe._payout_row_delta("trendyol", r["raw_transaction_type"], r["seller_revenue"],
                                  r["debt"], r["credit"], r["commission_amount"])
        gr += d[0]; comm += d[1]; svc += d[2]; ret += d[3]

    assert gr == pytest.approx(t["gross_revenue"])
    assert comm == pytest.approx(t["commission"])
    assert svc == pytest.approx(t["service_fee"])
    assert ret == pytest.approx(t["return_amount"])
    # net_hakedis - iade = _payout_row_net formülünün TY için ürettiğiyle aynı olmalı
    assert (net_hakedis_from_totals - t["return_amount"]) == pytest.approx(gr - comm - svc - ret)


def test_settlement_totals_match_payout_row_delta_hepsiburada(db):
    """HB: Payment=Ciro (seller_revenue), Commission/ServiceFee ayrı satırlarda düşülür."""
    rows = [
        _settlement_row(id="h1", marketplace="hepsiburada", raw_transaction_type="Payment",
                         seller_revenue=100.0),
        _settlement_row(id="h2", marketplace="hepsiburada", raw_transaction_type="Commission",
                         seller_revenue=-11.0),
        _settlement_row(id="h3", marketplace="hepsiburada",
                         raw_transaction_type="PaymentServiceCostReflection", seller_revenue=-2.0),
        _settlement_row(id="h4", marketplace="hepsiburada", raw_transaction_type="Return",
                         debt=30.0, credit=0.0),
    ]
    upsert_settlements([dict(r) for r in rows])

    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    key = ("hepsiburada", 1, "BC1")
    t = totals[key]

    gr = comm = svc = ret = 0.0
    for r in rows:
        d = fe._payout_row_delta("hepsiburada", r["raw_transaction_type"], r["seller_revenue"],
                                  r["debt"], r["credit"], r["commission_amount"])
        gr += d[0]; comm += d[1]; svc += d[2]; ret += d[3]

    assert gr == pytest.approx(t["gross_revenue"]) == 100.0
    assert comm == pytest.approx(t["commission"]) == 11.0
    assert svc == pytest.approx(t["service_fee"]) == 2.0
    assert ret == pytest.approx(t["return_amount"]) == 30.0


# ============================================================
# 3) Kargo maliyeti işaret kuralı
# ============================================================

def test_cargo_cost_sign_trendyol_always_expense(db):
    """TY: cargo_costs.amount her zaman pozitif bir GİDERDİR -> maliyet = SUM(amount)."""
    upsert_cargo_costs([
        {"id": "c1", "marketplace": "trendyol", "invoice_serial_number": "INV1",
         "shipment_package_id": 42, "order_number": "ON1", "barcode": "BC1",
         "amount": 15.0, "raw_json": None},
    ])
    with get_connection() as conn:
        by_spid, _ = fe._load_cargo_by_order(conn)
    assert by_spid[("trendyol", 42)] == pytest.approx(15.0)


def test_cargo_cost_sign_hepsiburada_signed(db):
    """HB: amount işaretli (negatif=gider) -> maliyet = -SUM(amount)."""
    upsert_cargo_costs([
        {"id": "c2", "marketplace": "hepsiburada", "invoice_serial_number": None,
         "shipment_package_id": 7, "order_number": "ON2", "barcode": "BC2",
         "amount": -25.0, "raw_json": None},
    ])
    with get_connection() as conn:
        by_spid, _ = fe._load_cargo_by_order(conn)
    assert by_spid[("hepsiburada", 7)] == pytest.approx(25.0)  # gider olarak pozitif maliyet


def test_cargo_cost_hepsiburada_income_reduces_cost(db):
    """HB'de pozitif (gelir/tazminat) bir kargo kaydı gelirse maliyeti ARTIRMAMALI,
    aksine düşürmeli (bkz. finance_engine.py _load_cargo_by_order docstring'i)."""
    upsert_cargo_costs([
        {"id": "c3", "marketplace": "hepsiburada", "invoice_serial_number": None,
         "shipment_package_id": 8, "order_number": "ON3", "barcode": "BC3",
         "amount": -25.0, "raw_json": None},
        {"id": "c4", "marketplace": "hepsiburada", "invoice_serial_number": None,
         "shipment_package_id": 8, "order_number": "ON3", "barcode": "BC3",
         "amount": 10.0, "raw_json": None},  # tazminat/gelir
    ])
    with get_connection() as conn:
        by_spid, _ = fe._load_cargo_by_order(conn)
    # net = -25 + 10 = -15 -> cost = -net = 15 (25 değil, tazminat düşmüş olmalı)
    assert by_spid[("hepsiburada", 8)] == pytest.approx(15.0)


# ============================================================
# 4) payout_calendar: official verinin estimated/lagEstimated'ın yerine geçmesi
# ============================================================

def test_payout_calendar_official_overrides_estimated(db, monkeypatch):
    future_dt = datetime.now() + timedelta(days=10)
    future_ms = int(future_dt.timestamp() * 1000)

    upsert_settlements([_settlement_row(
        id="s10", marketplace="trendyol", raw_transaction_type="Satış",
        credit=200.0, commission_amount=20.0, payment_date=future_ms,
    )])

    from external_payout_db import save_estimates
    save_estimates("trendyol", [
        {"region_code": "TR", "payment_date": future_ms, "amount": 999.0},
    ])

    result = fe.payout_calendar(marketplace_filter="trendyol")
    day_key = future_dt.strftime("%Y-%m-%d")
    matching = [d for d in result["days"] if d["date"] == day_key]
    assert matching, "official veri işlenen günde bir kayıt bekleniyordu"
    entry = matching[0]["byMarketplace"]["trendyol"]

    # official gelince estimated/lagEstimated sıfırlanmalı, official dolmalı
    assert entry["official"] == pytest.approx(999.0)
    assert entry["estimated"] == 0.0
    assert entry["lagEstimated"] == 0.0
    assert entry.get("overdue") is not True


def test_payout_calendar_confirmed_from_payment_order(db):
    """Trendyol GERÇEK banka ödemesi (other_financials.PaymentOrder) -> confirmed."""
    pay_dt = datetime.now() - timedelta(days=1)
    pay_ms = int(pay_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    upsert_other_financials([{
        "id": "of1", "marketplace": "trendyol", "transaction_date": pay_ms,
        "barcode": None, "transaction_type": "PaymentOrder", "raw_transaction_type": "PaymentOrder",
        "transaction_sub_type": None, "receipt_id": None, "description": None,
        "debt": 500.0, "credit": 0.0, "order_number": None, "payment_order_id": None,
        "payment_date": pay_ms, "shipment_package_id": None,
    }])
    result = fe.payout_calendar(marketplace_filter="trendyol")
    day_key = datetime.fromtimestamp(pay_ms / 1000).strftime("%Y-%m-%d")
    matching = [d for d in result["days"] if d["date"] == day_key]
    assert matching
    assert matching[0]["byMarketplace"]["trendyol"]["confirmed"] == pytest.approx(500.0)


# ============================================================
# 5) VAT (KDV) hesabı uçtan uca
# ============================================================

def test_profit_summary_vat_calculation(db):
    now_ms = int(datetime.now().timestamp() * 1000)
    upsert_orders([{
        "shipment_package_id": 100, "marketplace": "trendyol", "order_number": "ONV1",
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 120.0, "discount_amount": 0.0, "net_amount": 120.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 100, "marketplace": "trendyol", "barcode": "SKU-VAT",
        "merchant_sku": "SKU-VAT", "product_name": "Test Ürün", "quantity": 1,
        "line_unit_price": 120.0, "commission_rate": 10.0,
    }])
    upsert_settlements([_settlement_row(
        id="sv1", marketplace="trendyol", barcode="SKU-VAT", shipment_package_id=100,
        raw_transaction_type="Satış", credit=120.0, commission_amount=12.0,
        seller_revenue=108.0, order_number="ONV1",
    )])
    # Satış fiyatı %20 KDV dahil (120 dahil / 100 hariç), maliyet %10 KDV dahil (55/50)
    upsert_product_costs([{
        "sku": "SKU-VAT", "product_name": "Test Ürün",
        "sale_price_incl_vat": 120.0, "sale_price_excl_vat": 100.0,
        "cost_incl_vat": 55.0, "cost_excl_vat": 50.0,
    }])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]

    assert line["grossRevenue"] == pytest.approx(120.0)
    assert line["revenueExclVat"] == pytest.approx(100.0)
    assert line["vatOnSale"] == pytest.approx(20.0)
    assert line["cogs"] == pytest.approx(55.0)
    assert line["cogsExclVat"] == pytest.approx(50.0)
    assert line["vatOnCost"] == pytest.approx(5.0)
    # netHakedis = 120 - 12 (commission) - 0 (service_fee) = 108
    assert line["netHakedis"] == pytest.approx(108.0)
    # profit = netHakedis - cogs - cargo(0) = 108 - 55 = 53
    assert line["profit"] == pytest.approx(53.0)


# ============================================================
# 6) compute_actual_payout_lag_days (kalibrasyon teşhis fonksiyonu)
# ============================================================

def test_compute_actual_payout_lag_days_below_threshold_returns_none(db):
    """Örneklem min_sample_size'ın altındaysa averageLagDays None dönmeli
    (az veriyle güvenilmez bir kalibrasyon önerilmemeli)."""
    tx_ms = int(datetime.now().timestamp() * 1000)
    pay_ms = tx_ms + 10 * 24 * 60 * 60 * 1000  # 10 gün sonra
    upsert_settlements([_settlement_row(
        id="lag1", marketplace="trendyol", raw_transaction_type="Satış",
        credit=100.0, commission_amount=10.0, transaction_date=tx_ms, payment_date=pay_ms,
    )])
    result = fe.compute_actual_payout_lag_days(min_sample_size=20)
    assert result["trendyol"]["sale"]["sampleSize"] == 1
    assert result["trendyol"]["sale"]["averageLagDays"] is None
    assert result["trendyol"]["sale"]["currentStaticValue"] == fe._AVG_PAYOUT_LAG_DAYS["trendyol"]["sale"]


def test_today_net_profit_excludes_overhead_from_orders_outside_window(db):
    """08.08.2026 REGRESYON TESTİ: 'bugünkü net kâr' (include_settlement_only=False)
    hesaplanırken, bugün hiç sipariş olmasa bile GEÇMİŞ bir siparişe ait, bugün
    işlenen bir iade kaydı overhead'e (ve net_profit'e) karışmamalı. Aksi halde
    'Bugünkü Satış=0' iken 'Net Kazanç>0' gibi imkânsız görünen bir durum
    oluşuyordu (bkz. finance_engine._load_return_totals docstring'i)."""
    now = datetime.now()
    start_of_today = datetime(now.year, now.month, now.day)
    end_of_today = start_of_today + timedelta(days=1)
    two_days_ago_ms = int((start_of_today - timedelta(days=2)).timestamp() * 1000)
    today_ms = int((start_of_today + timedelta(hours=1)).timestamp() * 1000)

    # Sipariş 2 gün önce verildi (BUGÜNÜN penceresinin dışında) -> bugünün
    # 'lines' listesi boş kalacak.
    upsert_orders([{
        "shipment_package_id": 200, "marketplace": "trendyol", "order_number": "ONY1",
        "order_date": two_days_ago_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 200, "marketplace": "trendyol", "barcode": "SKU-OLD",
        "merchant_sku": "SKU-OLD", "product_name": "Eski Ürün", "quantity": 1,
        "line_unit_price": 100.0, "commission_rate": 10.0,
    }])
    # O siparişin iadesi BUGÜN işleniyor (transaction_date = bugün), net kredi
    # ağırlıklı (debt=0, credit=50) -> total = 0 - 50 = -50 (overhead'i negatife
    # çekip net_profit'i yanlışlıkla pozitif gösterirdi, düzeltmeden önce).
    upsert_settlements([_settlement_row(
        id="ret1", marketplace="trendyol", barcode="SKU-OLD", shipment_package_id=200,
        raw_transaction_type="İade", debt=0.0, credit=50.0,
        order_number="ONY1", transaction_date=today_ms,
    )])

    summary = fe.compute_profit_summary(
        start_dt=start_of_today, end_dt=end_of_today, marketplace_filter=None,
        include_settlement_only=False,
    )
    t = summary["totals"]
    assert t["grossProfit"] == pytest.approx(0.0)    # bugün hiç sipariş yok
    assert t["overheadTotal"] == pytest.approx(0.0)   # geçmiş siparişin iadesi karışmamalı
    assert t["netProfit"] == pytest.approx(0.0)
    assert summary["by_marketplace"] == {}


def test_periodic_report_still_includes_overhead_regardless_of_order_date(db):
    """Dönemsel (varsayılan include_settlement_only=True) raporlarda davranış
    DEĞİŞMEMELİ: aralıktaki TÜM iade kayıtları, siparişin o aralıkta verilip
    verilmediğine bakılmaksızın sayılmaya devam etmeli (cash-flow görünümü) —
    yukarıdaki 'bugünkü net kâr' düzeltmesi sadece include_settlement_only=False
    olan çağrıları etkiler."""
    now = datetime.now()
    order_date_ms = int((now - timedelta(days=10)).timestamp() * 1000)  # aralığın DIŞINDA
    tx_ms = int(now.timestamp() * 1000)  # ama iade aralığın İÇİNDE (bugün) işlendi

    upsert_orders([{
        "shipment_package_id": 201, "marketplace": "trendyol", "order_number": "ONY2",
        "order_date": order_date_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 201, "marketplace": "trendyol", "barcode": "SKU-OLD2",
        "merchant_sku": "SKU-OLD2", "product_name": "Eski Ürün 2", "quantity": 1,
        "line_unit_price": 100.0, "commission_rate": 10.0,
    }])
    upsert_settlements([_settlement_row(
        id="ret2", marketplace="trendyol", barcode="SKU-OLD2", shipment_package_id=201,
        raw_transaction_type="İade", debt=0.0, credit=50.0,
        order_number="ONY2", transaction_date=tx_ms,
    )])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")  # include_settlement_only=True (varsayılan)
    assert summary["totals"]["overheadTotal"] == pytest.approx(-50.0)
    assert summary["totals"]["netProfit"] == pytest.approx(-summary["totals"]["overheadTotal"])


def test_compute_actual_payout_lag_days_computes_average(db):
    """min_sample_size karşılanınca gerçek ortalama gecikme hesaplanmalı."""
    rows = []
    for i in range(5):
        tx_ms = int(datetime.now().timestamp() * 1000)
        pay_ms = tx_ms + 20 * 24 * 60 * 60 * 1000  # her satır tam 20 gün sonra ödenmiş
        rows.append(_settlement_row(
            id=f"lagN{i}", marketplace="trendyol", raw_transaction_type="Satış",
            credit=100.0, commission_amount=10.0, transaction_date=tx_ms, payment_date=pay_ms,
        ))
    upsert_settlements(rows)
    result = fe.compute_actual_payout_lag_days(min_sample_size=5)
    assert result["trendyol"]["sale"]["sampleSize"] == 5
    assert result["trendyol"]["sale"]["averageLagDays"] == pytest.approx(20.0, abs=0.1)
