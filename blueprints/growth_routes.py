"""Satış büyütme sinyalleri: kampanya kârlılığı, müşteri soruları, iade aksiyonları.

NOT (09.08.2026): Trendyol webhook alıcısı bu dosyadan kasıtlı olarak
çıkarıldı. Sebep: LAL'in internete açık HTTPS adresi yok, dolayısıyla webhook
zaten hiç tetiklenemiyor; ayrıca webhook'un dayandığı sync_trendyol_order_payload
fonksiyonu sync_core.py'de hiç mevcut değildi ve import edildiği an tüm
uygulamayı ImportError ile çökertiyordu. Kalıcı HTTPS çözümü (Cloudflare
Tunnel vb.) kurulduğunda webhook ayrı bir adımda, sync_trendyol_order_payload
fonksiyonu gerçekten yazılarak eklenebilir.
"""

from datetime import datetime, timedelta

import requests
from flask import Blueprint, jsonify, request

from database import get_connection
from sync_core import (
    API_KEY, API_SECRET, BASE_URL, SUPPLIER_ID, USER_AGENT, _check_credentials,
    normalize_trendyol_epoch_ms, trendyol_get,
)

bp = Blueprint("growth_routes", __name__)


@bp.route("/api/growth/campaign-profit")
def campaign_profit():
    """Sipariş satırındaki salesCampaignId ile kampanya kaynaklı net katkı.

    Reklam harcaması gerektirmez: bu, pazaryeri satış kampanyalarının satıcı
    indirimi ve tahmini ürün maliyeti sonrası gerçekten para bırakıp
    bırakmadığını gösterir. Sadece sales_campaign_id dolu olan (yeni
    senkronda yazılan) satırları kapsar — migration öncesi eski satırlarda
    bu alan NULL olduğu için otomatik olarak hariç tutulur.
    """
    days = max(1, min(request.args.get("days", default=30, type=int) or 30, 3650))
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT ol.sales_campaign_id AS campaignId,
                   ol.merchant_sku AS sku,
                   MAX(ol.product_name) AS productName,
                   SUM(COALESCE(ol.quantity, 0)) AS quantity,
                   SUM(COALESCE(ol.line_unit_price, 0) * COALESCE(ol.quantity, 0)) AS revenue,
                   SUM(COALESCE(ol.seller_discount, 0) * COALESCE(ol.quantity, 0)) AS sellerDiscount,
                   SUM(COALESCE(ol.marketplace_discount, 0) * COALESCE(ol.quantity, 0)) AS marketplaceDiscount,
                   SUM(COALESCE(ol.line_unit_price, 0) * COALESCE(ol.quantity, 0)
                       * COALESCE(ol.commission_rate, 0) / 100.0) AS estimatedCommission,
                   SUM(COALESCE(pc.cost_incl_vat, 0) * COALESCE(ol.quantity, 0)) AS productCost,
                   SUM(CASE WHEN pc.sku IS NULL THEN 1 ELSE 0 END) AS missingCostLines
            FROM order_lines ol
            JOIN orders o ON o.marketplace = ol.marketplace
                         AND o.shipment_package_id = ol.shipment_package_id
            LEFT JOIN product_costs pc ON pc.sku = ol.merchant_sku
            WHERE ol.marketplace = 'trendyol'
              AND ol.sales_campaign_id IS NOT NULL
              AND o.order_date BETWEEN ? AND ?
              AND COALESCE(o.status, '') NOT IN ('Cancelled', 'İptal Edildi', 'UnSupplied', 'UnDelivered', 'UnPacked')
            GROUP BY ol.sales_campaign_id, ol.merchant_sku
            ORDER BY campaignId, revenue DESC
        """, (start_ms, end_ms)).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["estimatedContribution"] = round(
            item["revenue"] - item["sellerDiscount"] - item["estimatedCommission"] - item["productCost"], 2
        )
        item["hasCompleteCost"] = item.pop("missingCostLines") == 0
        for key in ("revenue", "sellerDiscount", "marketplaceDiscount", "estimatedCommission", "productCost"):
            item[key] = round(item[key] or 0, 2)
        items.append(item)
    return jsonify({"days": days, "items": items})


def _trendyol_date_chunks(days):
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    current = start_dt
    while current < end_dt:
        chunk_end = min(current + timedelta(days=14), end_dt)
        yield int(current.timestamp() * 1000), int(chunk_end.timestamp() * 1000)
        current = chunk_end


def _question_action(question):
    text = (question.get("text") or "").lower()
    if any(term in text for term in ("beden", "ölçü", "boy", "ebat", "cm")):
        return "Ölçü/beden bilgisi soruluyor: ürün açıklaması ve görsellere net ölçü tablosu ekleyin."
    if any(term in text for term in ("renk", "materyal", "malzeme", "kumaş", "içerik")):
        return "Ürün özelliği belirsiz: başlık, açıklama ve kategori özelliklerini netleştirin."
    if any(term in text for term in ("kargo", "teslim", "ne zaman", "gelir mi")):
        return "Teslimat sorusu: kargoya veriliş süresi ve teslimat beklentisini görünürleştirin."
    return "Yanıtı bilgi bankasına ekleyin; aynı soru tekrar ederse ürün açıklamasını güncelleyin."


@bp.route("/api/growth/questions/sync", methods=["POST"])
def sync_customer_questions():
    """Trendyol Soru&Cevap API'sinden son en fazla 90 günün sorularını çeker.

    Platform iki haftalık pencere sınırladığı için çağrılar parçalanır.
    """
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400
    data = request.get_json(silent=True) or {}
    days = max(1, min(int(data.get("days", 30)), 90))
    status = data.get("status", "WAITING_FOR_ANSWER")
    if status not in {"WAITING_FOR_ANSWER", "ANSWERED", "REPORTED", "REJECTED", "UNANSWERED"}:
        return jsonify({"error": "Geçersiz soru durumu."}), 400

    questions = []
    try:
        for start_ms, end_ms in _trendyol_date_chunks(days):
            page = 0
            while True:
                payload = trendyol_get(
                    f"/integration/qna/sellers/{SUPPLIER_ID}/questions/filter",
                    {"supplierId": SUPPLIER_ID, "startDate": start_ms, "endDate": end_ms,
                     "status": status, "page": page, "size": 50,
                     "orderByField": "CreatedDate", "orderByDirection": "DESC"},
                )
                questions.extend(payload.get("content") or [])
                page += 1
                if page >= (payload.get("totalPages") or 1):
                    break
    except requests.RequestException as exc:
        return jsonify({"error": f"Trendyol soru API hatası: {exc}"}), 502

    seen = set()
    items = []
    for question in questions:
        question_id = question.get("id")
        if question_id in seen:
            continue
        seen.add(question_id)
        item = {"id": question_id, "productName": question.get("productName"),
                "text": question.get("text"), "status": question.get("status"),
                "createdAt": normalize_trendyol_epoch_ms(question.get("creationDate")),
                "webUrl": question.get("webUrl"), "action": _question_action(question)}
        items.append(item)
    return jsonify({"days": days, "status": status, "items": items, "total": len(items)})


@bp.route("/api/growth/questions/<int:question_id>/answer", methods=["POST"])
def answer_customer_question(question_id):
    """LAL'deki kullanıcı onayıyla Trendyol'a yanıt gönderir; otomatik göndermez."""
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if data.get("confirm") is not True:
        return jsonify({"error": "Yanıt göndermek için confirm: true zorunlu."}), 400
    if not 10 <= len(text) <= 2000:
        return jsonify({"error": "Yanıt 10-2000 karakter olmalı."}), 400
    try:
        response = requests.post(
            f"{BASE_URL}/integration/qna/sellers/{SUPPLIER_ID}/questions/{question_id}/answers",
            json={"text": text}, headers={"User-Agent": USER_AGENT}, auth=(API_KEY, API_SECRET), timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return jsonify({"error": f"Trendyol yanıt API hatası: {exc}"}), 502
    return jsonify({"ok": True, "answerId": (response.json() or {}).get("answerId")})


@bp.route("/api/growth/return-actions")
def return_actions():
    """İade API'sindeki nedenleri SKU/ürün düzeyinde eyleme dönüştürür."""
    error = _check_credentials()
    if error:
        return jsonify({"error": error}), 400
    days = max(1, min(request.args.get("days", default=90, type=int) or 90, 365))
    buckets = {}
    try:
        for start_ms, end_ms in _trendyol_date_chunks(days):
            page = 0
            while True:
                payload = trendyol_get(
                    f"/integration/order/sellers/{SUPPLIER_ID}/claims",
                    {"startDate": start_ms, "endDate": end_ms, "page": page, "size": 200},
                )
                for claim in payload.get("content") or []:
                    for line in claim.get("claimItems") or claim.get("items") or []:
                        reason = line.get("claimReason") or line.get("reason") or claim.get("claimReason") or "Belirtilmemiş"
                        sku = line.get("merchantSku") or line.get("barcode") or "Bilinmeyen SKU"
                        key = (sku, str(reason))
                        bucket = buckets.setdefault(key, {"sku": sku, "reason": str(reason), "count": 0,
                                                           "productName": line.get("productName")})
                        bucket["count"] += line.get("quantity") or 1
                page += 1
                if page >= (payload.get("totalPages") or 1):
                    break
    except requests.RequestException as exc:
        return jsonify({"error": f"Trendyol iade API hatası: {exc}"}), 502

    def recommendation(reason):
        text = reason.lower()
        if any(x in text for x in ("kır", "hasar", "bozuk")):
            return "Paketleme ve kalite kontrolünü inceleyin; hasarlı gönderim maliyetini ölçün."
        if any(x in text for x in ("beden", "küçük", "büyük", "uyum")):
            return "Ölçü tablosu, varyant adı ve ürün açıklamasını güncelleyin."
        if any(x in text for x in ("farklı", "görsel", "beklenti")):
            return "Görsel/açıklama uyumunu denetleyin; yanıltıcı vaadi kaldırın."
        if any(x in text for x in ("geç", "teslim", "kargo")):
            return "Hazırlama süresi ve kargo performansını kontrol edin."
        return "İade nedenini ürün açıklaması, kalite ve fiyat verisiyle birlikte inceleyin."

    items = sorted(buckets.values(), key=lambda item: item["count"], reverse=True)
    for item in items:
        item["action"] = recommendation(item["reason"])
    return jsonify({"days": days, "items": items})
