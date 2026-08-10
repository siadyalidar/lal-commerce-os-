#!/bin/bash
set -e

mkdir -p static/images
cp icon.png static/images/icon.png
cp logo.png static/images/logo.png

sips -z 32 32   static/images/icon.png --out static/images/favicon-32x32.png
sips -z 16 16   static/images/icon.png --out static/images/favicon-16x16.png
sips -z 180 180 static/images/icon.png --out static/images/apple-touch-icon.png
sips -z 192 192 static/images/icon.png --out static/images/android-chrome-192x192.png
sips -z 512 512 static/images/icon.png --out static/images/android-chrome-512x512.png

cat > static/manifest.json << 'MANIFEST_END'
{
  "name": "LAL Commerce OS",
  "short_name": "LAL",
  "icons": [
    { "src": "/static/images/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/images/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png" }
  ],
  "theme_color": "#0f172a",
  "background_color": "#0f172a",
  "display": "standalone",
  "start_url": "/"
}
MANIFEST_END

python3 update_base.py
python3 update_sidebar.py

cat >> static/css/components.css << 'CSS_END'

/* Logo gorseli icin brand-mark override (icon.png/logo.png entegrasyonu) */
img.lal-brand-mark {
  width: 28px;
  height: 28px;
  object-fit: contain;
  border-radius: 6px;
  background: transparent;
}
CSS_END

echo "Tamamlandi."
