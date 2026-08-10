#!/usr/bin/env python3
"""
fix_sidebar_reports_link.py
-----------------------------
Codex'in yeni sidebar'ı üretirken düşürdüğü "Raporlar" menü linkini
templates/components/_sidebar.html içine geri ekler (Stok linkinin hemen
altına, aynı nav grubu içinde).

KULLANIM:
    cd ~/Desktop/lal-commerce-os-guncel
    python3 fix_sidebar_reports_link.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def fail(what):
    print(f"\n❌ DURDURULDU: {what}")
    sys.exit(1)


def main():
    path = ROOT / "templates" / "components" / "_sidebar.html"
    if not path.exists():
        fail(f"{path} bulunamadı")
    text = path.read_text(encoding="utf-8")

    if "active_page == 'raporlar'" in text:
        print("= Raporlar linki zaten mevcut, değişiklik yapılmadı")
        return

    anchor = '''    <a class="lal-nav-item {{ 'is-active' if active_page == 'stok' else '' }}" href="{{ url_for('dashboard_routes.stok_page') }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>
      <span class="lal-nav-label">Stok</span>
    </a>'''

    if text.count(anchor) != 1:
        fail(f"Stok linkinin beklenen orijinal metni bulunamadı (1 yerine {text.count(anchor)} eşleşme)")

    reports_link = '''
    <a class="lal-nav-item {{ 'is-active' if active_page == 'raporlar' else '' }}" href="/raporlar">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>
      <span class="lal-nav-label">Raporlar</span>
    </a>'''

    new_text = text.replace(anchor, anchor + reports_link, 1)
    path.write_text(new_text, encoding="utf-8")
    print("✅ Raporlar linki sidebar'a eklendi (Stok'un altına)")
    print("Tarayıcıda sayfayı yenile (Flask'ı yeniden başlatmana gerek yok, template her istekte okunur).")


if __name__ == "__main__":
    main()
