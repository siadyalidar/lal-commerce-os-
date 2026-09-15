"""
tests/test_trendyol_cargo_items.py
------------------------------------
trendyol_finance.py::_cargo_items_to_rows() için testler.

DÜZELTME (15.09.2026): Bu dosya RC1 düzeltmesinden (14.09.2026) beri kırıktı
-- fonksiyon tekil `_cargo_item_to_row(item, invoice)` imzasından çoğul
`_cargo_items_to_rows(items, invoice)` imzasına geçmişti (bkz. o fonksiyonun
docstring'i: aynı hash'e sahip kalemler için occurrence-counter eklendi,
tekil işleme modeliyle bu artık mümkün değil), ama testler güncellenmemişti
(ImportError, pytest collection'ı durduruyordu). Test senaryolarının KENDİSİ
hâlâ geçerli/değerli -- sadece çağrı şekli tek-item-listesi + [0] indeksine
uyarlandı.

KÖK NEDEN (09.09.2026 canlı ortamda keşfedildi): Trendyol kargo faturası
kalemleri API'sinde item'ların KENDİ id'si yok ("id"/"invoiceItemId"/
"itemId" hiçbir zaman gelmiyor), bu yüzden kod invoice_serial_number +
orderNumber birleşimini fallback PK olarak kullanıyordu. AMA aynı siparişin
hem "Gönderi Kargo Bedeli" (gidiş) hem "İade Kargo Bedeli" (dönüş) kalemi
AYNI orderNumber'ı taşıyor -> aynı id üretiliyor -> upsert biri diğerinin
ÜZERİNE YAZIYOR, kargo maliyeti sessizce eksik/yanlış kayıt ediliyor.

Doğrusu: API'nin her kalem için verdiği 'parcelUniqueId' alanı GERÇEKTEN
benzersiz (gidiş ve iade için farklı parsel id'leri var, bkz. canlı örnek
aşağıdaki testlerde) -- bu id üretiminde kullanılmalı.
"""

from trendyol_finance import _cargo_items_to_rows


def test_gonderi_ve_iade_kargo_kalemleri_ayni_id_uretmemeli():
    """KRİTİK REGRESYON: aynı orderNumber'a ait Gönderi ve İade kargo kalemleri
    (canlı ortamda gözlemlenen gerçek veri şekli) FARKLI id üretmeli --
    aksi halde upsert biri diğerini siler (bkz. B4 sonrası canlı ortamda
    235 kalemden 21'inin kaybolması)."""
    gonderi = {
        "shipmentPackageType": "Gönderi Kargo Bedeli",
        "parcelUniqueId": 7330031883537786,
        "orderNumber": "11133789340",
        "amount": 93.05,
        "desi": 0,
    }
    iade = {
        "shipmentPackageType": "İade Kargo Bedeli",
        "parcelUniqueId": 7260031979375171,
        "orderNumber": "11133789340",
        "amount": 100.72,
        "desi": 1,
    }
    # Aynı faturanın İKİ kalemi birlikte işleniyor (gerçek akışta da böyle,
    # bkz. sync_cargo_costs: tüm liste tek çağrıda _cargo_items_to_rows'a girer).
    rows = _cargo_items_to_rows([gonderi, iade], "DDF2026012103322")
    row_gonderi, row_iade = rows[0], rows[1]

    assert row_gonderi["id"] != row_iade["id"]
    assert row_gonderi["order_number"] == row_iade["order_number"] == "11133789340"
    assert row_gonderi["amount"] == 93.05
    assert row_iade["amount"] == 100.72


def test_parcel_unique_id_kullanilir_id_olarak():
    """parcelUniqueId varsa doğrudan id üretiminde kullanılmalı (fallback
    zincirine düşülmemeli)."""
    item = {
        "shipmentPackageType": "Gönderi Kargo Bedeli",
        "parcelUniqueId": 7330031883537786,
        "orderNumber": "11133789340",
        "amount": 93.05,
        "desi": 0,
    }
    row = _cargo_items_to_rows([item], "DDF2026012103322")[0]
    assert "7330031883537786" in row["id"]


def test_explicit_item_id_hala_onceliklidir():
    """Regresyon: API bir gün gerçek 'id'/'invoiceItemId' alanı vermeye
    başlarsa (dosyadaki not: alan adları doğrulanmamış), o alan HALA
    parcelUniqueId'den önce kullanılmalı."""
    item = {
        "id": "REAL-ITEM-ID-123",
        "parcelUniqueId": 7330031883537786,
        "orderNumber": "11133789340",
        "amount": 93.05,
    }
    row = _cargo_items_to_rows([item], "DDF2026012103322")[0]
    assert row["id"] == "REAL-ITEM-ID-123"


def test_parcel_unique_id_de_yoksa_eski_fallback_calisir():
    """Regresyon: parcelUniqueId bile yoksa (beklenmeyen bir şema), eski
    barcode/orderNumber/shipmentPackageId fallback zinciri hâlâ çalışmalı
    (çökmemeli, en azından bir id üretmeli).

    GÜNCELLENDİ (15.09.2026, RC1 ile birlikte): id artık SADECE
    'invoice-orderNumber' değil -- içerik hash'i + occurrence-counter de
    ekleniyor (aynı orderNumber'a ait, parcelUniqueId'siz birden fazla kalem
    çakışmasın diye, bkz. _cargo_items_to_rows docstring'i). Bu yüzden tam
    eşitlik yerine ÖN EK (prefix) kontrolü yapılıyor -- davranışın kırılganlığı
    yerine niyeti (invoice + orderNumber ile başlaması) test ediliyor."""
    item = {"orderNumber": "11133789340", "amount": 50.0}
    row = _cargo_items_to_rows([item], "DDF2026012103322")[0]
    assert row["id"].startswith("DDF2026012103322-11133789340-")


def test_ayni_icerikli_iki_kalem_farkli_id_alir_occurrence_ile():
    """YENİ (15.09.2026): parcelUniqueId'siz VE birebir aynı içerikli iki
    kalem gelirse (content_hash de aynı çıkar), occurrence-counter sayesinde
    yine de FARKLI id almalılar -- aksi halde ikincisi birincinin üzerine
    sessizce yazar (bkz. _cargo_items_to_rows docstring'indeki RC1 senaryosu)."""
    item = {"orderNumber": "11133789340", "amount": 50.0}
    rows = _cargo_items_to_rows([item, dict(item)], "DDF2026012103322")
    assert rows[0]["id"] != rows[1]["id"]


def test_raw_json_her_zaman_saklanir():
    """Ne olursa olsun ham JSON her zaman saklanmalı (ileride şema netleşirse
    geriye dönük düzeltme yapılabilsin diye)."""
    item = {
        "shipmentPackageType": "İade Kargo Bedeli",
        "parcelUniqueId": 7260031979375171,
        "orderNumber": "11133789340",
        "amount": 100.72,
        "desi": 1,
    }
    row = _cargo_items_to_rows([item], "DDF2026012103322")[0]
    assert "İade Kargo Bedeli" in row["raw_json"]
