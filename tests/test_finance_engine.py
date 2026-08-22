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


@pytest.mark.parametrize("canonical,expected", [
    ("ManualRefund", "return"),
    ("ManualRefundCancel", "return"),
])
def test_categorize_trendyol_manual_refund_by_canonical_type(canonical, expected):
    """21.08.2026: ManualRefund/ManualRefundCancel'ın raw_transaction_type'ta
    (Türkçeleştirilmiş metin) TAM OLARAK hangi string ile geleceği canlı hesapla
    doğrulanmadı — bu yüzden kategorizasyon KANONİK transaction_type'a (bizim
    kontrol ettiğimiz, tahmine dayanmayan alan) dayanmalı, raw metne değil.
    Raw metin burada bilerek TANINMAYAN bir değer ('Bilinmeyen Kısmi İade
    Metni') olarak veriliyor; yine de canonical_type üzerinden doğru
    kategorize edilmeli."""
    assert fe._categorize("trendyol", "Bilinmeyen Kısmi İade Metni", canonical) == expected


def test_categorize_trendyol_unknown_raw_without_canonical_stays_other():
    """Geriye dönük uyumluluk: canonical_type verilmezse (eski çağrı yerleri,
    örn. _payout_row_delta) davranış DEĞİŞMEMELİ."""
    assert fe._categorize("trendyol", "Bilinmeyen Kısmi İade Metni") == "other"


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
# 5b) İade / COGS reversal (21.08.2026 düzeltmesi)
# ============================================================

def _setup_line(spid, sku, quantity, unit_price, cost_incl_vat, now_ms,
                 order_number, marketplace="trendyol"):
    """Ortak kurulum: bir sipariş + satırı + ürün maliyeti."""
    upsert_orders([{
        "shipment_package_id": spid, "marketplace": marketplace, "order_number": order_number,
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": unit_price * quantity,
        "discount_amount": 0.0, "net_amount": unit_price * quantity,
    }])
    upsert_order_lines([{
        "shipment_package_id": spid, "marketplace": marketplace, "barcode": sku,
        "merchant_sku": sku, "product_name": f"Ürün {sku}", "quantity": quantity,
        "line_unit_price": unit_price, "commission_rate": 10.0,
    }])
    if cost_incl_vat is not None:
        upsert_product_costs([{
            "sku": sku, "product_name": f"Ürün {sku}",
            "sale_price_incl_vat": unit_price, "sale_price_excl_vat": unit_price / 1.2,
            "cost_incl_vat": cost_incl_vat, "cost_excl_vat": cost_incl_vat / 1.1,
        }])


def test_cogs_reversal_full_return(db):
    """Tam iade (transaction_type='Return'): cogsReversal = TAM COGS (oran=1.0)."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(300, "SKU-FULL", 1, 100.0, 40.0, now_ms, "ONF1")
    upsert_settlements([
        _settlement_row(id="fr-sale", barcode="SKU-FULL", shipment_package_id=300,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="ONF1", transaction_date=now_ms),
        _settlement_row(id="fr-ret", barcode="SKU-FULL", shipment_package_id=300,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=100.0, credit=0.0, order_number="ONF1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]

    assert line["grossRevenue"] == pytest.approx(100.0)
    assert line["returnAmount"] == pytest.approx(100.0)
    assert line["netRevenue"] == pytest.approx(0.0)
    assert line["cogs"] == pytest.approx(40.0)
    assert line["cogsReversal"] == pytest.approx(40.0)  # tam iade -> tam COGS geri alınır
    assert line["cogsReversalEstimated"] is True
    # line "profit" alanı DEĞİŞMEMELİ (cogsReversal sadece toplamlarda eklenir)
    assert line["profit"] == pytest.approx(90.0 - 40.0 - 0.0)
    assert summary["totals"]["cogsReversalTotal"] == pytest.approx(40.0)


def test_cogs_reversal_partial_manual_refund(db):
    """Trendyol ManualRefund (kısmi iade): 2 adetten 1'i iade -> return_amount
    orijinal cironun yarısı -> cogsReversal de COGS'un yarısı olmalı."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(301, "SKU-PARTIAL", 2, 50.0, 20.0, now_ms, "ONP1")  # ciro=100, cogs=40 (2x20)
    upsert_settlements([
        _settlement_row(id="pr-sale", barcode="SKU-PARTIAL", shipment_package_id=301,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="ONP1", transaction_date=now_ms),
        # Kısmi iade: 1 adetlik tutar (50) iade edildi -> transaction_type='ManualRefund'
        # (raw_transaction_type bilerek TANINMAYAN bir Türkçe metin, canonical'a
        # güvenildiğini kanıtlamak için)
        _settlement_row(id="pr-refund", barcode="SKU-PARTIAL", shipment_package_id=301,
                         raw_transaction_type="Kısmi İade", transaction_type="ManualRefund",
                         debt=50.0, credit=0.0, order_number="ONP1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]

    assert line["returnAmount"] == pytest.approx(50.0)
    assert line["netRevenue"] == pytest.approx(50.0)
    assert line["cogs"] == pytest.approx(40.0)
    # oran = 50/100 = 0.5 -> cogsReversal = 40 * 0.5 = 20
    assert line["cogsReversal"] == pytest.approx(20.0)
    assert line["cogsReversalEstimated"] is True


def test_cogs_reversal_manual_refund_cancel_offsets_partial_refund(db):
    """ManualRefundCancel, önceki ManualRefund'u mahsuplaştırır (net iade -> 0),
    dolayısıyla net cogsReversal de 0 olmalı (aynı parayı iki kez saymama)."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(302, "SKU-CANCEL", 1, 100.0, 40.0, now_ms, "ONC1")
    upsert_settlements([
        _settlement_row(id="mc-sale", barcode="SKU-CANCEL", shipment_package_id=302,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="ONC1", transaction_date=now_ms),
        _settlement_row(id="mc-refund", barcode="SKU-CANCEL", shipment_package_id=302,
                         raw_transaction_type="Kısmi İade", transaction_type="ManualRefund",
                         debt=100.0, credit=0.0, order_number="ONC1", transaction_date=now_ms),
        # İptal: alacak (credit) tarafında, tam tersi yönde
        _settlement_row(id="mc-cancel", barcode="SKU-CANCEL", shipment_package_id=302,
                         raw_transaction_type="Kısmi İade İptal", transaction_type="ManualRefundCancel",
                         debt=0.0, credit=100.0, order_number="ONC1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]

    assert line["returnAmount"] == pytest.approx(0.0)  # 100 (debt) - 100 (credit) = 0
    assert line["cogsReversal"] is None  # return_amount<=0 -> reversal hesaplanmaz
    assert summary["totals"]["cogsReversalTotal"] == pytest.approx(0.0)


def test_cogs_reversal_full_return_plus_partial_refund_same_line(db):
    """Aynı satırda hem tam İade hem ayrıca bir ManualRefund kaydı olması
    (örn. iki ayrı iade olayı) -> tutarlar toplanır, oran 1.0'da sınırlanır."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(303, "SKU-COMBO", 1, 100.0, 40.0, now_ms, "ONCB1")
    upsert_settlements([
        _settlement_row(id="cb-sale", barcode="SKU-COMBO", shipment_package_id=303,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="ONCB1", transaction_date=now_ms),
        _settlement_row(id="cb-ret", barcode="SKU-COMBO", shipment_package_id=303,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=100.0, credit=0.0, order_number="ONCB1", transaction_date=now_ms),
        _settlement_row(id="cb-refund", barcode="SKU-COMBO", shipment_package_id=303,
                         raw_transaction_type="Kısmi İade", transaction_type="ManualRefund",
                         debt=30.0, credit=0.0, order_number="ONCB1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]

    # Ham toplam iade tutarı 130 olurdu (100+30), ama orijinal ciro 100'ü
    # aşamaz -> COGS reversal 1.0 oranında SINIRLANIR (40'ı geçmez).
    assert line["returnAmount"] == pytest.approx(130.0)
    assert line["cogsReversal"] == pytest.approx(40.0)
    assert line["cogsReversalNote"] == "return_amount_exceeds_revenue_clamped"


def test_cogs_reversal_return_on_different_transaction_date_than_sale(db):
    """İade, satıştan GÜNLER SONRA farklı bir transaction_date ile işlenmiş
    olabilir; her ikisi de rapor aralığındaysa (order_date VE transaction_date
    ayrı ayrı doğru eksende) cogsReversal yine doğru hesaplanmalı."""
    now = datetime.now()
    sale_ms = int((now - timedelta(days=5)).timestamp() * 1000)
    return_ms = int(now.timestamp() * 1000)
    _setup_line(304, "SKU-DATED", 1, 100.0, 40.0, sale_ms, "OND1")
    upsert_settlements([
        _settlement_row(id="dt-sale", barcode="SKU-DATED", shipment_package_id=304,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="OND1", transaction_date=sale_ms),
        _settlement_row(id="dt-ret", barcode="SKU-DATED", shipment_package_id=304,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=100.0, credit=0.0, order_number="OND1", transaction_date=return_ms),
    ])

    # Rapor aralığı hem satışı hem iadeyi kapsayacak kadar geniş (7 gün)
    summary = fe.compute_profit_summary(days=7, marketplace_filter="trendyol")
    line = summary["lines"][0]
    assert line["orderDate"] == sale_ms  # satış tarihi KORUNUYOR, iade tarihiyle karışmıyor
    assert line["returnAmount"] == pytest.approx(100.0)
    assert line["cogsReversal"] == pytest.approx(40.0)


def test_cogs_reversal_missing_cost_not_fabricated(db):
    """Maliyet bilinmiyorsa (product_costs'ta yok), gerçek bir iade olsa bile
    COGS reversal UYDURULMAMALI (None kalmalı)."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(305, "SKU-NOCOST", 1, 100.0, None, now_ms, "ONNC1")  # cost_incl_vat=None -> hiç maliyet girilmez
    upsert_settlements([
        _settlement_row(id="nc-sale", barcode="SKU-NOCOST", shipment_package_id=305,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="ONNC1", transaction_date=now_ms),
        _settlement_row(id="nc-ret", barcode="SKU-NOCOST", shipment_package_id=305,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=100.0, credit=0.0, order_number="ONNC1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]
    assert line["missingCost"] is True
    assert line["returnAmount"] == pytest.approx(100.0)
    assert line["cogsReversal"] is None
    assert line["cogsReversalNote"] == "missing_cost"
    assert summary["totals"]["cogsReversalTotal"] == pytest.approx(0.0)


def test_cogs_reversal_duplicate_settlement_row_not_double_counted(db):
    """Aynı id ile settlement satırı iki kez upsert edilirse (örn. sync tekrar
    çalıştırıldığında), iade tutarı VE cogsReversal İKİ KEZ sayılmamalı."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(306, "SKU-DUP", 1, 100.0, 40.0, now_ms, "ONDUP1")
    settlement_rows = [
        _settlement_row(id="dup-sale", barcode="SKU-DUP", shipment_package_id=306,
                         raw_transaction_type="Satış", credit=100.0, commission_amount=10.0,
                         seller_revenue=90.0, order_number="ONDUP1", transaction_date=now_ms),
        _settlement_row(id="dup-ret", barcode="SKU-DUP", shipment_package_id=306,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=100.0, credit=0.0, order_number="ONDUP1", transaction_date=now_ms),
    ]
    upsert_settlements(settlement_rows)
    upsert_settlements([dict(r) for r in settlement_rows])  # aynı id'lerle TEKRAR yazılıyor (re-sync simülasyonu)

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]
    assert line["returnAmount"] == pytest.approx(100.0)  # 200 DEĞİL
    assert line["cogsReversal"] == pytest.approx(40.0)   # 80 DEĞİL


def test_cogs_reversal_zero_original_revenue_guarded(db):
    """Orijinal ciro 0/None ise (örn. tamamen tahmini/kırık bir satır),
    sıfıra bölme olmamalı ve reversal uydurulmamalı."""
    now_ms = int(datetime.now().timestamp() * 1000)
    upsert_orders([{
        "shipment_package_id": 307, "marketplace": "trendyol", "order_number": "ONZ1",
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 0.0, "discount_amount": 0.0, "net_amount": 0.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 307, "marketplace": "trendyol", "barcode": "SKU-ZERO",
        "merchant_sku": "SKU-ZERO", "product_name": "Ürün", "quantity": 1,
        "line_unit_price": 0.0, "commission_rate": 10.0,
    }])
    upsert_product_costs([{
        "sku": "SKU-ZERO", "product_name": "Ürün",
        "sale_price_incl_vat": 0.0, "sale_price_excl_vat": 0.0,
        "cost_incl_vat": 40.0, "cost_excl_vat": 36.0,
    }])
    upsert_settlements([
        _settlement_row(id="z-ret", barcode="SKU-ZERO", shipment_package_id=307,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=10.0, credit=0.0, order_number="ONZ1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]
    assert line["cogsReversal"] is None
    assert line["cogsReversalNote"] == "zero_original_revenue"


def test_return_financial_waterfall_full_chain_matches_hand_calculation(db):
    """UÇTAN UCA REGRESYON: talep edilen tam finansal zincirin gerçek
    hesaplanan netProfit'e karşı EL İLE hesaplanmış bir beklenen sonuçla
    doğrulanması. Önceki testler returnAmount/cogsReversal'ı tek tek
    doğruluyordu; bu test onların TOPLAMDA doğru netProfit'e vardığını
    kanıtlıyor.

    Senaryo (tam iade, kargo dahil):
      Ürün birim fiyatı: 200 TL, adet: 1
      Komisyon: %10 -> 20 TL
      Kargo (outbound): 15 TL
      COGS: 80 TL
      İade: TAM (200 TL debt, 0 credit)

    EL İLE HESAP (talep edilen model):
      Gross Revenue           = 200
      Returns                 = 200
      Net Revenue             = Gross Revenue - Returns            = 0
      COGS                    = 80
      COGS Reversal           = 80 * (200/200)                     = 80  (oran=1.0, tam iade)
      Net COGS                = COGS - COGS Reversal                = 0
      Outbound Shipping       = 15
      Return Shipping         = 0   (henüz modellenmiyor, Karar 3)
      Marketplace Fees        = Komisyon 20 (+ stopaj/platform/erken ödeme = 0)
      Net Profit = Net Revenue - Net COGS - Outbound Shipping
                   - Return Shipping - Marketplace Fees - Other
                 = 0 - 0 - 15 - 0 - 20 - 0
                 = -35
    """
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(400, "SKU-WATERFALL", 1, 200.0, 80.0, now_ms, "ONWF1")
    upsert_cargo_costs([
        {"id": "wf-cargo", "marketplace": "trendyol", "invoice_serial_number": "INVWF",
         "shipment_package_id": 400, "order_number": "ONWF1", "barcode": "SKU-WATERFALL",
         "amount": 15.0, "raw_json": None},
    ])
    upsert_settlements([
        _settlement_row(id="wf-sale", barcode="SKU-WATERFALL", shipment_package_id=400,
                         raw_transaction_type="Satış", credit=200.0, commission_amount=20.0,
                         seller_revenue=180.0, order_number="ONWF1", transaction_date=now_ms),
        _settlement_row(id="wf-ret", barcode="SKU-WATERFALL", shipment_package_id=400,
                         raw_transaction_type="İade", transaction_type="Return",
                         debt=200.0, credit=0.0, order_number="ONWF1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="trendyol")
    line = summary["lines"][0]
    totals = summary["totals"]

    # --- Adım adım el hesabıyla karşılaştırma ---
    gross_revenue = 200.0
    returns = 200.0
    net_revenue = gross_revenue - returns
    assert net_revenue == 0.0
    assert line["grossRevenue"] == pytest.approx(gross_revenue)
    assert line["returnAmount"] == pytest.approx(returns)
    assert line["netRevenue"] == pytest.approx(net_revenue)
    assert totals["grossRevenue"] == pytest.approx(gross_revenue)
    assert totals["returnAmount"] == pytest.approx(returns)
    assert totals["netRevenue"] == pytest.approx(net_revenue)

    cogs = 80.0
    cogs_reversal = 80.0  # tam iade -> tam oran (200/200=1.0) -> tam COGS
    net_cogs = cogs - cogs_reversal
    assert net_cogs == 0.0
    assert line["cogs"] == pytest.approx(cogs)
    assert line["cogsReversal"] == pytest.approx(cogs_reversal)
    assert line["cogsReversalEstimated"] is True
    assert totals["cogsReversalTotal"] == pytest.approx(cogs_reversal)

    outbound_shipping = 15.0
    return_shipping = 0.0  # henüz modellenmiyor (Karar 3)
    marketplace_fees = 20.0  # komisyon (stopaj/platform/erken ödeme bu senaryoda 0)
    other_deductions = 0.0

    expected_net_profit = (
        net_revenue - net_cogs - outbound_shipping - return_shipping
        - marketplace_fees - other_deductions
    )
    assert expected_net_profit == pytest.approx(-35.0)

    # --- Gerçek motor çıktısı, EL HESABIYLA BİREBİR EŞLEŞMELİ ---
    assert totals["netProfit"] == pytest.approx(expected_net_profit)
    assert summary["by_marketplace"]["trendyol"]["netProfit"] == pytest.approx(expected_net_profit)

    # --- İade tutarının TAM OLARAK BİR KEZ sayıldığının kanıtı ---
    # Yanlış (çift sayan) bir motor netProfit'i şu şekilde üretirdi:
    #   grossProfit - overheadTotal - returnAmount  (returnAmount zaten
    #   overheadTotal'ın İÇİNDE olduğu için bu, iadeyi İKİNCİ KEZ düşer)
    gross_profit = (gross_revenue - marketplace_fees) - cogs - outbound_shipping  # netHakedis - cogs - kargo
    overhead_total = returns  # bu senaryoda stopaj/platform/erken ödeme=0, sadece returnAmount
    double_counted_net_profit = gross_profit - overhead_total - returns
    assert totals["netProfit"] != pytest.approx(double_counted_net_profit)
    assert double_counted_net_profit == pytest.approx(-315.0)  # yanlış senaryo, karşılaştırma için
    # Doğru formülde returnAmount SADECE overhead_total içinde bir kez var:
    assert totals["overheadTotal"] == pytest.approx(overhead_total)
    assert totals["netProfit"] == pytest.approx(gross_profit - overhead_total + cogs_reversal)


# ============================================================
# 5c) Hepsiburada iade/COGS reversal PARİTESİ (22.08.2026)
# ------------------------------------------------------------
# _build_line_result / _load_settlement_lines / _categorize'daki iade+COGS
# reversal mantığı MARKETPLACE-AGNOSTIK yazıldı (bkz. finance_engine.py) —
# bu bölüm bunun HB için de GERÇEKTEN doğru çalıştığını KANITLIYOR.
# Bu testler İÇİN üretim kodunda hiçbir değişiklik yapılmadı (bkz. son
# rapor) — sadece mevcut jenerik mantığın HB'de de doğru sonuç verdiği
# doğrulanıyor.
# ============================================================

def test_hb_cogs_reversal_full_return(db):
    """HB tam iade: cogsReversal = TAM COGS (oran=1.0). TY'deki
    test_cogs_reversal_full_return ile BİREBİR AYNI senaryo, sadece
    marketplace='hepsiburada' ve HB'ye özgü raw_transaction_type'lar
    ('Payment'/'Return') kullanılıyor."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(500, "HB-SKU-FULL", 1, 100.0, 40.0, now_ms, "HBONF1", marketplace="hepsiburada")
    upsert_settlements([
        _settlement_row(id="hb-fr-sale", marketplace="hepsiburada", barcode="HB-SKU-FULL",
                         shipment_package_id=500, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=100.0,
                         order_number="HBONF1", transaction_date=now_ms),
        # HB: negatif signed_amount = gider (bkz. sync_core.py sync_hb_finance_data) ->
        # debt=abs(amt), credit=0 -> return_amount pozitif
        _settlement_row(id="hb-fr-ret", marketplace="hepsiburada", barcode="HB-SKU-FULL",
                         shipment_package_id=500, raw_transaction_type="Return",
                         transaction_type="Return", debt=100.0, credit=0.0,
                         order_number="HBONF1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]

    assert line["grossRevenue"] == pytest.approx(100.0)
    assert line["returnAmount"] == pytest.approx(100.0)
    assert line["netRevenue"] == pytest.approx(0.0)
    assert line["cogs"] == pytest.approx(40.0)
    assert line["cogsReversal"] == pytest.approx(40.0)
    assert line["cogsReversalEstimated"] is True
    assert summary["totals"]["cogsReversalTotal"] == pytest.approx(40.0)


def test_hb_cogs_reversal_missing_cost_not_fabricated(db):
    """HB: maliyet bilinmiyorsa, gerçek bir iade olsa bile COGS reversal
    UYDURULMAMALI (None kalmalı) — TY ile aynı koruma."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(501, "HB-SKU-NOCOST", 1, 100.0, None, now_ms, "HBONNC1", marketplace="hepsiburada")
    upsert_settlements([
        _settlement_row(id="hb-nc-sale", marketplace="hepsiburada", barcode="HB-SKU-NOCOST",
                         shipment_package_id=501, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=100.0,
                         order_number="HBONNC1", transaction_date=now_ms),
        _settlement_row(id="hb-nc-ret", marketplace="hepsiburada", barcode="HB-SKU-NOCOST",
                         shipment_package_id=501, raw_transaction_type="Return",
                         transaction_type="Return", debt=100.0, credit=0.0,
                         order_number="HBONNC1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]
    assert line["missingCost"] is True
    assert line["returnAmount"] == pytest.approx(100.0)
    assert line["cogsReversal"] is None
    assert line["cogsReversalNote"] == "missing_cost"


def test_hb_cogs_reversal_zero_original_revenue_guarded(db):
    """HB: orijinal ciro 0 ise sıfıra bölme olmamalı, reversal uydurulmamalı."""
    now_ms = int(datetime.now().timestamp() * 1000)
    upsert_orders([{
        "shipment_package_id": 502, "marketplace": "hepsiburada", "order_number": "HBONZ1",
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "HepsiJet", "gross_amount": 0.0, "discount_amount": 0.0, "net_amount": 0.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 502, "marketplace": "hepsiburada", "barcode": "HB-SKU-ZERO",
        "merchant_sku": "HB-SKU-ZERO", "product_name": "Ürün", "quantity": 1,
        "line_unit_price": 0.0, "commission_rate": 10.0,
    }])
    upsert_product_costs([{
        "sku": "HB-SKU-ZERO", "product_name": "Ürün",
        "sale_price_incl_vat": 0.0, "sale_price_excl_vat": 0.0,
        "cost_incl_vat": 40.0, "cost_excl_vat": 36.0,
    }])
    upsert_settlements([
        _settlement_row(id="hb-z-ret", marketplace="hepsiburada", barcode="HB-SKU-ZERO",
                         shipment_package_id=502, raw_transaction_type="Return",
                         transaction_type="Return", debt=10.0, credit=0.0,
                         order_number="HBONZ1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]
    assert line["cogsReversal"] is None
    assert line["cogsReversalNote"] == "zero_original_revenue"


def test_hb_cogs_reversal_duplicate_settlement_row_not_double_counted(db):
    """HB: aynı id ile settlement satırı iki kez upsert edilirse (re-sync
    simülasyonu), iade tutarı VE cogsReversal İKİ KEZ sayılmamalı."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(503, "HB-SKU-DUP", 1, 100.0, 40.0, now_ms, "HBONDUP1", marketplace="hepsiburada")
    settlement_rows = [
        _settlement_row(id="hb-dup-sale", marketplace="hepsiburada", barcode="HB-SKU-DUP",
                         shipment_package_id=503, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=100.0,
                         order_number="HBONDUP1", transaction_date=now_ms),
        _settlement_row(id="hb-dup-ret", marketplace="hepsiburada", barcode="HB-SKU-DUP",
                         shipment_package_id=503, raw_transaction_type="Return",
                         transaction_type="Return", debt=100.0, credit=0.0,
                         order_number="HBONDUP1", transaction_date=now_ms),
    ]
    upsert_settlements(settlement_rows)
    upsert_settlements([dict(r) for r in settlement_rows])  # re-sync simülasyonu

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]
    assert line["returnAmount"] == pytest.approx(100.0)  # 200 DEĞİL
    assert line["cogsReversal"] == pytest.approx(40.0)   # 80 DEĞİL


def test_hb_return_on_different_transaction_date_than_sale(db):
    """HB: iade, satıştan günler sonra farklı bir transaction_date ile
    işlenmiş olabilir; ikisi de rapor aralığındaysa doğru hesaplanmalı,
    satış tarihi iade tarihiyle karışmamalı."""
    now = datetime.now()
    sale_ms = int((now - timedelta(days=5)).timestamp() * 1000)
    return_ms = int(now.timestamp() * 1000)
    _setup_line(504, "HB-SKU-DATED", 1, 100.0, 40.0, sale_ms, "HBOND1", marketplace="hepsiburada")
    upsert_settlements([
        _settlement_row(id="hb-dt-sale", marketplace="hepsiburada", barcode="HB-SKU-DATED",
                         shipment_package_id=504, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=100.0,
                         order_number="HBOND1", transaction_date=sale_ms),
        _settlement_row(id="hb-dt-ret", marketplace="hepsiburada", barcode="HB-SKU-DATED",
                         shipment_package_id=504, raw_transaction_type="Return",
                         transaction_type="Return", debt=100.0, credit=0.0,
                         order_number="HBOND1", transaction_date=return_ms),
    ])

    summary = fe.compute_profit_summary(days=7, marketplace_filter="hepsiburada")
    line = summary["lines"][0]
    assert line["orderDate"] == sale_ms
    assert line["returnAmount"] == pytest.approx(100.0)
    assert line["cogsReversal"] == pytest.approx(40.0)


def test_hb_cogs_reversal_return_amount_exceeds_revenue_clamped(db):
    """HB: iade tutarı orijinal ciroyu aşarsa (örn. birden fazla iade
    olayı), COGS reversal 1.0 oranında SINIRLANIR, COGS'tan fazlası asla
    tersine çevrilmez."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(505, "HB-SKU-CLAMP", 1, 100.0, 40.0, now_ms, "HBONCL1", marketplace="hepsiburada")
    upsert_settlements([
        _settlement_row(id="hb-cl-sale", marketplace="hepsiburada", barcode="HB-SKU-CLAMP",
                         shipment_package_id=505, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=100.0,
                         order_number="HBONCL1", transaction_date=now_ms),
        _settlement_row(id="hb-cl-ret1", marketplace="hepsiburada", barcode="HB-SKU-CLAMP",
                         shipment_package_id=505, raw_transaction_type="Return",
                         transaction_type="Return", debt=100.0, credit=0.0,
                         order_number="HBONCL1", transaction_date=now_ms),
        _settlement_row(id="hb-cl-ret2", marketplace="hepsiburada", barcode="HB-SKU-CLAMP",
                         shipment_package_id=505, raw_transaction_type="Return",
                         transaction_type="Return", debt=30.0, credit=0.0,
                         order_number="HBONCL1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]
    assert line["returnAmount"] == pytest.approx(130.0)
    assert line["cogsReversal"] == pytest.approx(40.0)  # asla 40'ı aşmaz
    assert line["cogsReversalNote"] == "return_amount_exceeds_revenue_clamped"


def test_hb_return_financial_waterfall_full_chain_matches_hand_calculation(db):
    """HB için TAM finansal zincir regresyonu — TY'deki
    test_return_financial_waterfall_full_chain_matches_hand_calculation
    ile BİREBİR AYNI senaryo/rakamlar (200 ciro, %10 komisyon, 15 kargo,
    80 COGS, tam iade), sadece HB'ye özgü alan/işaret kuralları kullanılarak.

    EL İLE HESAP:
      Gross Revenue = 200, Returns = 200 -> Net Revenue = 0
      COGS = 80, COGS Reversal = 80 -> Net COGS = 0
      Outbound Shipping = 15
      Marketplace Fees = Komisyon 20
      Net Profit = 0 - 0 - 15 - 20 = -35
    """
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(506, "HB-SKU-WATERFALL", 1, 200.0, 80.0, now_ms, "HBONWF1", marketplace="hepsiburada")
    upsert_cargo_costs([
        # HB kargosu İŞARETLİ: negatif = gider (bkz. test_cargo_cost_sign_hepsiburada_signed)
        {"id": "hb-wf-cargo", "marketplace": "hepsiburada", "invoice_serial_number": "INVHBWF",
         "shipment_package_id": 506, "order_number": "HBONWF1", "barcode": "HB-SKU-WATERFALL",
         "amount": -15.0, "raw_json": None},
    ])
    upsert_settlements([
        _settlement_row(id="hb-wf-sale", marketplace="hepsiburada", barcode="HB-SKU-WATERFALL",
                         shipment_package_id=506, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=200.0,
                         order_number="HBONWF1", transaction_date=now_ms),
        _settlement_row(id="hb-wf-comm", marketplace="hepsiburada", barcode="HB-SKU-WATERFALL",
                         shipment_package_id=506, raw_transaction_type="Commission",
                         transaction_type="Sale", seller_revenue=-20.0,
                         order_number="HBONWF1", transaction_date=now_ms),
        _settlement_row(id="hb-wf-ret", marketplace="hepsiburada", barcode="HB-SKU-WATERFALL",
                         shipment_package_id=506, raw_transaction_type="Return",
                         transaction_type="Return", debt=200.0, credit=0.0,
                         order_number="HBONWF1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]
    totals = summary["totals"]

    gross_revenue = 200.0
    returns = 200.0
    net_revenue = gross_revenue - returns
    assert net_revenue == 0.0
    assert line["grossRevenue"] == pytest.approx(gross_revenue)
    assert line["returnAmount"] == pytest.approx(returns)
    assert line["netRevenue"] == pytest.approx(net_revenue)
    assert totals["grossRevenue"] == pytest.approx(gross_revenue)
    assert totals["returnAmount"] == pytest.approx(returns)
    assert totals["netRevenue"] == pytest.approx(net_revenue)

    cogs = 80.0
    cogs_reversal = 80.0
    net_cogs = cogs - cogs_reversal
    assert net_cogs == 0.0
    assert line["cogs"] == pytest.approx(cogs)
    assert line["cogsReversal"] == pytest.approx(cogs_reversal)
    assert line["cogsReversalEstimated"] is True
    assert totals["cogsReversalTotal"] == pytest.approx(cogs_reversal)

    outbound_shipping = 15.0
    return_shipping = 0.0  # henüz modellenmiyor (HB için de - bkz. son rapor)
    marketplace_fees = 20.0  # komisyon
    other_deductions = 0.0

    expected_net_profit = (
        net_revenue - net_cogs - outbound_shipping - return_shipping
        - marketplace_fees - other_deductions
    )
    assert expected_net_profit == pytest.approx(-35.0)
    assert totals["netProfit"] == pytest.approx(expected_net_profit)
    assert summary["by_marketplace"]["hepsiburada"]["netProfit"] == pytest.approx(expected_net_profit)

    # --- İade tutarının TAM OLARAK BİR KEZ sayıldığının kanıtı (HB için de) ---
    gross_profit = (gross_revenue - marketplace_fees) - cogs - outbound_shipping
    overhead_total = returns
    double_counted_net_profit = gross_profit - overhead_total - returns
    assert totals["netProfit"] != pytest.approx(double_counted_net_profit)
    assert totals["overheadTotal"] == pytest.approx(overhead_total)
    assert totals["netProfit"] == pytest.approx(gross_profit - overhead_total + cogs_reversal)


# ============================================================
# 5d) HB komisyon/hizmet bedeli NETLEŞTİRME düzeltmesi (22.08.2026)
# ------------------------------------------------------------
# Gerçek DB'de doğrulanan bug: bir komisyon/hizmet bedeli ÜCRETİ (negatif
# seller_revenue) ile onun TAM İADESİ (CommissionInvoiceRefund/
# PaymentServiceCostReflectionRefund, pozitif seller_revenue) abs() ile
# aynı yöne toplandığı için ekonomik olarak 0 olması gereken bir tutar,
# yanlışlıkla 2 katına çıkan sahte bir maliyet üretiyordu (bkz. gerçek
# vaka: HB shipment_package_id=5501255780, barcode=HBCV0000D6NQ0H).
# BU BÖLÜM SADECE bu netleştirmeyi test eder — return_amount/cogsReversal
# mantığına DOKUNULMADI, ilgili testler (5b/5c) değişmeden yukarıda duruyor.
# ============================================================

def test_hb_commission_charge_and_refund_net_to_zero(db):
    """Bir komisyon ÜCRETİ + onun TAM İADESİ -> net komisyon maliyeti 0
    olmalı (önceden abs() ile 2x tutar oluyordu)."""
    rows = [
        _settlement_row(id="cf-charge", marketplace="hepsiburada",
                         raw_transaction_type="Commission", seller_revenue=-981.5),
        _settlement_row(id="cf-refund", marketplace="hepsiburada",
                         raw_transaction_type="CommissionInvoiceRefund", seller_revenue=981.5),
    ]
    upsert_settlements([dict(r) for r in rows])
    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    t = totals[("hepsiburada", 1, "BC1")]
    assert t["commission"] == pytest.approx(0.0)


def test_hb_service_fee_charge_and_refund_net_to_zero(db):
    """Aynı netleştirme, hizmet bedeli (service_fee) için."""
    rows = [
        _settlement_row(id="sf-charge", marketplace="hepsiburada",
                         raw_transaction_type="PaymentServiceCostReflection", seller_revenue=-38.5),
        _settlement_row(id="sf-refund", marketplace="hepsiburada",
                         raw_transaction_type="PaymentServiceCostReflectionRefund", seller_revenue=38.5),
    ]
    upsert_settlements([dict(r) for r in rows])
    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    t = totals[("hepsiburada", 1, "BC1")]
    assert t["service_fee"] == pytest.approx(0.0)


def test_hb_commission_only_charge_produces_correct_positive_cost(db):
    """İadesi OLMAYAN, tek başına bir komisyon ücreti -> normal pozitif
    maliyet olarak kalmalı (bu senaryoda abs()'ten önceki/sonraki davranış
    AYNIDIR — regresyon garantisi)."""
    rows = [
        _settlement_row(id="co-charge", marketplace="hepsiburada",
                         raw_transaction_type="Commission", seller_revenue=-100.0),
    ]
    upsert_settlements([dict(r) for r in rows])
    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    t = totals[("hepsiburada", 1, "BC1")]
    assert t["commission"] == pytest.approx(100.0)


def test_hb_commission_only_refund_produces_negative_net_credit(db):
    """Karşılığı olmayan (yalnız başına gelen) bir komisyon iadesi/
    düzeltmesi -> net komisyon NEGATİF olmalı (seller'a net kredi),
    0'da taban değil — gerçek işaretli veriye sadık kalınıyor."""
    rows = [
        _settlement_row(id="co-refund-only", marketplace="hepsiburada",
                         raw_transaction_type="CommissionInvoiceRefund", seller_revenue=50.0),
    ]
    upsert_settlements([dict(r) for r in rows])
    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    t = totals[("hepsiburada", 1, "BC1")]
    assert t["commission"] == pytest.approx(-50.0)


def test_hb_payout_row_delta_matches_load_settlement_lines_after_fix(db):
    """_payout_row_delta ile _load_settlement_lines'ın komisyon/service_fee
    netlemesi düzeltmeden SONRA da BİREBİR AYNI kalmalı (dosyanın kendi
    docstring'indeki 'bilinçli tekrar' garantisi)."""
    rows = [
        _settlement_row(id="pd-sale", marketplace="hepsiburada", raw_transaction_type="Payment",
                         seller_revenue=4811.30),
        _settlement_row(id="pd-comm-charge", marketplace="hepsiburada",
                         raw_transaction_type="Commission", seller_revenue=-981.5),
        _settlement_row(id="pd-comm-refund", marketplace="hepsiburada",
                         raw_transaction_type="CommissionInvoiceRefund", seller_revenue=981.5),
        _settlement_row(id="pd-svc-charge", marketplace="hepsiburada",
                         raw_transaction_type="PaymentServiceCostReflection", seller_revenue=-38.5),
        _settlement_row(id="pd-svc-refund", marketplace="hepsiburada",
                         raw_transaction_type="PaymentServiceCostReflectionRefund", seller_revenue=38.5),
    ]
    upsert_settlements([dict(r) for r in rows])
    with get_connection() as conn:
        totals = fe._load_settlement_lines(conn)
    t = totals[("hepsiburada", 1, "BC1")]

    gr = comm = svc = ret = 0.0
    for r in rows:
        d = fe._payout_row_delta("hepsiburada", r["raw_transaction_type"], r["seller_revenue"],
                                  r["debt"], r["credit"], r["commission_amount"])
        gr += d[0]; comm += d[1]; svc += d[2]; ret += d[3]

    assert gr == pytest.approx(t["gross_revenue"])
    assert comm == pytest.approx(t["commission"]) == pytest.approx(0.0)
    assert svc == pytest.approx(t["service_fee"]) == pytest.approx(0.0)


def test_hb_real_world_case_5501255780_commission_and_service_fee_net_to_zero(db):
    """GERÇEK ÜRETİM VERİSİ (22.08.2026 doğrulaması) — HB
    shipment_package_id=5501255780, barcode=HBCV0000D6NQ0H için BİREBİR
    aynı 6 settlement satırı. Beklenen (kullanıcının onayladığı) sonuç:
      grossRevenue = 4811.30
      returnAmount = 4811.30
      commission net = 0
      serviceFee net = 0
    Bu satırların NET ücret etkisi = 0 olmalı; returnAmount/cogsReversal
    davranışı DEĞİŞMEMELİ (o kısım zaten önceki pass'te PASS aldı)."""
    now_ms = int(datetime.now().timestamp() * 1000)
    _setup_line(600, "HBCV0000D6NQ0H", 1, 4811.30, 1500.0, now_ms, "REALORD1", marketplace="hepsiburada")
    upsert_settlements([
        _settlement_row(id="real-payment", marketplace="hepsiburada", barcode="HBCV0000D6NQ0H",
                         shipment_package_id=600, raw_transaction_type="Payment",
                         transaction_type="Sale", seller_revenue=4811.30,
                         order_number="REALORD1", transaction_date=now_ms),
        _settlement_row(id="real-return", marketplace="hepsiburada", barcode="HBCV0000D6NQ0H",
                         shipment_package_id=600, raw_transaction_type="Return",
                         transaction_type="Return", debt=4811.30, credit=0.0,
                         order_number="REALORD1", transaction_date=now_ms),
        _settlement_row(id="real-svc-refund", marketplace="hepsiburada", barcode="HBCV0000D6NQ0H",
                         shipment_package_id=600, raw_transaction_type="PaymentServiceCostReflectionRefund",
                         transaction_type="Sale", seller_revenue=38.5,
                         order_number="REALORD1", transaction_date=now_ms),
        _settlement_row(id="real-comm-charge", marketplace="hepsiburada", barcode="HBCV0000D6NQ0H",
                         shipment_package_id=600, raw_transaction_type="Commission",
                         transaction_type="Sale", seller_revenue=-981.5,
                         order_number="REALORD1", transaction_date=now_ms),
        _settlement_row(id="real-comm-refund", marketplace="hepsiburada", barcode="HBCV0000D6NQ0H",
                         shipment_package_id=600, raw_transaction_type="CommissionInvoiceRefund",
                         transaction_type="Sale", seller_revenue=981.5,
                         order_number="REALORD1", transaction_date=now_ms),
        _settlement_row(id="real-svc-charge", marketplace="hepsiburada", barcode="HBCV0000D6NQ0H",
                         shipment_package_id=600, raw_transaction_type="PaymentServiceCostReflection",
                         transaction_type="Sale", seller_revenue=-38.5,
                         order_number="REALORD1", transaction_date=now_ms),
    ])

    summary = fe.compute_profit_summary(days=1, marketplace_filter="hepsiburada")
    line = summary["lines"][0]

    assert line["grossRevenue"] == pytest.approx(4811.30)
    assert line["returnAmount"] == pytest.approx(4811.30)
    assert line["netRevenue"] == pytest.approx(0.0)
    assert line["commission"] == pytest.approx(0.0)
    assert line["serviceFee"] == pytest.approx(0.0)
    # netHakedis artık grossRevenue ile AYNI olmalı (ücretlerin net etkisi 0)
    assert line["netHakedis"] == pytest.approx(4811.30)
    # Bu davranış DEĞİŞMEMELİ (önceki pass'te zaten PASS): tam iade -> tam COGS reversal
    assert line["cogsReversal"] == pytest.approx(1500.0)
    assert line["cogsReversalEstimated"] is True


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


def test_payout_lag_days_recognizes_manual_refund_as_return(db):
    """21.08.2026: compute_actual_payout_lag_days da (payout_calendar ile
    aynı canonical-type yolunu paylaşıyor) artık ManualRefund'u 'return'
    olarak tanımalı, 'other' diye atlamamalı — aksi halde sample_size hiç
    dolmaz ve averageLagDays hep None kalır."""
    rows = []
    for i in range(5):
        tx_ms = int(datetime.now().timestamp() * 1000)
        pay_ms = tx_ms + 15 * 24 * 60 * 60 * 1000
        rows.append(_settlement_row(
            id=f"lagRefund{i}", marketplace="trendyol",
            raw_transaction_type="Kısmi İade", transaction_type="ManualRefund",
            debt=50.0, credit=0.0, transaction_date=tx_ms, payment_date=pay_ms,
        ))
    upsert_settlements(rows)
    result = fe.compute_actual_payout_lag_days(min_sample_size=5)
    assert result["trendyol"]["return"]["sampleSize"] == 5
    assert result["trendyol"]["return"]["averageLagDays"] == pytest.approx(15.0, abs=0.1)


def test_payout_calendar_recognizes_manual_refund(db):
    """payout_calendar() de ManualRefund'u 'return' kategorisiyle netleştirip
    ilgili güne 'lagEstimated' olarak yazmalı, sessizce atlamamalı."""
    now_ms = int(datetime.now().timestamp() * 1000)
    upsert_settlements([
        _settlement_row(id="cal-refund", marketplace="trendyol",
                         raw_transaction_type="Kısmi İade", transaction_type="ManualRefund",
                         debt=50.0, credit=0.0, transaction_date=now_ms, payment_date=None),
    ])
    calendar = fe.payout_calendar()
    total_lag_estimated = sum(
        day["byMarketplace"].get("trendyol", {}).get("lagEstimated", 0.0)
        for day in calendar["days"]
    )
    # Negatif -50 net beklenir (debt=50, credit=0 -> return_amount=+50 -> net = 0-0-0-50)
    assert total_lag_estimated == pytest.approx(-50.0)
