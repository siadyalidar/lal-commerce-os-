import pytest

pytest.importorskip("reportlab")
import ty_label as tyl

PKG = {
    "orderNumber": "11691412837", "cargoTrackingNumber": 7330037863693961,
    "customerFirstName": "Uğur", "customerLastName": "Sökmen",
    "shipmentAddress": {"fullName": "Uğur Sökmen", "district": "Çankaya", "city": "Ankara",
                        "fullAddress": "bahçelievler mahallesi 46. sokak no:37/3 akyüz apt."},
    "lines": [{"productName": "Daire Tipi Tesisat ve Beyaz Eşya Koruyucu Su Filtresi",
               "quantity": 1, "productSize": "Tek Ebat", "barcode": "SFHY-3-GRS",
               "stockCode": "SFHY-3-GRS"}],
}


def _need_font():
    try:
        tyl._ensure_fonts()
    except RuntimeError:
        pytest.skip("yazı tipi yok")


def test_pdf_is_generated():
    _need_font()
    pdf = tyl.build_label_pdf(PKG)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


@pytest.mark.parametrize("bad", [None, "", "abc", "12 34"])
def test_bad_tracking_number(bad):
    _need_font()
    with pytest.raises(ValueError):
        tyl.build_label_pdf(dict(PKG, cargoTrackingNumber=bad))
