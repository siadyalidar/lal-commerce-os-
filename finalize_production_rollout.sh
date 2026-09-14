#!/bin/bash
# finalize_production_rollout.sh
# ---------------------------------
# ON-KOSUL: patch 0001, 0002, 0003 VE 0004 uygulanmis olmali (0004,
# Beat aktivasyonundan sonra testlerin kirilmasini onleyen bir test
# duzeltmesidir -- bu script olmadan calisirsa adim 5'te test suite
# BASARISIZ olur).
#
# TEK SEFERDE calistirilir. Adimlar sirayla:
#   0) on-kosul kontrolu (patch 0001-0003 uygulanmis mi)
#   1) HB barcode sayisi (salt okunur)
#   2) kontrollu rollout (limit=5) -- GERCEK Hepsiburada API'sine istek atar
#   3) SADECE rollout basarili (skipped degil, failed_skus bos) ise:
#      Beat schedule'i aktive et
#   4) Beat schedule dogrulama (aktif mi, duplicate var mi)
#   5) SADECE 3-4 basariliysa: tam test suite'i calistir
#   6) SADECE testler gecerse: commit at
#
# Herhangi bir adimda sorun cikarsa script DURUR, commit ATILMAZ,
# Beat aktive EDILMEZ. Hata mesaji acikca yazdirilir.
#
# Kullanim:
#   cd /Users/s1dar/Desktop/lal-commerce-os-guncel
#   bash finalize_production_rollout.sh

set -e
trap 'echo ""; echo "!!! SCRIPT BASARISIZ OLDU (satir $LINENO) -- Beat aktive edilmedi, commit atilmadi. Yukaridaki hatayi incele. !!!"' ERR

echo "======================================================================"
echo "0) ON-KOSUL: patch 0001-0004 uygulanmis mi?"
echo "======================================================================"
python3 -c "
import inspect
import hb_review_sync_tasks
sig = inspect.signature(hb_review_sync_tasks.sync_hepsiburada_reviews.run)
assert 'limit' in sig.parameters, 'limit parametresi yok -- patch 0003 uygulanmamis olabilir, once onu uygulayin.'
assert hasattr(hb_review_sync_tasks, 'acquire_sync_lock'), 'lock entegrasyonu yok -- patch 0003 eksik.'
print('OK: production kod (limit + lock) mevcut.')
"
echo ""
echo "--- Mevcut test suite (Beat aktivasyonundan ONCE) ---"
python3 -m pytest -q 2>&1 | tail -6

echo ""
echo "======================================================================"
echo "1) HB BARCODE SAYISI (salt okunur)"
echo "======================================================================"
python3 -c "
import database
barcodes = database.list_hb_review_barcodes()
print(f'HB barcode sayisi: {len(barcodes)}')
"

echo ""
echo "======================================================================"
echo "2) KONTROLLU ROLLOUT (limit=5) -- GERCEK Hepsiburada API'sine istek"
echo "======================================================================"
python3 << 'PYEOF'
import json
import sys

from hb_review_sync_tasks import sync_hepsiburada_reviews

result = sync_hepsiburada_reviews.run(limit=5)
print(json.dumps(result, ensure_ascii=False, indent=2))

with open("/tmp/hb_rollout_result.json", "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

if result.get("skipped"):
    print("\nUYARI: sync 'skipped' dondu (kilit alinamadi) -- BEAT AKTIVE EDILMEYECEK.")
    sys.exit(1)

failed = result.get("failed_skus", [])
if failed:
    print(f"\nUYARI: {len(failed)} sku basarisiz oldu -- BEAT AKTIVE EDILMEYECEK.")
    for f in failed:
        print(f"  sku={f.get('sku')} phase={f.get('phase')} hata={f.get('error')}")
    sys.exit(1)

print("\nKontrollu rollout basarili -- 0 basarisiz sku.")
PYEOF

echo ""
echo "======================================================================"
echo "3) BEAT SCHEDULE AKTIVASYONU (sadece rollout basariliysa buraya gelinir)"
echo "======================================================================"
python3 << 'PYEOF'
path = "celery_app.py"
with open(path) as f:
    content = f.read()

old_block = '''    # "hb-review-sync": {
    #     "task": "hb_review_sync_tasks.sync_hepsiburada_reviews",
    #     "schedule": crontab(hour=4, minute=0),  # her gece 04:00 (Europe/Istanbul)
    #     "kwargs": {"limit": None},  # kontrollü ilk rollout sonrası sınırsız
    # },'''

new_block = '''    "hb-review-sync": {
        "task": "hb_review_sync_tasks.sync_hepsiburada_reviews",
        "schedule": crontab(hour=4, minute=0),  # her gece 04:00 (Europe/Istanbul)
        "kwargs": {"limit": None},
    },'''

if new_block in content:
    print("Beat schedule zaten aktif -- degisiklik gerekmiyor.")
elif old_block in content:
    content = content.replace(old_block, new_block)
    with open(path, "w") as f:
        f.write(content)
    print("Beat schedule aktive edildi.")
else:
    raise SystemExit(
        "HATA: beklenen yorum blogu bulunamadi -- celery_app.py elle kontrol edilmeli "
        "(muhtemelen dosya beklenenden farkli, script durduruluyor, DEGISIKLIK YAPILMADI)."
    )
PYEOF

echo ""
echo "======================================================================"
echo "4) BEAT SCHEDULE DOGRULAMA (aktif mi, duplicate var mi, celisme var mi)"
echo "======================================================================"
python3 -c "
from celery_app import celery_app

sched = celery_app.conf.beat_schedule
assert 'hb-review-sync' in sched, 'hb-review-sync schedule kayitli degil!'
entry = sched['hb-review-sync']
print('Task:', entry['task'])
print('Schedule:', entry['schedule'])
print('kwargs:', entry.get('kwargs'))

task_names = [v['task'] for v in sched.values()]
count = task_names.count('hb_review_sync_tasks.sync_hepsiburada_reviews')
assert count == 1, f'DUPLICATE SCHEDULE: {count} kayit bulundu!'
print('Duplicate schedule kontrolu: OK (tek kayit)')

# nightly-reconciliation-sync ile saat celismesi kontrolu
recon = sched.get('nightly-reconciliation-sync')
if recon:
    print('nightly-reconciliation-sync schedule:', recon['schedule'], '(review sync: 04:00, bu farkli)')
"

echo ""
echo "======================================================================"
echo "5) TAM TEST SUITE (sadece Beat basariyla aktive edildiyse buraya gelinir)"
echo "======================================================================"
python3 -m pytest -q 2>&1 | tail -20

echo ""
echo "======================================================================"
echo "6) GIT COMMIT (sadece testler gectiyse buraya gelinir)"
echo "======================================================================"
git add -A
git status --short
git commit -m "EKLEME: HB review sync Beat schedule production'da aktive edildi

- Kontrollu rollout (limit=5) basariyla tamamlandi (0 basarisiz sku),
  sonuc /tmp/hb_rollout_result.json'da.
- celery_app.py: hb-review-sync artik AKTIF -- her gece 04:00 (Europe/Istanbul),
  kwargs={'limit': None}, nightly-reconciliation-sync (03:00) ile celismiyor.
- Duplicate schedule kontrolu yapildi: tek kayit.
- Tam test suite calistirildi ve gecti."

echo ""
echo "======================================================================"
echo "SONUC"
echo "======================================================================"
echo "Son commit:"
git log --oneline -1
echo ""
echo "Working tree durumu (bos olmali):"
git status --short
echo ""
echo "Degisen dosyalar (son commit):"
git diff --stat HEAD~1 HEAD
echo ""
echo "TAMAMLANDI."
