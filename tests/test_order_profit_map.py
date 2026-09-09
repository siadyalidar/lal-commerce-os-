"""
tests/test_order_profit_map.py
--------------------------------
blueprints/order_routes.py::_build_order_profit_map() için testler.

B4 audit sırasında bulundu: bu fonksiyon sipariş bazlı netProfit'i satır
satır toplarken sadece 'missingCost' bayrağını kontrol ediyordu; kargo
faturası eksik (cargoMissing=True, dolayısıyla finance_engine artık o
satırın profit'ini None döndürüyor -- bkz. B4 fix) olduğunda satır
sessizce 0 katkı yapıyormuş gibi 'elif' ile atlanıyordu -- sipariş
netProfit'i YANLIŞLIKLA gerçek bir rakammış gibi (aslında eksik veriyle)
gösteriliyordu. Doğrusu: cargoMissing durumunda da (missingCost ile AYNI
ilkeyle) sipariş netProfit'i None olmalı.
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


def test_order_profit_map_none_when_cargo_missing(db):
    """KRİTİK (B4): kargo faturası eksikse sipariş netProfit'i None olmalı --
    sessizce 0 katkı yapıp diğer satırlarla toplanmamalı."""
    now = datetime.now()
    _seed_order_with_settlement(700, "ONCM1", "SKU-OCM1", 100.0, 40.0, now, cargo_amount=None)

    result = _build_order_profit_map(now, now)
    key = ("trendyol", "ONCM1")
    assert key in result
    assert result[key]["netProfit"] is None


def test_order_profit_map_computes_when_cargo_present(db):
    """Kontrol testi: kargo faturası varsa sipariş netProfit'i normal hesaplanmalı."""
    now = datetime.now()
    _seed_order_with_settlement(701, "ONCM2", "SKU-OCM2", 100.0, 40.0, now, cargo_amount=10.0)

    result = _build_order_profit_map(now, now)
    key = ("trendyol", "ONCM2")
    assert key in result
    # profit = net_hakedis(100-10 komisyon) - cogs(40) - kargo(10) = 40
    assert result[key]["netProfit"] == pytest.approx(40.0)


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
