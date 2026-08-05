"""
finance_engine.py
--------------------
TEK, BİRLEŞİK kâr/zarar motoru — Trendyol VE Hepsiburada için.

Bu modül profit_engine.py + hb_profit_engine.py'nin YERİNE geçer. Önceki
mimaride iki ayrı motor vardı ve aynı ekonomik olay (örn. bir HB satışı)
için iki farklı "gelir" tanımı üretiyorlardı — bu dosya o sorunu ortadan
kaldırmak için yazıldı. Detaylı gerekçe için proje sohbet geçmişindeki
27.07.2026 tarihli mimari denetim raporuna bakın.

TEMEL KAVRAM AYRIMI (önceki motorların karıştırdığı nokta):
  Ciro (Gross Revenue)   = Müşterinin ödediği / platformun "satış tutarı"
                            olarak bildirdiği brüt tutar.
                            TY: settlements.credit (transaction_type='Sale')
                            HB: settlements.seller_revenue (raw_transaction_type='Payment')
  Komisyon               = Platformun kestiği pazaryeri komisyonu.
  Hizmet Bedeli           = (Sadece HB'de satır bazında var) Ödeme hizmeti
                            maliyeti yansıması + işlem bedeli.
  Net Hakediş             = Ciro - Komisyon - Hizmet Bedeli
                            (TY: settlements.seller_revenue ile birebir aynı;
                             HB: Payment - Commission - ServiceFee)
  Net Kâr                 = Net Hakediş - Ürün Maliyeti - Kargo - Stopaj
                            - Platform Hizmet Bedeli (dönemsel, TY) - Erken
                            Ödeme Maliyeti - İADE TUTARI

KANIT (28.07.2026 denetiminde gerçek DB verisiyle doğrulandı):
  - TY: 200 örnek "Satış" satırında credit = commission_amount + seller_revenue
    farksız (0 uyuşmazlık). Yani credit = Ciro, seller_revenue = Net Hakediş.
  - HB: Aynı shipment_package_id için Payment / Commission oranı paketler
    arası tutarlı (~%12) ve Commission/ServiceFee satırlarında da barcode
    doluyor — yani HB tarafında da satır (barkod) bazında Ciro/Komisyon/
    Hizmet Bedeli eşleştirmesi TY ile birebir aynı desende yapılabiliyor.

ÖNCEKİ MOTORLARA GÖRE DÜZELTİLEN HATALAR:
  1) İade (Return) artık net_profit hesabına dahil (önceden sadece
     TY/'all' tarafında raporlanıyor ama düşülmüyordu).
  2) "Ciro" ile "Net Hakediş" ayrı alanlar; ikisi de raporlanıyor, biri
     diğerinin yerine geçmiyor.
  3) Kargo maliyeti artık İŞARETLİ toplanıyor (SUM(ABS(amount)) yerine),
     böylece ileride HB'de bir "gelir/tazminat" tipli kargo kaydı çıkarsa
     yanlışlıkla maliyete eklenmek yerine doğru şekilde netleşir.
  4) Stopaj/Platform Hizmet Bedeli/Erken Ödeme Maliyeti artık debt-credit
     netlemesiyle hesaplanıyor (önceden sadece debt kullanılıyordu).
  5) marketplace='all' artık marketplace='trendyol' + marketplace='hepsiburada'
     ile SATIR SATIR aynı formülden üretiliyor — iki farklı motor yok.

BİLİNÇLİ OLARAK KORUNAN (dürüst) TAHMİNLER:
  - Kargo maliyeti bir sipariş içindeki satırlara EŞİT bölüştürülüyor
    (satır bazında ayrım pazaryeri API'lerinden gelmiyor).
  - order_lines'da karşılığı olmayan (settlement-only) satırlarda
    quantity=1 varsayılıyor ve "quantityEstimated": true ile işaretleniyor
    (bkz. dosya sonundaki BACKFILL NOTU).
"""

from collections import defaultdict
from datetime import datetime, timedelta

from database import get_connection, get_fixed_expenses_by_month

# ============================================================
# KATEGORİ EŞLEMESİ (marketplace-farkında, raw_transaction_type -> kategori)
# ============================================================
# TY tarafında Türkçeleştirilmiş orijinal metin raw_transaction_type'ta
# saklanıyor (bkz. trendyol_finance.py); DB'de gözlemlenen gerçek değerler
# "Satış" / "İade". Kanonik "Sale"/"Return" değerleri de (geriye dönük
# uyumluluk / olası İngilizce yanıtlar için) kabul ediliyor.
_TY_SALE_RAW = {"Satış", "Sale"}
_TY_RETURN_RAW = {"İade", "Return"}

_HB_SALE_RAW = {"Payment"}
_HB_COMMISSION_RAW = {
    "Commission", "CommissionRefund", "CommissionCorrection", "CommissionInvoiceRefund",
}
_HB_SERVICE_FEE_RAW = {
    "PaymentServiceCostReflection", "PaymentServiceCostReflectionRefund", "ProcessingFeeExpense",
}
_HB_RETURN_RAW = {"Return"}

# Hiç sevk edilmemiş / oluşmamış paketler kâr hesabına dahil edilmez.
# (bkz. eski profit_engine.py'deki aynı gerekçe — ürün depodan çıkmadıysa
# ne gerçek maliyet ne gerçek gelir oluşur.)
NEVER_FULFILLED_STATUSES = ("Cancelled", "İptal Edildi", "UnSupplied", "UnDelivered", "UnPacked")


def _categorize(marketplace, raw_type):
    """(marketplace, raw_transaction_type) -> 'sale' | 'commission' | 'service_fee' | 'return' | 'other'"""
    if marketplace == "hepsiburada":
        if raw_type in _HB_SALE_RAW:
            return "sale"
        if raw_type in _HB_COMMISSION_RAW:
            return "commission"
        if raw_type in _HB_SERVICE_FEE_RAW:
            return "service_fee"
        if raw_type in _HB_RETURN_RAW:
            return "return"
        return "other"
    # trendyol (ve tanımadığımız her marketplace için TY varsayımı — TY hâlâ
    # en yaygın/varsayılan entegrasyon olduğundan)
    if raw_type in _TY_SALE_RAW:
        return "sale"
    if raw_type in _TY_RETURN_RAW:
        return "return"
    return "other"


def _resolve_range(days=None, start_dt=None, end_dt=None):
    if start_dt is not None and end_dt is not None:
        pass
    else:
        days = days or 30
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)


# ============================================================
# VERİ YÜKLEME
# ============================================================

def _load_lines(conn, start_ms, end_ms):
    placeholders = ",".join("?" for _ in NEVER_FULFILLED_STATUSES)
    return conn.execute(f"""
        SELECT ol.shipment_package_id, ol.marketplace, ol.barcode, ol.merchant_sku, ol.product_name,
               ol.quantity, ol.line_unit_price, ol.commission_rate,
               o.order_date, o.order_number, o.status
        FROM order_lines ol
        JOIN orders o ON o.shipment_package_id = ol.shipment_package_id
                      AND o.marketplace = ol.marketplace
        WHERE o.order_date BETWEEN ? AND ?
          AND (o.status IS NULL OR o.status NOT IN ({placeholders}))
    """, (start_ms, end_ms, *NEVER_FULFILLED_STATUSES)).fetchall()


def _load_settlement_lines(conn, start_ms=None, end_ms=None):
    """(marketplace, shipment_package_id, barcode) -> kategori bazlı toplamlar.

    start_ms/end_ms verilmezse TÜM GEÇMİŞ okunur (bilinen order_lines'ı
    eşleştirmek için kasıtlı olarak tarih sınırsız — bkz. eski
    profit_engine.py'deki aynı gerekçe: hakediş, sipariş tarihinden
    haftalar sonra oluşabiliyor).
    """
    where = "1=1"
    params = []
    if start_ms is not None and end_ms is not None:
        where = "transaction_date BETWEEN ? AND ?"
        params = [start_ms, end_ms]

    rows = conn.execute(f"""
        SELECT marketplace, shipment_package_id, barcode, raw_transaction_type,
               seller_revenue, debt, credit, commission_amount, transaction_date, order_number
        FROM settlements
        WHERE {where}
    """, params).fetchall()

    totals = defaultdict(lambda: {
        "gross_revenue": 0.0, "commission": 0.0, "service_fee": 0.0,
        "return_amount": 0.0, "order_number": None, "min_date": None,
    })
    for r in rows:
        spid = r["shipment_package_id"]
        if spid is None:
            continue
        mp = r["marketplace"]
        key = (mp, spid, r["barcode"])
        cat = _categorize(mp, r["raw_transaction_type"])
        t = totals[key]

        if cat == "sale":
            if mp == "hepsiburada":
                t["gross_revenue"] += r["seller_revenue"] or 0.0
            else:
                # TY: credit = Ciro (dogrulandi), commission_amount ayrica dusuluyor
                t["gross_revenue"] += r["credit"] or 0.0
                t["commission"] += r["commission_amount"] or 0.0
        elif cat == "commission":
            # Sadece HB burada dusuyor (TY komisyonu 'sale' satirinin icinde)
            t["commission"] += abs(r["seller_revenue"] or 0.0)
        elif cat == "service_fee":
            t["service_fee"] += abs(r["seller_revenue"] or 0.0)
        elif cat == "return":
            t["return_amount"] += (r["debt"] or 0.0) - (r["credit"] or 0.0)

        if r["order_number"] and not t["order_number"]:
            t["order_number"] = r["order_number"]
        d = r["transaction_date"]
        if d:
            t["min_date"] = d if t["min_date"] is None else min(t["min_date"], d)

    return totals


def _load_barcode_sku_map(conn):
    rows = conn.execute("""
        SELECT marketplace, barcode, merchant_sku, product_name
        FROM order_lines
        WHERE barcode IS NOT NULL AND merchant_sku IS NOT NULL
        GROUP BY marketplace, barcode
        ORDER BY id DESC
    """).fetchall()
    mapping = {}
    for r in rows:
        key = (r["marketplace"], r["barcode"])
        if key not in mapping:
            mapping[key] = {"merchant_sku": r["merchant_sku"], "product_name": r["product_name"]}
    return mapping


def _build_settlement_only_lines(settlement_totals_in_range, known_keys, barcode_sku_map):
    """order_lines'da karşılığı olmayan settlement kayıtları için sentetik satır.

    quantity=1 VARSAYIMDIR, gerçek değer değil (bkz. dosya başı BACKFILL NOTU).
    """
    synthetic = []
    for key, t in settlement_totals_in_range.items():
        if key in known_keys:
            continue
        marketplace, spid, barcode = key
        if t["gross_revenue"] == 0 and t["commission"] == 0 and t["service_fee"] == 0:
            # Bu satirda sadece Return/Other var, "satis" satiri yok -> senkron
            # sirasinda ayrica ele alinacak, burada sentetik satis satiri
            # uretmenin anlami yok (yanlislikla 0 gelirli bir satir eklenir).
            continue
        sku_info = barcode_sku_map.get((marketplace, barcode), {})
        synthetic.append({
            "shipment_package_id": spid,
            "marketplace": marketplace,
            "barcode": barcode,
            "merchant_sku": sku_info.get("merchant_sku"),
            "product_name": sku_info.get("product_name") or f"Bilinmeyen ürün (barkod: {barcode})",
            "quantity": 1,
            "line_unit_price": None,
            "commission_rate": None,
            "order_date": t["min_date"],
            "order_number": t["order_number"],
            "status": None,
            "_fromSettlementOnly": True,
            "_quantityEstimated": True,
        })
    return synthetic


def _load_return_totals(conn, start_ms, end_ms):
    """marketplace -> iade tutarı (debt-credit netlenmiş), rapor aralığında."""
    rows = conn.execute("""
        SELECT marketplace, raw_transaction_type, COALESCE(SUM(debt), 0) AS debt_sum,
               COALESCE(SUM(credit), 0) AS credit_sum, COUNT(*) AS cnt
        FROM settlements
        WHERE transaction_date BETWEEN ? AND ?
        GROUP BY marketplace, raw_transaction_type
    """, (start_ms, end_ms)).fetchall()
    totals = defaultdict(lambda: {"total": 0.0, "count": 0})
    for r in rows:
        if _categorize(r["marketplace"], r["raw_transaction_type"]) == "return":
            totals[r["marketplace"]]["total"] += (r["debt_sum"] or 0) - (r["credit_sum"] or 0)
            totals[r["marketplace"]]["count"] += r["cnt"]
    return totals


def _load_other_financial_totals(conn, start_ms, end_ms):
    """marketplace -> {transaction_type: net_tutar (debt-credit)}.
    ESKİ MOTORDAN FARK: artık sadece 'debt' değil, debt-credit netlemesi
    yapılıyor (bir kesinti sonradan iptal/düzeltilirse doğru yansısın diye).
    """
    rows = conn.execute("""
        SELECT marketplace, transaction_type,
               COALESCE(SUM(debt), 0) - COALESCE(SUM(credit), 0) AS net_total,
               COALESCE(SUM(debt), 0) AS debt_total, COALESCE(SUM(credit), 0) AS credit_total
        FROM other_financials
        WHERE transaction_date BETWEEN ? AND ?
        GROUP BY marketplace, transaction_type
    """, (start_ms, end_ms)).fetchall()
    totals = defaultdict(dict)
    for r in rows:
        totals[r["marketplace"]][r["transaction_type"]] = {
            "net": r["net_total"], "debt": r["debt_total"], "credit": r["credit_total"],
        }

    # Kargo faturasi kalemlerini DeductionInvoices icinden ayirip cikariyoruz
    # (cargo_costs tablosundan satir bazinda zaten dusuluyor, cift saymamak icin).
    kargo_rows = conn.execute("""
        SELECT marketplace, COALESCE(SUM(debt), 0) - COALESCE(SUM(credit), 0) AS net_total
        FROM other_financials
        WHERE transaction_type = 'DeductionInvoices' AND transaction_date BETWEEN ? AND ?
          AND (lower(COALESCE(description, '')) LIKE '%kargo%'
               OR lower(COALESCE(raw_transaction_type, '')) LIKE '%kargo%')
        GROUP BY marketplace
    """, (start_ms, end_ms)).fetchall()
    for r in kargo_rows:
        totals[r["marketplace"]]["_kargo_within_deduction_invoices"] = {"net": r["net_total"]}
    return totals


def _load_costs(conn):
    rows = conn.execute("""
        SELECT sku, cost_incl_vat, cost_excl_vat, sale_price_incl_vat, sale_price_excl_vat, product_name
        FROM product_costs
    """).fetchall()
    return {r["sku"]: r for r in rows}


def _load_cargo_by_order(conn):
    """(marketplace, shipment_package_id) -> NET kargo maliyeti.

    ESKİ MOTORDAN FARK (kritik düzeltme): SUM(ABS(amount)) yerine artık
    marketplace'e özgü işaret kuralı uygulanıyor:
      - trendyol: cargo_costs.amount HER ZAMAN pozitif bir GİDERDİR
                  (kargo faturası kalemi) -> maliyet = SUM(amount)
      - hepsiburada: amount İŞARETLİDİR (negatif=gider, pozitif=gelir/tazminat,
                  bkz. app.py sync_hb_finance_data dokümantasyonu)
                  -> maliyet = -SUM(amount)
    Bu ayrım olmadan, ileride bir "kargo tazminatı" (income) kaydı gelirse
    eski motor bunu da maliyete EKLERdi (ABS aldığı için); bu motor doğru
    şekilde maliyetten DÜŞER.
    NOT (kanıt): 28.07.2026 itibarıyla mevcut DB'de HB cargo_costs'ta 333
    satırın tamamı negatif, hiç pozitif (gelir) kaydı yok — yani bu düzeltme
    şu anki toplamları DEĞİŞTİRMEZ, sadece gelecekteki bir income kaydına
    karşı koruma sağlar.
    """
    rows = conn.execute("""
        SELECT marketplace, shipment_package_id, order_number, SUM(amount) AS net_amount
        FROM cargo_costs
        WHERE amount IS NOT NULL
        GROUP BY marketplace, shipment_package_id, order_number
    """).fetchall()
    by_spid, by_order_number = {}, {}
    for r in rows:
        mp = r["marketplace"]
        net = r["net_amount"] or 0
        cost = net if mp == "trendyol" else -net
        if r["shipment_package_id"] is not None:
            key = (mp, r["shipment_package_id"])
            by_spid[key] = by_spid.get(key, 0) + cost
        elif r["order_number"]:
            key = (mp, r["order_number"])
            by_order_number[key] = by_order_number.get(key, 0) + cost
    return by_spid, by_order_number


def _sku_vat_rate(cost_row, side="cost"):
    if side == "cost":
        incl, excl = cost_row["cost_incl_vat"], cost_row["cost_excl_vat"]
    else:
        incl, excl = cost_row["sale_price_incl_vat"], cost_row["sale_price_excl_vat"]
    if incl is None or excl is None or excl == 0:
        return None
    return (incl - excl) / excl


# ============================================================
# ANA HESAPLAMA
# ============================================================

def _gather_summary_inputs(start_ms, end_ms, marketplace_filter):
    """DB'den bir kâr/zarar özeti için gereken TÜM ham veriyi tek bağlantı
    içinde okur ve (satırlar + yardımcı haritalar) olarak döner.

    ÖNEMLİ: settlement_totals_all TARİH SINIRSIZ okunur (bilinen order_lines'ı
    eşleştirmek için) — bkz. _load_settlement_lines docstring'i.
    """
    with get_connection() as conn:
        lines = list(_load_lines(conn, start_ms, end_ms))
        if marketplace_filter:
            lines = [ln for ln in lines if ln["marketplace"] == marketplace_filter]

        settlement_totals_all = _load_settlement_lines(conn)
        settlement_totals_in_range = _load_settlement_lines(conn, start_ms, end_ms)
        return_totals = _load_return_totals(conn, start_ms, end_ms)
        other_totals = _load_other_financial_totals(conn, start_ms, end_ms)
        costs = _load_costs(conn)
        cargo_by_spid, cargo_by_order_number = _load_cargo_by_order(conn)
        barcode_sku_map = _load_barcode_sku_map(conn)

    known_keys = {(ln["marketplace"], ln["shipment_package_id"], ln["barcode"]) for ln in lines}
    settlement_only_lines = _build_settlement_only_lines(settlement_totals_in_range, known_keys, barcode_sku_map)
    if marketplace_filter:
        settlement_only_lines = [ln for ln in settlement_only_lines if ln["marketplace"] == marketplace_filter]
    lines.extend(settlement_only_lines)

    lines_per_order = defaultdict(int)
    for ln in lines:
        group_key = (ln["marketplace"], ln["shipment_package_id"] if ln["shipment_package_id"] is not None
                     else f"order:{ln['order_number']}")
        lines_per_order[group_key] += 1

    return {
        "lines": lines,
        "settlement_totals_all": settlement_totals_all,
        "return_totals": return_totals,
        "other_totals": other_totals,
        "costs": costs,
        "cargo_by_spid": cargo_by_spid,
        "cargo_by_order_number": cargo_by_order_number,
        "lines_per_order": lines_per_order,
    }


def _build_line_result(ln, settlement_totals_all, costs, cargo_by_spid, cargo_by_order_number, lines_per_order):
    """Tek bir sipariş satırı için Ciro/Komisyon/KDV/Kâr hesabını yapar.
    Döner: (line_result dict, estimated: bool, missing_cost_sku: str|None,
            cargo_missing_order_number: str|None)
    """
    key = (ln["marketplace"], ln["shipment_package_id"], ln["barcode"])
    settlement = settlement_totals_all.get(key)

    if settlement and (settlement["gross_revenue"] or settlement["commission"] or settlement["service_fee"]):
        gross_revenue = settlement["gross_revenue"]
        commission = settlement["commission"]
        service_fee = settlement["service_fee"]
        net_hakedis = gross_revenue - commission - service_fee
        estimated = False
    else:
        rate = (ln["commission_rate"] or 0) / 100
        gross_revenue = (ln["line_unit_price"] or 0) * (ln["quantity"] or 0)
        commission = gross_revenue * rate
        service_fee = 0.0
        net_hakedis = gross_revenue - commission
        estimated = True

    # DÜZELTME: order_lines'da hiç karşılığı olmayan (settlement-only) satırlarda
    # merchant_sku hiçbir zaman bilinemiyor (barcode_sku_map'te de yoksa "Bilinmeyen
    # ürün" olarak kalıyor) — bu satırlara SKU üzerinden asla maliyet girilemiyordu,
    # kullanıcı arayüzden "Kaydet"e bassa bile sku boş gittiği için istek reddediliyordu.
    # Bu satırlarda barkodu SKU YERİNE GEÇEN bir anahtar olarak kullanıyoruz: hem
    # maliyet eşleştirmesi hem de kullanıcıya gösterilen/kaydedilen "sku" değeri
    # artık aynı (product_costs.sku = barkod). Gerçek merchant_sku'su bilinen
    # satırlarda davranış DEĞİŞMEDİ.
    effective_sku = ln["merchant_sku"] or ln["barcode"]

    cost_row = costs.get(effective_sku)
    missing_cost_sku = None
    if cost_row and cost_row["cost_incl_vat"] is not None:
        cogs = cost_row["cost_incl_vat"] * (ln["quantity"] or 0)
        missing_cost = False
    else:
        cogs = None
        missing_cost = True
        if effective_sku:
            missing_cost_sku = effective_sku

    spid = ln["shipment_package_id"]
    cargo_total_for_order = cargo_by_spid.get((ln["marketplace"], spid))
    if cargo_total_for_order is None:
        cargo_total_for_order = cargo_by_order_number.get((ln["marketplace"], ln["order_number"]))
    cargo_missing_order = None
    if cargo_total_for_order is None:
        cargo_line = 0.0
        cargo_missing = True
        cargo_missing_order = ln["order_number"]
    else:
        group_key = (ln["marketplace"], spid if spid is not None else f"order:{ln['order_number']}")
        n = max(lines_per_order.get(group_key, 1), 1)
        cargo_line = cargo_total_for_order / n
        cargo_missing = False

    vat_missing = cost_row is None
    gross_revenue_excl_vat = None
    cogs_excl_vat = None
    vat_on_sale = None
    vat_on_cost = None
    if cost_row is not None:
        sale_vat_rate = _sku_vat_rate(cost_row, "sale")
        cost_vat_rate = _sku_vat_rate(cost_row, "cost")
        if sale_vat_rate is not None:
            gross_revenue_excl_vat = gross_revenue / (1 + sale_vat_rate)
            vat_on_sale = gross_revenue - gross_revenue_excl_vat
        else:
            vat_missing = True
        if cost_vat_rate is not None and cogs is not None:
            cogs_excl_vat = cogs / (1 + cost_vat_rate)
            vat_on_cost = cogs - cogs_excl_vat
        elif cogs is not None:
            vat_missing = True

    profit = (net_hakedis - cogs - cargo_line) if cogs is not None else None
    profit_excl_vat = None
    if cogs is not None and gross_revenue_excl_vat is not None and cogs_excl_vat is not None:
        net_hakedis_excl_vat = gross_revenue_excl_vat - (commission + service_fee) / (1 + (_sku_vat_rate(cost_row, "sale") or 0))
        profit_excl_vat = net_hakedis_excl_vat - cogs_excl_vat - cargo_line

    line_result = {
        "orderNumber": ln["order_number"],
        "orderDate": ln["order_date"],
        "marketplace": ln["marketplace"],
        "sku": effective_sku,
        "barcode": ln["barcode"],
        "productName": ln["product_name"],
        "quantity": ln["quantity"],
        "grossRevenue": round(gross_revenue, 2) if gross_revenue is not None else None,
        "revenueExclVat": round(gross_revenue_excl_vat, 2) if gross_revenue_excl_vat is not None else None,
        "commission": round(commission, 2) if commission is not None else None,
        "serviceFee": round(service_fee, 2) if service_fee is not None else None,
        "netHakedis": round(net_hakedis, 2) if net_hakedis is not None else None,
        "cogs": round(cogs, 2) if cogs is not None else None,
        "cogsExclVat": round(cogs_excl_vat, 2) if cogs_excl_vat is not None else None,
        "cargo": round(cargo_line, 2),
        "vatOnSale": round(vat_on_sale, 2) if vat_on_sale is not None else None,
        "vatOnCost": round(vat_on_cost, 2) if vat_on_cost is not None else None,
        "profit": round(profit, 2) if profit is not None else None,
        "profitExclVat": round(profit_excl_vat, 2) if profit_excl_vat is not None else None,
        "estimated": estimated,
        "missingCost": missing_cost,
        "cargoMissing": cargo_missing,
        "vatMissing": vat_missing,
        "fromSettlementOnly": isinstance(ln, dict) and ln.get("_fromSettlementOnly", False),
        "quantityEstimated": isinstance(ln, dict) and ln.get("_quantityEstimated", False),
    }
    return line_result, estimated, missing_cost_sku, cargo_missing_order


def _compute_overhead_by_marketplace(other_totals, return_totals, marketplaces_present):
    """Pazaryeri başına stopaj/platform hizmet bedeli/erken ödeme maliyeti/iade
    toplamlarını ve bunların TOPLAMINI (overhead) hesaplar. return_amount artık
    overhead'e (ve dolayısıyla net_profit'e) dahil — eski motorda bu düşülmüyordu."""
    overhead_by_mp = {}
    payment_order_by_mp = {}
    for mp in marketplaces_present:
        ot = other_totals.get(mp, {})
        stoppage = ot.get("Stoppage", {}).get("net", 0)
        platform_fee_raw = ot.get("DeductionInvoices", {}).get("net", 0)
        kargo_within = ot.get("_kargo_within_deduction_invoices", {}).get("net", 0)
        platform_fee = platform_fee_raw - kargo_within
        cash_advance = ot.get("CashAdvance", {}).get("net", 0)
        return_amount = return_totals.get(mp, {}).get("total", 0)
        overhead_by_mp[mp] = {
            "stoppage": stoppage,
            "platform_fee": platform_fee,
            "cash_advance": cash_advance,
            "return_amount": return_amount,
            "total": stoppage + platform_fee + cash_advance + return_amount,
            "has_data": mp in other_totals or mp in return_totals,
        }
        payment_order_by_mp[mp] = ot.get("PaymentOrder", {}).get("debt", 0) - ot.get("PaymentOrder", {}).get("credit", 0)
    return overhead_by_mp, payment_order_by_mp


def _aggregate_by_marketplace(lines, line_results, overhead_by_mp, return_totals, payment_order_by_mp):
    """Satır sonuçlarını pazaryeri bazında toplayıp panelin 'by_marketplace'
    bölümü için özetler."""
    mp_stats = defaultdict(lambda: {
        "gross_revenue": 0.0, "net_hakedis": 0.0, "gross_profit": 0.0, "cargo_total": 0.0,
        "item_count": 0, "line_count": 0,
        "lines_with_real_settlement": 0, "lines_estimated": 0,
        "order_spids": set(),
    })
    for ln, r in zip(lines, line_results):
        s = mp_stats[r["marketplace"]]
        s["gross_revenue"] += r["grossRevenue"] or 0
        s["net_hakedis"] += r["netHakedis"] or 0
        if r["profit"] is not None:
            s["gross_profit"] += r["profit"]
        s["cargo_total"] += r["cargo"] or 0
        s["item_count"] += ln["quantity"] or 0
        s["line_count"] += 1
        s["order_spids"].add(ln["shipment_package_id"] if ln["shipment_package_id"] is not None
                              else f"order:{ln['order_number']}")
        if r["estimated"]:
            s["lines_estimated"] += 1
        else:
            s["lines_with_real_settlement"] += 1

    by_marketplace = {}
    for mp, s in mp_stats.items():
        mp_overhead = overhead_by_mp.get(mp, {"total": 0.0, "has_data": False, "return_amount": 0.0,
                                               "stoppage": 0.0, "platform_fee": 0.0, "cash_advance": 0.0})
        by_marketplace[mp] = {
            "grossRevenue": round(s["gross_revenue"], 2),
            "netHakedis": round(s["net_hakedis"], 2),
            "grossProfit": round(s["gross_profit"], 2),
            "cargoTotal": round(s["cargo_total"], 2),
            "stoppage": round(mp_overhead.get("stoppage", 0), 2),
            "platformServiceFee": round(mp_overhead.get("platform_fee", 0), 2),
            "cashAdvanceCost": round(mp_overhead.get("cash_advance", 0), 2),
            "returnAmount": round(mp_overhead.get("return_amount", 0), 2),
            "overheadTotal": round(mp_overhead["total"], 2),
            "netProfit": round(s["gross_profit"] - mp_overhead["total"], 2),
            "overheadApplied": mp_overhead["has_data"],
            "returnCount": return_totals.get(mp, {}).get("count", 0),
            "paymentOrderNet": round(payment_order_by_mp.get(mp, 0), 2),
            "orderCount": len(s["order_spids"]),
            "itemCount": s["item_count"],
            "lineCount": s["line_count"],
            "linesWithRealSettlement": s["lines_with_real_settlement"],
            "linesEstimatedPendingSettlement": s["lines_estimated"],
        }
    return by_marketplace


def compute_profit_summary(days=None, start_dt=None, end_dt=None, marketplace_filter=None):
    """marketplace_filter: None -> tüm pazaryerleri (=eski 'all'),
    'trendyol' veya 'hepsiburada' -> sadece o pazaryeri.

    ÖNEMLİ: 'all' artık ayrı bir motor DEĞİL — sadece filtre uygulanmamış
    hali. marketplace='trendyol' ile marketplace='all'in trendyol'a ait
    kısmı HER ZAMAN birebir aynı sayıyı üretir (aynı koddan geçiyor).

    Bu fonksiyon dört alt adıma bölünmüştür (okunabilirlik/test edilebilirlik
    için, bkz. _gather_summary_inputs / _build_line_result /
    _compute_overhead_by_marketplace / _aggregate_by_marketplace):
      1) Ham veriyi DB'den yükle
      2) Her satır için Ciro/Komisyon/KDV/Kâr hesapla
      3) Pazaryeri başına dönemsel giderleri (stopaj, iade, vb.) topla
      4) Satır sonuçlarını pazaryeri bazında özetle
    """
    start_ms, end_ms = _resolve_range(days, start_dt, end_dt)
    data = _gather_summary_inputs(start_ms, end_ms, marketplace_filter)
    lines = data["lines"]

    line_results = []
    missing_cost_skus = set()
    estimated_count = 0
    real_count = 0
    cargo_missing_orders = set()

    for ln in lines:
        line_result, estimated, missing_cost_sku, cargo_missing_order = _build_line_result(
            ln, data["settlement_totals_all"], data["costs"],
            data["cargo_by_spid"], data["cargo_by_order_number"], data["lines_per_order"],
        )
        line_results.append(line_result)
        if estimated:
            estimated_count += 1
        else:
            real_count += 1
        if missing_cost_sku:
            missing_cost_skus.add(missing_cost_sku)
        if line_result["cargoMissing"]:
            cargo_missing_orders.add(ln["order_number"])

    gross_profit = sum(r["profit"] for r in line_results if r["profit"] is not None)  # bkz. not (asagida overhead sonrasi net_profit'e girer)
    gross_profit_excl_vat = sum(r["profitExclVat"] for r in line_results if r["profitExclVat"] is not None)
    total_gross_revenue = sum(r["grossRevenue"] for r in line_results if r["grossRevenue"] is not None)
    total_net_hakedis = sum(r["netHakedis"] for r in line_results if r["netHakedis"] is not None)
    total_commission = sum(r["commission"] for r in line_results if r["commission"] is not None)
    total_service_fee = sum(r["serviceFee"] for r in line_results if r["serviceFee"] is not None)
    total_cargo = sum(r["cargo"] for r in line_results)
    total_vat_on_sale = sum(r["vatOnSale"] for r in line_results if r["vatOnSale"] is not None)
    total_vat_on_cost = sum(r["vatOnCost"] for r in line_results if r["vatOnCost"] is not None)
    vat_payable = total_vat_on_sale - total_vat_on_cost

    marketplaces_present = (set(ln["marketplace"] for ln in lines)
                             | set(data["other_totals"].keys()) | set(data["return_totals"].keys()))
    if marketplace_filter:
        marketplaces_present &= {marketplace_filter}

    overhead_by_mp, payment_order_by_mp = _compute_overhead_by_marketplace(
        data["other_totals"], data["return_totals"], marketplaces_present)

    stoppage_total = sum(v["stoppage"] for v in overhead_by_mp.values())
    platform_fee_total = sum(v["platform_fee"] for v in overhead_by_mp.values())
    cash_advance_total = sum(v["cash_advance"] for v in overhead_by_mp.values())
    payment_order_net = sum(payment_order_by_mp.values())
    return_total = sum(v["return_amount"] for v in overhead_by_mp.values())
    return_count = sum(data["return_totals"].get(mp, {}).get("count", 0) for mp in marketplaces_present)

    # overhead_total artık İADE TUTARINI DA içeriyor (kritik düzeltme — eski
    # motorda return_total hiç düşülmüyordu, sadece raporlanıyordu).
    overhead_total = stoppage_total + platform_fee_total + cash_advance_total + return_total
    net_profit = gross_profit - overhead_total
    net_profit_after_vat = net_profit - vat_payable

    by_marketplace = _aggregate_by_marketplace(
        lines, line_results, overhead_by_mp, data["return_totals"], payment_order_by_mp)

    return {
        "totals": {
            "grossRevenue": round(total_gross_revenue, 2),
            "netHakedis": round(total_net_hakedis, 2),
            "commission": round(total_commission, 2),
            "serviceFee": round(total_service_fee, 2),
            "grossProfit": round(gross_profit, 2),
            "grossProfitExclVat": round(gross_profit_excl_vat, 2),
            "cargoTotal": round(total_cargo, 2),
            "stoppage": round(stoppage_total, 2),
            "platformServiceFee": round(platform_fee_total, 2),
            "cashAdvanceCost": round(cash_advance_total, 2),
            "returnAmount": round(return_total, 2),
            "returnCount": return_count,
            "overheadTotal": round(overhead_total, 2),
            "netProfit": round(net_profit, 2),
            "vatOnSales": round(total_vat_on_sale, 2),
            "vatOnPurchases": round(total_vat_on_cost, 2),
            "vatPayableEstimate": round(vat_payable, 2),
            "netProfitAfterVatEstimate": round(net_profit_after_vat, 2),
            "paymentOrderNet": round(payment_order_net, 2),
        },
        "data_quality": {
            "lines_with_real_settlement": real_count,
            "lines_estimated_pending_settlement": estimated_count,
            "skus_missing_cost": sorted(missing_cost_skus),
            "orders_missing_cargo_invoice": len([o for o in cargo_missing_orders if o]),
        },
        "by_marketplace": by_marketplace,
        "lines": line_results,
    }


def monthly_profit(start_dt, end_dt, marketplace_filter=None):
    """Tek geçişte hesaplar (ESKİ MOTORDAN FARK: artık her ay için ayrı
    ayrı tüm settlements tablosunu taramıyor — önce TÜM aralık için satır
    sonuçlarını üretir, sonra Python tarafında aya göre gruplar).
    """
    summary = compute_profit_summary(start_dt=start_dt, end_dt=end_dt, marketplace_filter=marketplace_filter)

    by_month = defaultdict(lambda: {"grossRevenue": 0.0, "netHakedis": 0.0, "grossProfit": 0.0})
    for ln in summary["lines"]:
        d = ln["orderDate"]
        if not d:
            continue
        month_key = datetime.fromtimestamp(d / 1000).strftime("%Y-%m")
        m = by_month[month_key]
        m["grossRevenue"] += ln["grossRevenue"] or 0
        m["netHakedis"] += ln["netHakedis"] or 0
        if ln["profit"] is not None:
            m["grossProfit"] += ln["profit"]

    # NOT: Bu basitleştirilmiş aylık kırılım, dönemsel giderleri (stopaj,
    # platform hizmet bedeli, iade) SATIR TARİHİNE göre değil sipariş
    # tarihine göre aya dağıtmaz (bkz. TODO altında ayrıntı) — aylık
    # netProfit yaklaşık olarak grossProfit'e eşit tutulur; tam dönemsel
    # gider dağılımı için ay bazlı ayrı sorgu gerekir (bkz. finance_engine
    # TODO notu). Bu, eski motordaki performans sorununu çözer ama
  # dönemsel giderlerin aya dağılımı basitleştirilmiştir; net rakam
    # her zaman TOPLAM aralık için compute_profit_summary()'den alınmalı.
    # Aylık sabit giderler (kira, personel, vb.) — ay bazlı olduğu için burada
    # herhangi bir orantılama/tahmin gerekmiyor, doğrudan o aya girilen kalemlerin
    # toplamı düşülüyor. "Gerçek Net Kâr" = Net Kâr (brüt kâr, dönemsel giderler
    # zaten compute_profit_summary'de düşülmüş haliyle) - o ayın sabit giderleri.
    first_month = start_dt.strftime("%Y-%m")
    last_month = end_dt.strftime("%Y-%m")
    fixed_expenses_by_month = get_fixed_expenses_by_month(first_month, last_month)

    months = []
    cursor = datetime(start_dt.year, start_dt.month, 1)
    while cursor <= end_dt:
        key = cursor.strftime("%Y-%m")
        m = by_month.get(key, {"grossRevenue": 0.0, "netHakedis": 0.0, "grossProfit": 0.0})
        net_profit = round(m["grossProfit"], 2)  # bkz. yukarıdaki not
        fixed_expenses = round(fixed_expenses_by_month.get(key, 0.0), 2)
        months.append({
            "month": key,
            "revenue": round(m["grossRevenue"], 2),
            "grossProfit": round(m["grossProfit"], 2),
            "netProfit": net_profit,
            "fixedExpenses": fixed_expenses,
            "realNetProfit": round(net_profit - fixed_expenses, 2),
        })
        cursor = datetime(cursor.year + 1, 1, 1) if cursor.month == 12 else datetime(cursor.year, cursor.month + 1, 1)
    return months


# ============================================================
# HAKEDİŞ TAKVİMİ (payout calendar)
# ============================================================

def _payout_row_delta(mp, raw_type, seller_revenue, debt, credit, commission_amount):
    """_load_settlement_lines'daki KATEGORİ MATEMATİĞİYLE BİREBİR AYNI
    (bilinçli tekrar — orada (spid, barcode) bazında, burada (mp, gün)
    bazında gruplanıyor; formülün kendisi değişmiyor).

    Döndürür: (gross_revenue_delta, commission_delta, service_fee_delta,
               return_amount_delta)
    """
    cat = _categorize(mp, raw_type)
    gr = comm = svc = ret = 0.0
    if cat == "sale":
        if mp == "hepsiburada":
            gr += seller_revenue or 0.0
        else:
            gr += credit or 0.0
            comm += commission_amount or 0.0
    elif cat == "commission":
        comm += abs(seller_revenue or 0.0)
    elif cat == "service_fee":
        svc += abs(seller_revenue or 0.0)
    elif cat == "return":
        ret += (debt or 0.0) - (credit or 0.0)
    return gr, comm, svc, ret


def _payout_row_net(mp, raw_type, seller_revenue, debt, credit, commission_amount):
    gr, comm, svc, ret = _payout_row_delta(mp, raw_type, seller_revenue, debt, credit, commission_amount)
    return gr - comm - svc - ret


# HB'de (ve teorik olarak henüz payment_date'i oluşmamış herhangi bir TY
# satırında) settlements.payment_date NULL olduğunda, sipariş tarihinden bu
# yana geçen ORTALAMA gün farkını kullanarak tahmini bir hakediş günü
# üretiyoruz (bkz. 02.08.2026 analizi: HB Sale ortalama +38.8 gün,
# Return +36.5 gün; TY Sale +34.3, Return +28.6 — TY'de bu satır pratikte
# neredeyse hiç tetiklenmiyor çünkü TY payment_date'i satış anında dolduruyor).
#
# ⚠️ BU DEĞERLER STATİKTİR ve tek seferlik bir analize (02.08.2026) dayanıyor
# — pazaryeri ödeme davranışı zamanla değişirse (kampanya, politika
# güncellemesi, vb.) bu sayılar sessizce yanlışlaşabilir, hiçbir otomatik
# uyarı/yeniden kalibrasyon mekanizması yok. Periyodik olarak (örn. ayda bir)
# aşağıdaki compute_actual_payout_lag_days() fonksiyonunu çalıştırıp bu
# sözlüğü elle güncellemeniz önerilir — fonksiyon, DB'de artık gerçek
# payment_date'i atanmış satırlardan güncel ortalama gecikmeyi hesaplar.
_AVG_PAYOUT_LAG_DAYS = {
    "hepsiburada": {"sale": 39, "return": 37},
    "trendyol": {"sale": 34, "return": 29},
}


def compute_actual_payout_lag_days(min_sample_size=20):
    """_AVG_PAYOUT_LAG_DAYS'in hâlâ güncel olup olmadığını kontrol etmek için
    teşhis fonksiyonu. DB'de artık GERÇEK bir payment_date'i atanmış
    (transaction_date IS NOT NULL AND payment_date IS NOT NULL) satırlardan,
    (pazaryeri, kategori) bazında ORTALAMA (payment_date - transaction_date)
    gün farkını hesaplar.

    Otomatik olarak _AVG_PAYOUT_LAG_DAYS'i GÜNCELLEMEZ (bilinçli tercih —
    sessiz bir davranış değişikliğine yol açmasın diye); sonucu inceleyip
    sabit sözlüğü elle güncellemek kullanıcıya/geliştiriciye bırakılmıştır.
    Örnek kullanım: `python -c "from finance_engine import compute_actual_payout_lag_days as f; print(f())"`

    min_sample_size: bu sayının altında örneklemi olan (pazaryeri, kategori)
    çiftleri güvenilmez sayılıp sonuca dahil edilmez (sample_size raporlanır,
    ama averageLagDays None döner).
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT marketplace, raw_transaction_type, transaction_date, payment_date
            FROM settlements
            WHERE transaction_date IS NOT NULL AND payment_date IS NOT NULL
        """).fetchall()

    buckets = defaultdict(list)
    for r in rows:
        cat = _categorize(r["marketplace"], r["raw_transaction_type"])
        if cat not in ("sale", "return"):
            continue
        lag_days = (r["payment_date"] - r["transaction_date"]) / (1000 * 60 * 60 * 24)
        buckets[(r["marketplace"], cat)].append(lag_days)

    result = {}
    for (mp, cat), lags in buckets.items():
        result.setdefault(mp, {})[cat] = {
            "sampleSize": len(lags),
            "averageLagDays": round(sum(lags) / len(lags), 1) if len(lags) >= min_sample_size else None,
            "currentStaticValue": _AVG_PAYOUT_LAG_DAYS.get(mp, {}).get(cat),
        }
    return result

# GERÇEK ödeme günleri — DB'deki other_financials.PaymentOrder geçmişi (Trendyol)
# ve satıcı panelinin "Ödemeler" ekranı (Hepsiburada) karşılaştırılarak
# doğrulandı (02.08.2026, bkz. sohbet geçmişi):
#   Trendyol -> Pazartesi VE Perşembe (haftada 2 kez toplu ödeme emri)
#   Hepsiburada -> her Salı (Salı-Pazartesi hakediş dönemi, ertesi Salı öder)
# Python weekday(): Pazartesi=0 ... Pazar=6.
# NOT: Bu bir API kontratı DEĞİL, gözlemlenen davranış — pazaryeri
# takvimini değiştirirse (resmi tatil, kampanya vb.) burada da güncellenmesi
# gerekebilir.
_PAYOUT_WEEKDAYS = {
    "trendyol": {0, 3},
    "hepsiburada": {1},
}


def _round_up_to_payout_day(dt, mp):
    """dt'yi (veya dt'nin kendisi zaten bir ödeme günüyse dt'yi) pazaryerinin
    GERÇEK toplu ödeme gününe (bkz. _PAYOUT_WEEKDAYS) ileri yuvarlar.
    Bilinmeyen/tanımsız marketplace için dt'yi değiştirmeden döner.
    """
    weekdays = _PAYOUT_WEEKDAYS.get(mp)
    if not weekdays:
        return dt
    d = dt
    for _ in range(8):  # en kötü ihtimalle 7 gün içinde mutlaka bir ödeme günü vardır
        if d.weekday() in weekdays:
            return d
        d = d + timedelta(days=1)
    return dt  # teorik olarak buraya hiç düşülmez (güvenlik ağı)


def payout_calendar(start_dt=None, end_dt=None, marketplace_filter=None):
    """GÜNLÜK hakediş takvimi — hesaba gerçekten geçen / geçecek parayı üç
    ayrı güven seviyesinde döner:

      confirmed    -> GERÇEK banka verisi. Şu an sadece Trendyol için
                       mevcut: other_financials.transaction_type='PaymentOrder'
                       (fiilen hesaba yatan ödeme emri tutarı, debt-credit).
                       Hepsiburada'da bu API/kayıt karşılığı yok (doğrulandı,
                       02.08.2026), o yüzden HB hiçbir zaman 'confirmed'
                       üretmez — bu bilinçli bir kısıt, hata değil.
      estimated    -> Pazaryerinin KENDİ bildirdiği payment_date'e göre
                       (settlements tablosu). TY için sadece GELECEK günlerde
                       kullanılır (geçmiş günlerde artık gerçek PaymentOrder
                       verisi var, iki kaynağı karıştırmamak için geçmişte
                       settlements'a hiç bakılmaz). HB için TÜM günlerde
                       kullanılır (gerçek karşılığı olmadığından).
      lagEstimated -> settlements.payment_date'i HENÜZ NULL olan (pazaryeri
                       tarafından bir tarih verilmemiş) satırlar için, işlem
                       tarihine ortalama gecikme eklenerek BİZİM ürettiğimiz
                       kaba bir tahmin. 'overdue': tahmini tarih bugünden
                       önce ama hâlâ gerçek bir payment_date atanmamışsa True
                       (pazaryeri gecikmiş olabilir demektir).
      official     -> Pazaryerinin kendi panelinden (DevTools ile) çekilen
                       gelecek ödeme verisi (bkz. external_payout_db.py /
                       external_payout_scraper.py). Bir (gün, pazaryeri) için
                       bu veri varsa, o hücrenin estimated/lagEstimated
                       kalemleri 0'a çekilir ve tutar official'a taşınır —
                       aynı parayı iki kez saymamak için. Panel arayüzünde bu
                       endpoint'ten hiç veri çekilmediyse (credential
                       girilmemiş/sync hiç tetiklenmemiş) bu kalem her zaman 0
                       kalır, davranış öncekiyle birebir aynıdır.

    Bir (gün, pazaryeri) hücresi bu dört kalemin toplamını taşıyabilir — ör.
    aynı günde hem eski bir lagEstimated tahmin hem yeni bir estimated satır
    denk gelebilir (official geldiğinde ikisi de sıfırlanıp yerini alır).
    Panelde confirmed koyu, estimated orta, lagEstimated soluk/kesikli,
    official ayrı bir renkle (pazaryerinin kendi beyanı) gösterilmesi önerilir.

    start_dt/end_dt verilirse SADECE settlements/estimated tarafını
    (payment_date aralığını) sınırlar; PaymentOrder ve lagEstimated tarafı
    kasıtlı olarak sınırsız taranır (aksi halde ör. aralık dışına düşen
    gerçek bir banka ödemesi sessizce kaybolur).
    """
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ms = int(today_start.timestamp() * 1000)

    settlement_where = "payment_date IS NOT NULL"
    settlement_params = []
    if start_dt is not None and end_dt is not None:
        settlement_where += " AND payment_date BETWEEN ? AND ?"
        settlement_params = [int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000)]

    with get_connection() as conn:
        settlement_rows = conn.execute(f"""
            SELECT marketplace, raw_transaction_type, seller_revenue, debt, credit,
                   commission_amount, payment_date
            FROM settlements
            WHERE {settlement_where}
        """, settlement_params).fetchall()

        unplanned_rows = conn.execute("""
            SELECT marketplace, raw_transaction_type, seller_revenue, debt, credit,
                   commission_amount, transaction_date
            FROM settlements
            WHERE payment_date IS NULL
        """).fetchall()

        payment_order_rows = conn.execute("""
            SELECT marketplace, payment_date, debt, credit
            FROM other_financials
            WHERE transaction_type = 'PaymentOrder' AND payment_date IS NOT NULL
        """).fetchall()

    if marketplace_filter:
        settlement_rows = [r for r in settlement_rows if r["marketplace"] == marketplace_filter]
        unplanned_rows = [r for r in unplanned_rows if r["marketplace"] == marketplace_filter]
        payment_order_rows = [r for r in payment_order_rows if r["marketplace"] == marketplace_filter]

    # buckets[day][marketplace] = {"confirmed": x, "estimated": y, "lagEstimated": z}
    buckets = defaultdict(lambda: defaultdict(lambda: {
        "confirmed": 0.0, "estimated": 0.0, "lagEstimated": 0.0,
    }))
    overdue = defaultdict(lambda: defaultdict(bool))
    true_unplanned = defaultdict(float)  # transaction_date'i de olmayan, gerçekten dağıtılamayan satırlar

    # 1) Trendyol GERÇEK banka ödemeleri (PaymentOrder) -> confirmed
    for r in payment_order_rows:
        mp = r["marketplace"]
        day_key = datetime.fromtimestamp(r["payment_date"] / 1000).strftime("%Y-%m-%d")
        net = (r["debt"] or 0.0) - (r["credit"] or 0.0)
        buckets[day_key][mp]["confirmed"] += net

    # 2) Pazaryerinin kendi bildirdiği payment_date (settlements) -> estimated
    #    TY: sadece GELECEK günler (geçmiş zaten PaymentOrder'dan geldi).
    #    HB: her zaman (gerçek karşılığı yok).
    for r in settlement_rows:
        mp = r["marketplace"]
        raw_dt = datetime.fromtimestamp(r["payment_date"] / 1000)
        raw_day_ms = int(raw_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        if mp == "trendyol" and raw_day_ms <= today_start_ms:
            continue
        payout_dt = _round_up_to_payout_day(raw_dt, mp)
        day_key = payout_dt.strftime("%Y-%m-%d")
        net = _payout_row_net(mp, r["raw_transaction_type"], r["seller_revenue"], r["debt"], r["credit"], r["commission_amount"])
        buckets[day_key][mp]["estimated"] += net

    # 3) payment_date'i hiç atanmamış satırlar -> ortalama gecikmeyle tahmini gün (lagEstimated)
    for r in unplanned_rows:
        mp = r["marketplace"]
        cat = _categorize(mp, r["raw_transaction_type"])
        if cat == "other":
            continue
        net = _payout_row_net(mp, r["raw_transaction_type"], r["seller_revenue"], r["debt"], r["credit"], r["commission_amount"])
        tx_date_ms = r["transaction_date"]
        if tx_date_ms is None:
            true_unplanned[mp] += net
            continue
        lag_bucket = "return" if cat == "return" else "sale"
        lag_days = _AVG_PAYOUT_LAG_DAYS.get(mp, {}).get(lag_bucket, 35)
        raw_est_dt = datetime.fromtimestamp(tx_date_ms / 1000) + timedelta(days=lag_days)
        est_dt = _round_up_to_payout_day(raw_est_dt, mp)
        day_key = est_dt.strftime("%Y-%m-%d")
        buckets[day_key][mp]["lagEstimated"] += net
        if est_dt <= today_start:
            overdue[day_key][mp] = True

    # 4) Pazaryerinin kendi sitesinden (DevTools ile) çekilen "resmi" gelecek
    #    ödeme verisi -> varsa o (gün, pazaryeri) için 2 ve 3'teki
    #    estimated/lagEstimated tahminimizin YERİNE geçer (aynı parayı iki kez
    #    saymamak için) -- bu artık bizim tahminimiz değil, pazaryerinin kendi
    #    beyanı, dolayısıyla daha güvenilir. external_payout_db henüz hiç
    #    veri çekmemişse (tablo boş/yoksa) sessizce hiçbir şey değişmez.
    try:
        from external_payout_db import get_estimates
        official_rows = get_estimates(marketplace_filter) if marketplace_filter else get_estimates()
    except Exception:
        official_rows = []

    official_days = defaultdict(lambda: defaultdict(float))
    for r in official_rows:
        mp = r["marketplace"]
        raw_dt = datetime.fromtimestamp(r["payment_date"] / 1000)
        if start_dt is not None and end_dt is not None and not (start_dt <= raw_dt <= end_dt):
            continue
        day_key = raw_dt.strftime("%Y-%m-%d")
        official_days[day_key][mp] += r["amount"] or 0.0

    for day_key, by_mp in official_days.items():
        for mp, amount in by_mp.items():
            bucket = buckets[day_key][mp]
            bucket["estimated"] = 0.0
            bucket["lagEstimated"] = 0.0
            bucket["official"] = round(bucket.get("official", 0.0) + amount, 2)
            overdue[day_key][mp] = False

    days = []
    for day_key in sorted(buckets.keys()):
        by_marketplace = {}
        day_total = 0.0
        for mp, amounts in buckets[day_key].items():
            confirmed = round(amounts["confirmed"], 2)
            estimated = round(amounts["estimated"], 2)
            lag_estimated = round(amounts["lagEstimated"], 2)
            official = round(amounts.get("official", 0.0), 2)
            mp_total = round(confirmed + estimated + lag_estimated + official, 2)
            entry = {
                "confirmed": confirmed,
                "estimated": estimated,
                "lagEstimated": lag_estimated,
                "official": official,
                "total": mp_total,
            }
            if lag_estimated and overdue[day_key].get(mp):
                entry["overdue"] = True
            by_marketplace[mp] = entry
            day_total += mp_total
        days.append({
            "date": day_key,
            "total": round(day_total, 2),
            "byMarketplace": by_marketplace,
        })

    unplanned_by_marketplace = {mp: round(v, 2) for mp, v in true_unplanned.items()}
    return {
        "days": days,
        "unplanned": {
            "total": round(sum(true_unplanned.values()), 2),
            "byMarketplace": unplanned_by_marketplace,
        },
        "generatedAt": today_start.strftime("%Y-%m-%d"),
    }


def best_sellers(days=None, start_dt=None, end_dt=None, limit=10, marketplace_filter=None):
    summary = compute_profit_summary(days=days, start_dt=start_dt, end_dt=end_dt, marketplace_filter=marketplace_filter)

    agg = defaultdict(lambda: {"sku": None, "productName": None, "quantity": 0,
                                "revenue": 0.0, "profit": 0.0, "profit_lines": 0})
    for ln in summary["lines"]:
        group_key = ln["sku"] or (f"barcode:{ln['barcode']}" if ln["barcode"] else "unknown")
        a = agg[group_key]
        a["sku"] = ln["sku"]
        a["productName"] = ln["productName"]
        a["quantity"] += ln["quantity"] or 0
        a["revenue"] += ln["grossRevenue"] or 0
        if ln["profit"] is not None:
            a["profit"] += ln["profit"]
            a["profit_lines"] += 1

    result = []
    for group_key, a in agg.items():
        margin = (a["profit"] / a["revenue"]) if a["revenue"] else None
        result.append({
            "sku": a["sku"],
            "productName": a["productName"],
            "quantity": a["quantity"],
            "revenue": round(a["revenue"], 2),
            "profit": round(a["profit"], 2) if a["profit_lines"] else None,
            "margin": round(margin, 4) if margin is not None else None,
        })

    result.sort(key=lambda r: (r["profit"] if r["profit"] is not None else -1e18), reverse=True)
    return result[:limit]


# ============================================================
# BACKFILL NOTU (henüz uygulanmadı — Faz 3'te ele alınacak)
# ============================================================
# order_lines'da karşılığı olmayan (settlement-only) HB paketlerinde
# quantity=1 varsayımı kalıcı bir hata kaynağı olabilir, çünkü HB'nin
# /packages endpoint'i sadece "Open" kuyruğunu döndürüyor; geçmiş teslim
# edilmiş paketler bu yoldan çekilemiyor. Gerçek quantity'yi kazanmak için
# /orders/merchantid/{id}/ordernumber/{orderNumber} endpoint'inden tek tek
# sipariş detayı çekip order_lines'ı geriye dönük doldurmak gerekiyor.
# Bu, kademeli/throttled bir arka plan job'u olarak app.py'ye eklenecek
# (bkz. Faz 3 planı) — burada sadece "quantityEstimated": true işaretiyle
# şeffaf hale getirildi, henüz otomatik düzeltilmiyor.
