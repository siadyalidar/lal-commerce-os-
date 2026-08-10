path = "templates/base.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

anchor = "<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='css/precision-theme.css') }}\">"
insert = anchor + """

<link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='images/favicon-32x32.png') }}">
<link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='images/favicon-16x16.png') }}">
<link rel="apple-touch-icon" sizes="180x180" href="{{ url_for('static', filename='images/apple-touch-icon.png') }}">
<link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">"""

if anchor not in content:
    raise SystemExit("Anchor bulunamadi, base.html elle kontrol edilmeli.")
content = content.replace(anchor, insert, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("base.html guncellendi.")
