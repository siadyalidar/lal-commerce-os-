path = "templates/components/_sidebar.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '<span class="lal-brand-mark" aria-hidden="true">L</span>'
new = "<img class=\"lal-brand-mark\" src=\"{{ url_for('static', filename='images/logo.png') }}\" alt=\"LAL Commerce OS logo\">"

if old not in content:
    raise SystemExit("Beklenen satir bulunamadi, _sidebar.html elle kontrol edilmeli.")
content = content.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("_sidebar.html guncellendi.")
