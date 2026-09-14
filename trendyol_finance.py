"""
trendyol_finance.py
---------------------
Trendyol Finans API'sinden (Cari Hesap Ekstresi + Kargo Faturası Detayları)
gerçek hakediş, kesinti ve kargo maliyeti verilerini çeker.

Kaynaklar:
  https://developers.trendyol.com/docs/cari-hesap-ekstresi-entegrasyonu
  https://developers.trendyol.com/docs/kargo-faturası-detayları
  https://developers.trendyol.com/reference/getcargoinvoiceitems

ÜÇ SERVİS:
  - settlements      -> satış / iade hareketleri (satır bazlı gerçek komisyon ve hakediş)
  - otherfinancials   -> stopaj, kesinti faturaları, erken ödeme, hakediş ödemesi (dönem bazlı)
  - cargo-invoice/{invoiceSerialNumber}/items -> otherfinancials'taki "Kargo Faturası" adlı
    DeductionInvoices kayıtlarının satır detayı (hangi sipariş ne kadar kargo ücreti almış)

ÖNEMLİ KISIT: settlements/otherfinancials tek istekte en fazla 15 günlük aralığa izin
veriyor. Bu yüzden trendyol_client.date_chunks(..., max_days=15) kullanılır.

ÖNEMLİ NOT (Türkçeleştirme sorunu): Trendyol API'si yanıttaki "transactionType"
alanını Türkçeleştirilmiş döndürüyor (isteğe "Sale" gönderseniz bile içerikte
"transactionType": "Satış" dönüyor). Bu yüzden DB'ye BİZİM istediğimiz kanonik
tipi ("_queried_type") yazıyoruz; API'nin orijinal metni "raw_transaction_type"
kolonunda ayrıca saklanıyor (görüntüleme / teşhis / kargo faturası ayıklama için).

ÖNEMLİ NOT (kargo faturası şeması belirsizliği): cargo-invoice/items endpoint'inin
tam yanıt şeması Trendyol dokümantasyonunda örnek JSON ile gösterilmiyor. Bu yüzden
_cargo_item_to_row() olası alan adlarını (shipmentPackageId/orderNumber/barcode/amount
vb.) savunmacı şekilde dener ve HER ZAMAN ham JSON'u da saklar (raw_json kolonu) —
gerçek veride alan adları farklı çıkarsa kolayca düzeltilebilir.

ÖNEMLİ NOT (finansal kayıtların oluşma zamanı): Finansal kayıtlar sipariş TESLİM
EDİLDİKTEN sonra oluşur. Yeni/kargodaki bir sipariş için henüz settlement kaydı
olmayabilir — profit_engine.py bu durumda tahmini komisyona düşer.
"""

import hashlib
import json

import requests
from datetime import datetime, timedelta

from database import (
    clear_cargo_sync_failure,
    init_db,
    record_cargo_sync_failure,
    upsert_cargo_costs,
    upsert_other_financials,
    upsert_settlements,
)
from trendyol_client import SUPPLIER_ID, date_chunks, trendyol_get

# Şu an profit_engine.py'nin gerçekten kullandığı tipler. Diğer tipler
# (Discount, Coupon, Provizyon, WireTransfer, vb.) referans olarak dosya
# sonunda listeleniyor — ileride ayrıntı eklemek isterseniz buraya taşıyın.
#
# 21.08.2026 DÜZELTMESİ: ManualRefund/ManualRefundCancel eklendi. Trendyol'un
# "Cari Hesap Ekstresi" dokümantasyonuna göre KISMİ iadeler "Return" tipiyle
# DEĞİL, ayrı bir "ManualRefund" tipiyle bildiriliyor ("Bir ürün için ürün
# tutarından daha az olacak şekilde iade kaydı oluşturuluyor ise bu kayıt
# atılmaktadır" — resmi açıklama). "ManualRefundCancel" bunun tersidir (kısmi
# iade sonradan iptal/mahsuplaştırılırsa). Bu iki tip önceden hiç
# çekilmiyordu — yani KISMİ Trendyol iadeleri finans motoruna tamamen
# görünmezdi (ne ciro tarafında ne COGS tarafında). Bkz. finance_engine.py
# modül docstring'indeki 21.08.2026 notu.
SETTLEMENT_TRANSACTION_TYPES = ["Sale", "Return", "ManualRefund", "ManualRefundCancel"]
OTHER_FINANCIAL_TRANSACTION_TYPES = ["Stoppage", "CashAdvance", "DeductionInvoices", "PaymentOrder"]

# ⚠️ GEÇİCİ BYPASS (05.08.2026): Bu hesapta Trendyol'un otherfinancials
# endpoint'i transactionType=PaymentOrder için kendi backend'inde bir hata
# veriyor (400: "Cannot deserialize value of type Currency from String 'EUR'"
# — Trendyol tarafı bir enum/serialization hatası, bizim istek formatımızla
# ilgili değil). try/except ile parça bazında atlatma denendi ama hata hâlâ
# senkronu durduruyordu (kök neden henüz netleşmedi — muhtemelen retry/backoff
# katmanında farklı bir istisna tipi olarak yükseliyor). Panelin çalışır
# durumda kalması için PaymentOrder ŞİMDİLİK tamamen çekilen tipler listesinden
# çıkarıldı. ETKİSİ: Hakediş Takvimi'ndeki "confirmed" (gerçek banka ödemesi)
# verisi artık GÜNCELLENMİYOR — Trendyol tarafı için sadece "estimated"
# (settlements.payment_date tahmini) kalemler görünmeye devam edecek, bu daha
# az kesin ama panel çalışmaya devam eder. Diğer tüm veri (siparişler,
# settlements/satış-iade, kargo faturaları, Stoppage/CashAdvance/
# DeductionInvoices) ETKİLENMİYOR. Kök nedeni bulup PaymentOrder'ı geri
# eklemek için bkz. proje notları / bir sonraki oturum.
_PAYMENT_ORDER_TEMPORARILY_DISABLED = True
if _PAYMENT_ORDER_TEMPORARILY_DISABLED:
    OTHER_FINANCIAL_TRANSACTION_TYPES = [
        t for t in OTHER_FINANCIAL_TRANSACTION_TYPES if t != "PaymentOrder"
    ]

# Referans (kullanılmayan diğer tipler):
#   settlements: Discount, DiscountCancel, Coupon, CouponCancel, ProvisionPositive,
#     ProvisionNegative, SellerRevenuePositive,
#     SellerRevenueNegative, CommissionPositive, CommissionNegative,
#     SellerRevenuePositiveCancel, SellerRevenueNegativeCancel,
#     CommissionPositiveCancel, CommissionNegativeCancel
#   otherfinancials: WireTransfer, IncomingTransfer, ReturnInvoice,
#     CommissionAgreementInvoice, FinancialItem


def _fetch_paginated(path, base_params, size=500):
    """Tek bir transactionType/tarih parçası için tüm sayfaları çeker."""
    results = []
    page = 0
    while True:
        params = {**base_params, "page": page, "size": size}
        data = trendyol_get(path, params)
        content = data.get("content") or []
        results.extend(content)
        total_pages = data.get("totalPages") or 1
        page += 1
        if page >= total_pages:
            break
    return results


def _settlement_to_row(s):
    from sync_core import normalize_trendyol_epoch_ms
    return {
        "id": str(s.get("id")),
        "transaction_date": normalize_trendyol_epoch_ms(s.get("transactionDate")),
        "barcode": s.get("barcode"),
        "transaction_type": s.get("_queried_type"),
        "raw_transaction_type": s.get("transactionType"),
        "receipt_id": str(s.get("receiptId")) if s.get("receiptId") is not None else None,
        "description": s.get("description"),
        "debt": s.get("debt"),
        "credit": s.get("credit"),
        "payment_period": s.get("paymentPeriod"),
        "commission_rate": s.get("commissionRate"),
        "commission_amount": s.get("commissionAmount"),
        "seller_revenue": s.get("sellerRevenue"),
        "order_number": s.get("orderNumber"),
        "payment_order_id": s.get("paymentOrderId"),
        "payment_date": normalize_trendyol_epoch_ms(s.get("paymentDate")),
        "shipment_package_id": s.get("shipmentPackageId"),
    }


def _other_financial_to_row(f):
    from sync_core import normalize_trendyol_epoch_ms
    return {
        "id": str(f.get("id")),
        "transaction_date": normalize_trendyol_epoch_ms(f.get("transactionDate")),
        "barcode": f.get("barcode"),
        "transaction_type": f.get("_queried_type"),
        "raw_transaction_type": f.get("transactionType"),
        "transaction_sub_type": f.get("transactionSubType"),
        "receipt_id": str(f.get("receiptId")) if f.get("receiptId") is not None else None,
        "description": f.get("description"),
        "debt": f.get("debt"),
        "credit": f.get("credit"),
        "order_number": f.get("orderNumber"),
        "payment_order_id": f.get("paymentOrderId"),
        "payment_date": normalize_trendyol_epoch_ms(f.get("paymentDate")),
        "shipment_package_id": f.get("shipmentPackageId"),
    }


def fetch_settlements(start_dt, end_dt, transaction_types=None, progress_cb=None):
    """(start_dt, end_dt) aralığındaki settlement kayıtlarını 15 günlük parçalar
    halinde çeker ve her parça geldikçe DB'ye kademeli olarak yazar (bir istek
    ortada hata verirse önceki ilerleme kaybolmasın diye).
    progress_cb(mesaj: str) -> opsiyonel, her adımda çağrılır (dashboard'a ilerleme göstermek için).
    """
    types = transaction_types or SETTLEMENT_TRANSACTION_TYPES
    total = 0
    for chunk_start, chunk_end in date_chunks(start_dt, end_dt, max_days=15):
        start_ms = int(chunk_start.timestamp() * 1000)
        end_ms = int(chunk_end.timestamp() * 1000)
        for t_type in types:
            if progress_cb:
                progress_cb(f"Settlements: {t_type} ({chunk_start:%d.%m.%Y}-{chunk_end:%d.%m.%Y})")
            rows = _fetch_paginated(
                f"/integration/finance/che/sellers/{SUPPLIER_ID}/settlements",
                {"startDate": start_ms, "endDate": end_ms, "transactionType": t_type},
            )
            for r in rows:
                r["_queried_type"] = t_type
            upsert_settlements([_settlement_to_row(r) for r in rows])
            total += len(rows)
    return total


def fetch_other_financials(start_dt, end_dt, transaction_types=None, progress_cb=None):
    """otherfinancials için aynı mantık — kademeli kayıt + ilerleme bildirimi.

    DAYANIKLILIK NOTU (05.08.2026): Bir (tip, tarih aralığı) parçası Trendyol
    tarafında hata verirse (400 dahil) o parça atlanıp devam edilir, TÜM
    senkronizasyon durmaz. (PaymentOrder ayrıca yukarıdaki BYPASS ile listeden
    tamamen çıkarıldı, bkz. OTHER_FINANCIAL_TRANSACTION_TYPES notu.)

    Returns: (total_rows, failures)
    """
    types = transaction_types or OTHER_FINANCIAL_TRANSACTION_TYPES
    total = 0
    failures = []
    for chunk_start, chunk_end in date_chunks(start_dt, end_dt, max_days=15):
        start_ms = int(chunk_start.timestamp() * 1000)
        end_ms = int(chunk_end.timestamp() * 1000)
        for t_type in types:
            if progress_cb:
                progress_cb(f"Diğer finansal kayıtlar: {t_type} ({chunk_start:%d.%m.%Y}-{chunk_end:%d.%m.%Y})")
            try:
                rows = _fetch_paginated(
                    f"/integration/finance/che/sellers/{SUPPLIER_ID}/otherfinancials",
                    {"startDate": start_ms, "endDate": end_ms, "transactionType": t_type},
                )
            except Exception as e:
                # NOT: Daha önce sadece requests.RequestException yakalanıyordu
                # ama PaymentOrder hatası hâlâ dışarı sızıyordu — bu daha geniş
                # 'except Exception' güvenlik ağı, hangi istisna tipi olursa
                # olsun bir parçanın TÜM senkronu durdurmamasını garantiler.
                msg = f"{t_type} ({chunk_start:%d.%m.%Y}-{chunk_end:%d.%m.%Y}): {e}"
                failures.append(msg)
                if progress_cb:
                    progress_cb(f"⚠️ Atlandı (Trendyol hata döndü): {t_type} {chunk_start:%d.%m.%Y}-{chunk_end:%d.%m.%Y}")
                continue
            for r in rows:
                r["_queried_type"] = t_type
            upsert_other_financials([_other_financial_to_row(r) for r in rows])
            total += len(rows)
    return total, failures


def _cargo_field(item, *keys):
    """Kargo kalemi JSON'unda olası alan adlarını sırayla dener (bkz. dosya
    başındaki şema-belirsizliği notu)."""
    for k in keys:
        if k in item and item[k] is not None:
            return item[k]
    return None


def _cargo_item_content_hash(item):
    """Kalemin İÇERİĞİNDEN (tutar dahil) kısa, kararlı bir hash üretir.
    Aynı kalem tekrar senkronize edildiğinde AYNI hash'i üretir (idempotent);
    farklı tutarlı/alanlı iki kalem (örn. gidiş/iade) FARKLI hash alır."""
    payload = json.dumps(
        {
            "amount": _cargo_field(item, "amount", "price", "cargoPrice", "invoiceAmount", "total"),
            "shipmentPackageId": _cargo_field(item, "shipmentPackageId", "packageId"),
            "orderNumber": _cargo_field(item, "orderNumber", "orderNo"),
            "barcode": _cargo_field(item, "barcode"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _cargo_items_to_rows(items, invoice_serial_number):
    """Bir faturanın TÜM kalemlerini DB satırlarına çevirir.

    RC1 DÜZELTMESİ (14.09.2026): Eskiden _cargo_item_to_row() her kalemi TEK
    BAŞINA işliyordu ve parcelUniqueId yoksa fallback id sadece "ilk bulunan"
    barcode/orderNumber/shipmentPackageId'den üretiliyordu. Aynı faturada aynı
    siparişe ait, parcelUniqueId'si OLMAYAN iki farklı kargo hareketi (örn.
    bölünmüş paket, farklı tutar) gelirse ikisi AYNI id'yi alıp biri diğerinin
    ÜZERİNE sessizce yazılıyordu (kargo maliyeti kayboluyordu). Şimdi:
    parcelUniqueId yoksa id'ye kalemin İÇERİK HASH'i + aynı hash'e sahip
    kalemler için bir occurrence-counter eki ekleniyor -- böylece iki farklı
    kalem artık her zaman farklı id alır, aynı kalem tekrar sync edildiğinde
    ise (aynı liste sırasıyla) aynı id'yi üretmeye devam eder (idempotent).
    """
    seen_hash_counts = {}
    rows = []
    for item in items:
        item_id = _cargo_field(item, "id", "invoiceItemId", "itemId")
        if item_id is None:
            parcel_id = _cargo_field(item, "parcelUniqueId")
            if parcel_id is not None:
                item_id = f"{invoice_serial_number}-{parcel_id}"
            else:
                content_hash = _cargo_item_content_hash(item)
                occurrence = seen_hash_counts.get(content_hash, 0)
                seen_hash_counts[content_hash] = occurrence + 1
                base = _cargo_field(item, "barcode", "orderNumber", "shipmentPackageId") or "noref"
                item_id = f"{invoice_serial_number}-{base}-{content_hash}-{occurrence}"

        rows.append({
            "id": str(item_id),
            "invoice_serial_number": str(invoice_serial_number),
            "shipment_package_id": _cargo_field(item, "shipmentPackageId", "packageId"),
            "order_number": _cargo_field(item, "orderNumber", "orderNo"),
            "barcode": _cargo_field(item, "barcode"),
            "amount": _cargo_field(item, "amount", "price", "cargoPrice", "invoiceAmount", "total"),
            "raw_json": json.dumps(item, ensure_ascii=False),
        })
    return rows


def fetch_cargo_invoice_items(invoice_serial_number):
    """Tek bir kargo faturasının satır kalemlerini çeker (tüm sayfalar)."""
    return _fetch_paginated(
        f"/integration/finance/che/sellers/{SUPPLIER_ID}/cargo-invoice/{invoice_serial_number}/items",
        {},
    )


def sync_cargo_costs(progress_cb=None):
    """DB'deki otherfinancials tablosunda description/raw_transaction_type içinde
    "kargo" geçen DeductionInvoices kayıtlarını bulur, her birinin invoiceSerialNumber'ı
    (= kaydın "id"'si) ile kargo faturası kalemlerini çeker ve cargo_costs tablosuna yazar.
    NOT: Bu fonksiyon settlements/otherfinancials'ın DB'de zaten senkronize edilmiş
    olmasını varsayar (önce fetch_other_financials çağrılmalı).

    RC3 DÜZELTMESİ (14.09.2026): Bir fatura no'nun kalemleri çekilemezse (örn.
    Trendyol servisi geçmişe dönük hata veriyorsa) artık sessizce atlanmıyor;
    cargo_sync_failures tablosuna kaydediliyor (record_cargo_sync_failure) ki
    "normal gecikme" ile "kalıcı/anormal hata" ayrılabilsin. Başarılı olursa
    önceki hata kaydı temizleniyor (clear_cargo_sync_failure) — fatura daha
    sonra kendi kendine düzelirse durum de kendiliğinden temizlenir.
    """
    from database import get_connection

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id FROM other_financials
            WHERE transaction_type = 'DeductionInvoices'
              AND (
                    lower(COALESCE(description, '')) LIKE '%kargo%'
                    OR lower(COALESCE(raw_transaction_type, '')) LIKE '%kargo%'
                  )
        """).fetchall()

    invoice_ids = [r["id"] for r in rows]
    total_items = 0
    for i, invoice_id in enumerate(invoice_ids):
        if progress_cb:
            progress_cb(f"Kargo faturası detayı: {i + 1}/{len(invoice_ids)} ({invoice_id})")
        try:
            items = fetch_cargo_invoice_items(invoice_id)
        except Exception as e:
            # Bir fatura no ile ilgili sorun (örn. servis geçmişe dönük çalışmıyor)
            # tüm senkronizasyonu durdurmasın; diğer faturalarla devam et.
            # Ama artık bu durum kayıt altına alınıyor (bkz. docstring).
            record_cargo_sync_failure("Trendyol", str(invoice_id), str(e))
            if progress_cb:
                progress_cb(f"⚠️ Kargo faturası çekilemedi, kaydedildi: {invoice_id} ({e})")
            continue

        cargo_rows = _cargo_items_to_rows(items, invoice_id)
        upsert_cargo_costs(cargo_rows)
        total_items += len(cargo_rows)
        clear_cargo_sync_failure("Trendyol", str(invoice_id))

    return len(invoice_ids), total_items


def sync_finance_data(start_dt, end_dt, progress_cb=None):
    """(start_dt, end_dt) aralığı için settlements + otherfinancials + kargo faturası
    detaylarını çekip DB'ye yazar (kademeli). 'days' yerine artık doğrudan tarih
    aralığı alıyor ki hem "son N gün" hem "tüm zamanlar" aynı fonksiyonla çalışsın.
    Returns: dict — settlement_count, other_financial_count, other_financial_failures,
             cargo_invoice_count, cargo_item_count
    """
    init_db()

    n_settlements = fetch_settlements(start_dt, end_dt, progress_cb=progress_cb)
    n_other, other_failures = fetch_other_financials(start_dt, end_dt, progress_cb=progress_cb)
    n_invoices, n_cargo_items = sync_cargo_costs(progress_cb=progress_cb)

    return {
        "settlement_count": n_settlements,
        "other_financial_count": n_other,
        "other_financial_failures": other_failures,
        "cargo_invoice_count": n_invoices,
        "cargo_item_count": n_cargo_items,
    }


def reconcile_cargo_costs(lookback_days=180, progress_cb=None):
    """RC2 DÜZELTMESİ (14.09.2026): normal sync akışındaki dar pencere
    (fetch_settlements/fetch_other_financials'ın kapsadığı "son N gün"), ölçülen
    31.6 gün ortalama / 39.6 gün gözlemlenen maksimum settlement->kargo faturası
    gecikmesini garanti yakalamıyordu. Bu fonksiyon SADECE 'DeductionInvoices'
    tipini geniş bir pencerede (varsayılan 180 gün, gerekirse daha da geniş
    çağrılabilir) yeniden çeker, sonra sync_cargo_costs()'u tekrar çalıştırır.

    Idempotent: fetch_other_financials zaten upsert kullanıyor, sync_cargo_costs
    de upsert_cargo_costs kullanıyor (RC1 düzeltmesiyle artık collision-safe id
    üretiyor) — bu yüzden tekrar tekrar çağırmak duplicate ÜRETMEZ, sadece
    eksik/gecikmiş kayıtları tamamlar.

    Returns: dict — other_financial_count, other_financial_failures,
             cargo_invoice_count, cargo_item_count (sync_finance_data ile
             aynı anahtar isimleri, kıyaslama kolay olsun diye)
    """
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)

    if progress_cb:
        progress_cb(
            f"Kargo mutabakatı: geniş pencere taranıyor "
            f"({start_dt:%d.%m.%Y} - {end_dt:%d.%m.%Y}, {lookback_days} gün)"
        )

    n_other, other_failures = fetch_other_financials(
        start_dt, end_dt, transaction_types=["DeductionInvoices"], progress_cb=progress_cb
    )
    n_invoices, n_cargo_items = sync_cargo_costs(progress_cb=progress_cb)

    return {
        "other_financial_count": n_other,
        "other_financial_failures": other_failures,
        "cargo_invoice_count": n_invoices,
        "cargo_item_count": n_cargo_items,
    }


if __name__ == "__main__":
    import sys
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=n_days)
    result = sync_finance_data(start_dt, end_dt, progress_cb=print)
    print(result)
