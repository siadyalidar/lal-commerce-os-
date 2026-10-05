"""
Tek seferlik teşhis script'i — DB'ye hiç dokunmaz, sadece Trendyol API'sinden
belirli bir kargo faturasının HAM kalem listesini çeker ve bizim "kayıp"
sandığımız sipariş numaralarının bu ham listede olup olmadığını gösterir.

Kullanım:
    venv/bin/python3 debug_cargo_invoice_raw.py DDF2026018582731 \
        11505486378 11508127424 11508321601 11508472818 11508817543
"""
import sys

from trendyol_finance import fetch_cargo_invoice_items

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Kullanım: debug_cargo_invoice_raw.py <invoice_serial_number> [order_number ...]")
        sys.exit(1)

    invoice_id = sys.argv[1]
    missing_order_numbers = set(sys.argv[2:])

    items = fetch_cargo_invoice_items(invoice_id)
    print(f"\nFatura {invoice_id}: API'den TOPLAM {len(items)} ham kalem döndü.\n")

    found_orders = set()
    for it in items:
        order_no = it.get("orderNumber") or it.get("orderNo")
        found_orders.add(str(order_no))
        print(f"  orderNumber={order_no!r}  shipmentPackageId={it.get('shipmentPackageId') or it.get('packageId')!r}  "
              f"amount={it.get('amount') or it.get('price') or it.get('cargoPrice') or it.get('invoiceAmount') or it.get('total')!r}")

    print(f"\n--- Kontrol edilen 'kayıp' sipariş numaraları bu faturanın HAM API yanıtında var mı? ---")
    for on in missing_order_numbers:
        status = "✅ VAR (API'de mevcut, demek ki bizim kodda kayboluyor)" if on in found_orders else "❌ YOK (Trendyol bu faturaya hiç dahil etmemiş)"
        print(f"  {on}: {status}")
