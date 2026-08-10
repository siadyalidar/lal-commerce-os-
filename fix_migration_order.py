#!/usr/bin/env python3
"""
fix_migration_order.py
------------------------
apply_growth_patch.py'nin _migrate_growth_columns fonksiyonunu yanlış yere
(MIGRATIONS listesinden SONRAYA) koyması yüzünden çıkan NameError'ı düzeltir.

Fonksiyonu mevcut konumundan keser, MIGRATIONS listesinin başlangıcından
hemen önceye taşır.

KULLANIM:
    cd ~/Desktop/lal-commerce-os-guncel
    python3 fix_migration_order.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def fail(what):
    print(f"\n❌ DURDURULDU: {what}")
    print("   Dosya değiştirilmedi. Bu çıktıyı Claude'a gönder.")
    sys.exit(1)


def main():
    path = ROOT / "database.py"
    text = path.read_text(encoding="utf-8")

    func_start_marker = "def _migrate_growth_columns(conn):"
    func_end_marker = "\n\n\ndef init_db():"

    if func_start_marker not in text:
        fail(f"'{func_start_marker}' bulunamadı")
    if text.count(func_start_marker) != 1:
        fail(f"'{func_start_marker}' 1 kez değil {text.count(func_start_marker)} kez bulundu")

    start_idx = text.index(func_start_marker)

    # Fonksiyonun bittiği yeri bul: init_db()'den hemen önceki boşluklar dahil
    if func_end_marker in text:
        end_idx = text.index(func_end_marker) + len("\n\n\n")  # init_db'yi dahil etmeden öncesini al
    else:
        # Daha esnek: sadece "def init_db():" ara
        alt_marker = "def init_db():"
        if alt_marker not in text:
            fail("init_db() bulunamadı, fonksiyonun nerede bittiğini tespit edemedim")
        end_idx = text.index(alt_marker)
        # geriye doğru boş satırları da fonksiyon bloğuna dahil et
        while text[end_idx - 1] in ("\n", " "):
            end_idx -= 1
        end_idx += 0  # tam fonksiyon sonu

    func_block = text[start_idx:end_idx].rstrip("\n")
    print("Taşınacak blok bulundu:")
    print("-" * 40)
    print(func_block[:200] + ("..." if len(func_block) > 200 else ""))
    print("-" * 40)

    # Fonksiyonu eski konumundan çıkar
    text_without_func = text[:start_idx] + text[end_idx:]
    # start_idx civarındaki fazla boş satırları temizle (en fazla 2 art arda)
    text_without_func = re.sub(r"\n{3,}(def init_db)", r"\n\n\1", text_without_func)

    # MIGRATIONS listesinin başlangıcını bul: anchor tuple'ından geriye doğru
    # "= [" ile biten en yakın satırı ara
    anchor = '("2026_07_28_composite_marketplace_keys", _migrate_composite_keys),'
    if anchor not in text_without_func:
        fail(f"'{anchor}' MIGRATIONS listesinde bulunamadı")
    anchor_idx = text_without_func.index(anchor)

    list_start_pattern = re.compile(r"\n(\w+)\s*=\s*\[\s*\n")
    matches = list(list_start_pattern.finditer(text_without_func, 0, anchor_idx))
    if not matches:
        fail("MIGRATIONS listesinin başlangıcı ('X = [' satırı) bulunamadı")
    list_start_match = matches[-1]
    insert_pos = list_start_match.start() + 1  # \n'den sonrası

    new_text = (
        text_without_func[:insert_pos]
        + func_block + "\n\n\n"
        + text_without_func[insert_pos:]
    )

    path.write_text(new_text, encoding="utf-8")
    print(f"\n✅ Fonksiyon '{list_start_match.group(1)} = [' listesinden önceye taşındı.")
    print("Şimdi tekrar dene: python3 app.py")


if __name__ == "__main__":
    main()
