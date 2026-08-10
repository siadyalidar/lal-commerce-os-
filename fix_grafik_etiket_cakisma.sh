#!/bin/bash
set -e
python3 << 'PYEOF'
path = "static/js/gosterge-paneli.js"
with open(path, encoding="utf-8") as f:
    content = f.read()

old1 = """            datalabels: { display: (c) => c.dataIndex === lastIdx, anchor: 'end', align: 'right', offset: 8, clamp: true, color: '#34D399', font: { size: 11, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },"""
new1 = """            datalabels: { display: (c) => c.dataIndex === lastIdx, anchor: 'end', align: 'top', offset: 6, clamp: true, color: '#34D399', font: { size: 11, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },"""

old2 = """            datalabels: { display: (c) => c.dataIndex === lastIdx, anchor: 'start', align: 'right', offset: 8, clamp: true, color: '#F59E0B', font: { size: 11, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },"""
new2 = """            datalabels: { display: (c) => c.dataIndex === lastIdx, anchor: 'end', align: 'bottom', offset: 6, clamp: true, color: '#F59E0B', font: { size: 11, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },"""

c1 = content.count(old1)
c2 = content.count(old2)
if c1 != 1 or c2 != 1:
    print(f"HATA: blok1={c1}, blok2={c2} (ikisi de 1 olmalı). Dosya değiştirilmedi.")
    raise SystemExit(1)

content = content.replace(old1, new1).replace(old2, new2)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: static/js/gosterge-paneli.js güncellendi.")
PYEOF

if [ $? -eq 0 ]; then
  git add static/js/gosterge-paneli.js && \
  git commit -m "fix(gosterge-paneli): son ay etiketlerinin cakismasini dikey ayirma ile gider" && \
  git push
else
  echo "Script hata verdi, git islemi yapilmadi."
fi
