"""
cargo_label_service.py
--------------------------
Kargo etiketi üretiminin orkestrasyon katmanı: statü kontrolü, idempotency
önbelleği (cargo_labels tablosu) ve TEKLİ/TOPLU akışların TEK bir fonksiyon
üzerinden (get_labels_for_orders) yönetilmesi. cargo_label_client.py'nin
saf HTTP wrapper'larını burada bir araya getiriyoruz.

MİMARİ KARAR (Sidar'ın 12.09.2026 talebi): "1 sipariş / 10 sipariş / 50
sipariş" senaryoları SONRADAN farklı bir mimariye geçmeye gerek kalmadan
aynı fonksiyonla çalışsın. Bu yüzden get_labels_for_orders() DAİMA bir
liste alır -- tekli istek de tek elemanlı bir liste olarak buraya gelir
(bkz. blueprints/order_routes.py'deki route'lar). Tek bir siparişte hata
olması TÜM listeyi patlatmaz -- her sipariş kendi try/except'i içinde
işlenir, sonuç listesi girdiyle aynı sırada, per-item status/error ile
döner (bkz. LabelResult).

İDEMPOTENCY (Sidar'ın açık talebi -- createCommonLabel'ın tekrar tekrar
çağrılmasında yan etki riski):
  1) Önce cargo_labels önbelleği kontrol edilir (force_refresh=True
     verilmedikçe) -- API'ye HİÇ gidilmez.
  2) Trendyol'da önbellek boşsa, ÖNCE getCommonLabel denenir (create
     ÇAĞRILMADAN) -- eğer etiket Trendyol tarafında zaten create edilmişse
     (ör. panelden, ya da önceki bir oturumdan) bu tek çağrı yeterli olur,
     createCommonLabel'a hiç gidilmez.
  3) getCommonLabel boş dönerse (TrendyolLabelError) ANCAK O ZAMAN
     createCommonLabel çağrılır, ardından getCommonLabel TEKRAR denenir.
  Hepsiburada'da böyle iki-aşamalı bir yaşam döngüsü yok (tek GET), bu
  yüzden sadece önbellek kontrolü uygulanır.
"""

import json
import logging

from cargo_label_client import (
    HepsiburadaLabelError,
    TrendyolLabelError,
    hb_fetch_package_labels,
    trendyol_create_common_label,
    trendyol_get_common_label,
)
from database import get_cached_cargo_label, get_order_for_label, save_cargo_label

logger = logging.getLogger(__name__)

# Trendyol dokümantasyonu: barkod talebi Picking/Invoiced statüsüne
# geçtikten sonra yapılmalı (bkz. Faz 0 audit). Sidar'ın 12.09.2026
# onayı: bu kontrolü sistem otomatik yapsın.
TRENDYOL_LABEL_READY_STATUSES = {"Picking", "Invoiced", "Shipped"}


def _error_result(marketplace, shipment_package_id, reason):
    return {
        "marketplace": marketplace,
        "shipmentPackageId": shipment_package_id,
        "status": "error",
        "reason": reason,
        "labelFormat": None,
        "labelData": None,
        "fromCache": False,
    }


def _ok_result(marketplace, shipment_package_id, label_format, label_data, from_cache):
    return {
        "marketplace": marketplace,
        "shipmentPackageId": shipment_package_id,
        "status": "ok",
        "reason": None,
        "labelFormat": label_format,
        "labelData": label_data,
        "fromCache": from_cache,
    }


def _get_trendyol_label(shipment_package_id, order_row, force_refresh):
    if not force_refresh:
        cached = get_cached_cargo_label("trendyol", shipment_package_id)
        if cached:
            return _ok_result("trendyol", shipment_package_id, cached["label_format"], cached["label_data"], True)

    if order_row["status"] not in TRENDYOL_LABEL_READY_STATUSES:
        return _error_result(
            "trendyol", shipment_package_id,
            f"status_not_ready: sipariş '{order_row['status']}' statüsünde -- "
            f"etiket için Picking/Invoiced/Shipped bekleniyor.",
        )

    cargo_tracking_number = order_row.get("cargo_tracking_number")
    if not cargo_tracking_number:
        return _error_result(
            "trendyol", shipment_package_id,
            "missing_cargo_tracking_number: sipariş henüz kargoya atanmamış olabilir, "
            "senkronu bekleyin ya da tekrar deneyin.",
        )

    try:
        labels = trendyol_get_common_label(cargo_tracking_number)
    except TrendyolLabelError:
        try:
            trendyol_create_common_label(cargo_tracking_number)
            labels = trendyol_get_common_label(cargo_tracking_number)
        except TrendyolLabelError as exc:
            return _error_result("trendyol", shipment_package_id, f"trendyol_api_error: {exc}")

    label_data = json.dumps(labels, ensure_ascii=False)
    save_cargo_label(
        marketplace="trendyol", shipment_package_id=shipment_package_id,
        cargo_tracking_number=cargo_tracking_number, label_format="ZPL", label_data=label_data,
    )
    return _ok_result("trendyol", shipment_package_id, "ZPL", label_data, False)


def _get_hb_label(shipment_package_id, force_refresh):
    if not force_refresh:
        cached = get_cached_cargo_label("hepsiburada", shipment_package_id)
        if cached:
            return _ok_result("hepsiburada", shipment_package_id, cached["label_format"], cached["label_data"], True)

    try:
        raw = hb_fetch_package_labels(shipment_package_id)
    except HepsiburadaLabelError as exc:
        return _error_result("hepsiburada", shipment_package_id, f"hb_api_error: {exc}")

    label_data = json.dumps(raw, ensure_ascii=False)
    save_cargo_label(
        marketplace="hepsiburada", shipment_package_id=shipment_package_id,
        cargo_tracking_number=None, label_format="RAW_JSON", label_data=label_data,
    )
    return _ok_result("hepsiburada", shipment_package_id, "RAW_JSON", label_data, False)


def get_labels_for_orders(order_keys, force_refresh=False):
    """order_keys: [(marketplace, shipment_package_id), ...] -- 1, 10 ya da
    50 eleman fark etmez, aynı fonksiyon. Girdiyle AYNI sırada, her biri
    için bağımsız bir sonuç sözlüğü döner (bkz. modül docstring'i)."""
    results = []
    for marketplace, shipment_package_id in order_keys:
        try:
            if marketplace == "trendyol":
                order_row = get_order_for_label("trendyol", shipment_package_id)
                if order_row is None:
                    results.append(_error_result("trendyol", shipment_package_id, "order_not_found"))
                    continue
                results.append(_get_trendyol_label(shipment_package_id, order_row, force_refresh))
            elif marketplace == "hepsiburada":
                results.append(_get_hb_label(shipment_package_id, force_refresh))
            else:
                results.append(_error_result(marketplace, shipment_package_id, f"unsupported_marketplace: {marketplace}"))
        except Exception as exc:  # noqa: BLE001 -- tek siparişteki beklenmeyen hata TÜM toplu işlemi düşürmesin
            logger.exception(f"[cargo_label_service] beklenmeyen hata: {marketplace}/{shipment_package_id}")
            results.append(_error_result(marketplace, shipment_package_id, f"unexpected_error: {exc}"))
    return results
