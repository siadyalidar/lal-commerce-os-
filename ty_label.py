import io
import os
import re

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_FONTS = (
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
)
_REG, _BOLD = "LalLabel", "LalLabel-Bold"
_ready = False
GRAY = (0.45, 0.45, 0.45)


def _ensure_fonts():
    global _ready
    if _ready:
        return
    for reg, bold in _FONTS:
        if os.path.exists(reg) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont(_REG, reg))
            pdfmetrics.registerFont(TTFont(_BOLD, bold))
            _ready = True
            return
    raise RuntimeError("Türkçe karakter destekli yazı tipi bulunamadı")


def _clean(value):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _wrap(text, font, size, width):
    text = _clean(text)
    if not text:
        return ["-"]
    return simpleSplit(text, font, size, width) or [text]


def _fit(text, font, size, width):
    text = _clean(text) or "-"
    if pdfmetrics.stringWidth(text, font, size) <= width:
        return text
    while len(text) > 1 and pdfmetrics.stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def build_label_pdf(pkg):
    _ensure_fonts()
    tracking = _clean(pkg.get("cargoTrackingNumber"))
    if not tracking.isdigit():
        raise ValueError("Paketin kargo takip numarası yok veya geçersiz")
    addr = pkg.get("shipmentAddress") or {}
    name = _clean(addr.get("fullName")) or _clean(" ".join(
        str(x) for x in (pkg.get("customerFirstName"), pkg.get("customerLastName")) if x))
    full = _clean(addr.get("fullAddress")) or _clean(" ".join(
        str(x) for x in (addr.get("address1"), addr.get("address2")) if x))
    region = "/".join(x for x in (_clean(addr.get("district")), _clean(addr.get("city"))) if x)
    order_no = _clean(pkg.get("orderNumber"))
    items = pkg.get("lines") or []

    W, H = A4
    M = 28
    inner = W - 2 * M
    left_w = 262
    right_w = inner - left_w - 12
    label_w = 78
    val_w = left_w - 14 - label_w - 10
    LS = 13

    rows = [
        ("Sipariş No", [(order_no or "-", False)]),
        ("Ad-Soyad", [(t, False) for t in _wrap(name, _REG, 10.5, val_w)]),
        ("Adres", [(t, False) for t in _wrap(full, _REG, 10.5, val_w)]
         + ([(region, True)] if region else [])),
    ]
    left_h = 52 + sum(len(v) * LS + 8 for _, v in rows) + 6
    box_h = max(left_h, 190)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Trendyol kargo etiketi %s" % order_no)
    c.setLineWidth(0.8)
    c.setStrokeColorRGB(*GRAY)
    top = H - M

    ban_h = 46
    c.roundRect(M, top - ban_h, inner, ban_h, 6, stroke=1, fill=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(_BOLD, 10)
    y = top - 20
    for t in _wrap("Kargo şirketinin dikkatine, bu bir trendyol.com gönderisidir. "
                   "Trendyol anlaşmasına uygun işlem yapabilirsiniz.", _BOLD, 10, inner - 28):
        c.drawString(M + 14, y, t)
        y -= 13

    mid_top = top - ban_h - 18
    rx = M + left_w + 12
    c.roundRect(M, mid_top - box_h, left_w, box_h, 6, stroke=1, fill=0)
    c.roundRect(rx, mid_top - box_h, right_w, box_h, 6, stroke=1, fill=0)

    c.setFillColorRGB(0, 0, 0)
    c.setFont(_BOLD, 15)
    c.drawString(M + 14, mid_top - 28, "Alıcı Bilgileri")
    y = mid_top - 52
    for label, vals in rows:
        c.setFont(_BOLD, 10)
        c.drawString(M + 14, y, label)
        for text, bold in vals:
            c.setFont(_BOLD if bold else _REG, 10.5)
            c.drawString(M + 14 + label_w, y, text)
            y -= LS
        y -= 8

    c.setFont(_BOLD, 15)
    c.drawString(rx + 14, mid_top - 28, "Kargo Barkodu")
    bw = 1.6
    while True:
        bc = code128.Code128(tracking, barWidth=bw, barHeight=78, quiet=False)
        if bc.width <= right_w - 28 or bw <= 0.6:
            break
        bw -= 0.1
    bc.drawOn(c, rx + (right_w - bc.width) / 2, mid_top - 138)
    c.setFont(_REG, 17)
    c.drawCentredString(rx + right_w / 2, mid_top - 162, tracking)

    prod_top = mid_top - box_h - 18
    blocks = [(ln, _wrap(ln.get("productName"), _BOLD, 10.5, inner - 28 - 66)) for ln in items]
    prod_h = 56 + sum(len(nm) * LS + 40 for _, nm in blocks)
    c.setStrokeColorRGB(*GRAY)
    c.roundRect(M, prod_top - prod_h, inner, prod_h, 6, stroke=1, fill=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(_BOLD, 15)
    c.drawString(M + 22, prod_top - 30, "Ürün Bilgileri")
    y = prod_top - 62
    for i, (ln, nm) in enumerate(blocks, 1):
        c.setStrokeColorRGB(*GRAY)
        c.circle(M + 44, y - 8, 15, stroke=1, fill=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont(_BOLD, 12)
        c.drawCentredString(M + 44, y - 12, str(i))
        tx = M + 80
        yy = y
        c.setFont(_BOLD, 10.5)
        for t in nm:
            c.drawString(tx, yy, t)
            yy -= LS
        cols = [
            ("Adet", "%s Adet" % (ln.get("quantity") or 1)),
            ("Renk", ln.get("productColor")),
            ("Beden", ln.get("productSize")),
            ("Barkod", ln.get("barcode")),
            ("Stok Kodu", ln.get("stockCode") or ln.get("merchantSku")),
        ]
        xs = [tx, tx + 70, tx + 140, tx + 230, tx + 370]
        ws = [62, 62, 82, 132, 100]
        for (lab, val), x0, w0 in zip(cols, xs, ws):
            c.setFillColorRGB(*GRAY)
            c.setFont(_REG, 8)
            c.drawString(x0, yy - 2, lab)
            c.setFillColorRGB(0, 0, 0)
            c.setFont(_BOLD, 9.5)
            c.drawString(x0, yy - 14, _fit(val, _BOLD, 9.5, w0))
        y -= len(nm) * LS + 40
    c.showPage()
    c.save()
    return buf.getvalue()
