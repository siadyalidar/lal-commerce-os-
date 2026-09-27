"""
tests/test_order_profit_map.py
--------------------------------
blueprints/order_routes.py::_build_order_profit_map() için testler.

B4 audit sırasında bulundu (09.09.2026): bu fonksiyon sipariş bazlı netProfit'i
satır satır toplarken sadece 'missingCost' bayrağını kontrol ediyordu; kargo
faturası eksikse (cargoMissing=True) satır sessizce 0 katkı yapıyormuş gibi
atlanıyor, ama sipariş netProfit'i yine de (eksik veriyle) gerçek bir rakammış
gibi gösteriliyordu. O tarihte düzeltme: cargoMissing durumunda netProfit None.

DEĞİŞTİ (27.09.2026, Sidar onayıyla): finance_engine.py artık kargo eksikse
sabit bir tahmin yerine SKU bazlı geçmiş ortalama kullandığı için (isabetli
hale geldiği için) bu sayfa da tahmini netProfit'i GÖSTERİYOR -- ama
cargoEstimated=True ile açıkça işaretleyerek (bkz. static/js/siparisler.js
'~' öneki). 'Sessizce veri uydurmama' ilkesi korunuyor: sadece "hiç gösterme"
yerine "açıkça tahmini olduğunu belirterek göster" tercih edildi.
"""

from datetime import datetime

import pytest

from blueprints.order_routes import _build_order_profit_map
from database import (
    upsert_cargo_costs,
    upsert_orders,
    upsert_order_lines,
    upsert_product_costs,
    upsert_settlements,
)


def _seed_order_with_settlement(spid, order_number, sku, unit_price, cost_incl_vat,
                                 now_dt, cargo_amount=0.0, marketplace="trendyol"):
    now_ms = int(now_dt.timestamp() * 1000)
    upsert_orders([{
        "shipment_package_id": spid, "marketplace": marketplace, "order_number": order_number,
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": unit_price, "discount_amount": 0.0,
        "net_amount": unit_price,
    }])
    upsert_order_lines([{
        "shipment_package_id": spid, "marketplace": marketplace, "barcode": sku,
        "merchant_sku": sku, "product_name": f"Ürün {sku}", "quantity": 1,
        "line_unit_price": unit_price, "commission_rate": 10.0,
    }])
    upsert_product_costs([{
        "sku": sku, "product_name": f"Ürün {sku}",
        "sale_price_incl_vat": unit_price, "sale_price_excl_vat": unit_price / 1.2,
        "cost_incl_vat": cost_incl_vat, "cost_excl_vat": cost_incl_vat / 1.1,
    }])
    upsert_settlements([{
        "id": f"s-{spid}", "marketplace": marketplace, "transaction_date": now_ms,
        "barcode": sku, "transaction_type": "Sale", "raw_transaction_type": "Satış",
        "receipt_id": None, "description": None, "debt": None, "credit": unit_price,
        "payment_period": None, "commission_rate": None, "commission_amount": unit_price * 0.1,
        "seller_revenue": unit_price * 0.9, "order_number": order_number,
        "payment_order_id": None, "payment_date": None, "shipment_package_id": spid,
    }])
    if cargo_amount is not None:
        upsert_cargo_costs([{
            "id": f"cargo-{spid}", "marketplace": marketplace, "invoice_serial_number": f"INV-{spid}",
            "shipment_package_id": spid, "order_number": order_number, "barcode": sku,
            "amount": cargo_amount, "raw_json": None,
        }])


def test_order_profit_map_estimated_when_cargo_missing(db):
    """DEĞİŞTİ (27.09.2026, Sidar onayıyla): B4 (09.09.2026) kargo faturası
    eksikse sipariş netProfit'ini SESSİZCE None'a düşürüyordu. Artık
    finance_engine.py SKU bazlı ortalama ile isabetli bir tahmin ürettiği
    için (bkz. finance_engine.py CARGO_AVG_LOOKBACK_DAYS / _load_sku_cargo_averages),
    Siparişler sayfası da bu tahmini netProfit'e DAHİL EDİYOR -- ama asla
    sessizce gerçekmiş gibi sunmuyor: cargoEstimated=True ile açıkça
    işaretliyor (UI'da '~' öneki gösteriyor, bkz. static/js/siparisler.js)."""
    now = datetime.now()
    _seed_order_with_settlement(700, "ONCM1", "SKU-OCM1", 100.0, 40.0, now, cargo_amount=None)

    result = _build_order_profit_map(now, now)
    key = ("trendyol", "ONCM1")
    assert key in result
    # DB'de hiç bilinen (gerçek) kargo verisi yok -> zincirin sonundaki sabit
    # CARGO_COST_FALLBACK_ESTIMATE (₺200) kullanılır.
    # profit = net_hakedis(100-10 komisyon) - cogs(40) - kargo(200) = -150.
    assert result[key]["netProfit"] == pytest.approx((100 - 10) - 40 - 200.0)
    assert result[key]["cargoEstimated"] is True


def test_order_profit_map_none_when_missing_cost_even_if_cargo_also_missing(db):
    """missingCost HER ZAMAN öncelikli: hem ürün maliyeti hem kargo faturası
    eksikse bile netProfit None kalmalı -- kargo tahmini, eksik maliyeti
    MASKELEMEMELİ (iki ayrı 'sessizce veri uydurmama' ilkesi bağımsız çalışır)."""
    now = datetime.now()
    now_ms = int(now.timestamp() * 1000)
    upsert_orders([{
        "shipment_package_id": 703, "marketplace": "trendyol", "order_number": "ONCM4",
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 703, "marketplace": "trendyol", "barcode": "SKU-OCM4",
        "merchant_sku": "SKU-OCM4", "product_name": "Ürün", "quantity": 1,
        "line_unit_price": 100.0, "commission_rate": 10.0,
    }])
    # DİKKAT: HİÇ upsert_product_costs, HİÇ upsert_cargo_costs çağrılmadı.

    result = _build_order_profit_map(now, now)
    key = ("trendyol", "ONCM4")
    assert key in result
    assert result[key]["netProfit"] is None


def test_order_profit_map_computes_when_cargo_present(db):
    """Kontrol testi: kargo faturası varsa sipariş netProfit'i normal hesaplanmalı,
    cargoEstimated False olmalı (tahmini olmadığı açıkça belli olsun)."""
    now = datetime.now()
    _seed_order_with_settlement(701, "ONCM2", "SKU-OCM2", 100.0, 40.0, now, cargo_amount=10.0)

    result = _build_order_profit_map(now, now)
    key = ("trendyol", "ONCM2")
    assert key in result
    # profit = net_hakedis(100-10 komisyon) - cogs(40) - kargo(10) = 40
    assert result[key]["netProfit"] == pytest.approx(40.0)
    assert result[key]["cargoEstimated"] is False


def test_order_profit_map_none_when_missing_cost_still_works(db):
    """Regresyon: mevcut missingCost->None davranışı korunmalı (B4 öncesi de
    doğru çalışıyordu, sadece cargoMissing eksikti)."""
    now = datetime.now()
    now_ms = int(now.timestamp() * 1000)
    upsert_orders([{
        "shipment_package_id": 702, "marketplace": "trendyol", "order_number": "ONCM3",
        "order_date": now_ms, "status": "Delivered", "customer": "Test",
        "cargo_provider": "Aras", "gross_amount": 100.0, "discount_amount": 0.0, "net_amount": 100.0,
    }])
    upsert_order_lines([{
        "shipment_package_id": 702, "marketplace": "trendyol", "barcode": "SKU-OCM3",
        "merchant_sku": "SKU-OCM3", "product_name": "Ürün", "quantity": 1,
        "line_unit_price": 100.0, "commission_rate": 10.0,
    }])
    # DİKKAT: upsert_product_costs HİÇ çağrılmadı -> maliyet bilinmiyor.
    upsert_cargo_costs([{
        "id": "cargo-702", "marketplace": "trendyol", "invoice_serial_number": "INV-702",
        "shipment_package_id": 702, "order_number": "ONCM3", "barcode": "SKU-OCM3",
        "amount": 10.0, "raw_json": None,
    }])

    result = _build_order_profit_map(now, now)
    key = ("trendyol", "ONCM3")
    assert key in result
    assert result[key]["netProfit"] is None
