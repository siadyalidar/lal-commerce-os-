#!/bin/bash
mkdir -p ~/Desktop/agustos_rapor
cd ~/Desktop/agustos_rapor

AUTH="sidar:sidar"

echo "=== GENEL ==="
curl -s -u "$AUTH" "http://localhost:5050/api/reports/overview?full_history=true&marketplace=all" | python3 -m json.tool | tee genel.json

echo "=== TRENDYOL ==="
curl -s -u "$AUTH" "http://localhost:5050/api/reports/overview?full_history=true&marketplace=trendyol" | python3 -m json.tool | tee trendyol.json

echo "=== HEPSIBURADA ==="
curl -s -u "$AUTH" "http://localhost:5050/api/reports/overview?full_history=true&marketplace=hepsiburada" | python3 -m json.tool | tee hepsiburada.json

echo "=== IADELER ==="
curl -s -u "$AUTH" "http://localhost:5050/api/reports/export?type=returns&marketplace=all" | python3 -m json.tool | tee iadeler.json

echo "=== GIDERLER ==="
curl -s -u "$AUTH" "http://localhost:5050/api/reports/export?type=expenses&marketplace=all" | python3 -m json.tool | tee giderler.json
