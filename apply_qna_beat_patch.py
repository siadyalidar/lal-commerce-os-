#!/usr/bin/env python3
"""
apply_qna_beat_patch.py
------------------------
Trendyol musteri sorulari (QnA) senkronizasyonunu Celery Beat'e baglar.

ONEMLI KARAR: celery_app.py icindeki hazir yorum satiri gunde 1 kez
(05:00) calisacak sekilde tasarlanmisti -- bu, review sync ile ayni
mantik ama musteri sorulari icin cok yavas (musteri telefonuna bildirim
gelir, LAL saatlerce senkronize etmez). Bunun yerine periodic-recent-sync
ile ayni sik araligi kullaniyoruz: her 15 dakikada bir, limit=5 ile
kontrollu.

Kullanim:
    python3 apply_qna_beat_patch.py
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET = Path("celery_app.py")

OLD_BLOCK = '''    # OPSİYONEL — canlı Trendyol kimlik bilgileriyle küçük bir örneklemle
    # (limit=3-5) manuel doğrulama yaptıktan SONRA aşağıdaki yorumu kaldırın
    # (bkz. qna_sync_tasks.py docstring'i, 29.08.2026). 03:00 ve 04:00
    # dolu olduğu için 05:00 seçildi.
    # "trendyol-qna-sync": {
    #     "task": "qna_sync_tasks.sync_trendyol_questions",
    #     "schedule": crontab(hour=5, minute=0),  # her gece 05:00 (Europe/Istanbul)
    #     "kwargs": {"limit": None},
    # },'''

NEW_BLOCK = '''    # AKTİF — 30.08.2026: canlı ortamda manuel tetiklemeyle doğrulandı
    # (bkz. qna_sync_tasks.py docstring'i). Gece tek seferlik bir sync
    # yerine periodic-recent-sync ile AYNI sık aralık tercih edildi --
    # müşteri sorusu geldiğinde saatlerce beklemesin diye. limit=5 ile
    # kontrollü: her çalıştırmada en fazla 5 soru için AI taslağı üretilir
    # (soruların kendisi her zaman tam çekilir, sadece AI üretimi limitli).
    "trendyol-qna-sync": {
        "task": "qna_sync_tasks.sync_trendyol_questions",
        "schedule": 60 * 15,  # her 15 dakikada bir
        "kwargs": {"limit": 5},
    },'''


def main() -> int:
    if not TARGET.exists():
        print(f"HATA: {TARGET} bulunamadı. Bu scripti proje kök dizininde çalıştırın.")
        return 1

    original = TARGET.read_text(encoding="utf-8")

    occurrences = original.count(OLD_BLOCK)
    if occurrences == 0:
        print("HATA: Beklenen yorum satırı bloğu bulunamadı (belki zaten patch'lenmiş?).")
        print("Kontrol için: grep -n 'trendyol-qna-sync' celery_app.py")
        return 1
    if occurrences > 1:
        print(f"HATA: Blok {occurrences} kez bulundu, tam olarak 1 bekleniyordu. Elle kontrol edin.")
        return 1

    backup_path = TARGET.with_suffix(TARGET.suffix + f".bak_qna_beat_{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(TARGET, backup_path)
    print(f"Yedek alındı: {backup_path}")

    patched = original.replace(OLD_BLOCK, NEW_BLOCK)
    TARGET.write_text(patched, encoding="utf-8")
    print(f"{TARGET} güncellendi.")

    # Dogrulama
    verify = TARGET.read_text(encoding="utf-8")
    checks = [
        ('"trendyol-qna-sync": {' in verify, "beat_schedule girdisi eklendi"),
        ('"schedule": 60 * 15' in verify, "15 dakikalık aralık ayarlandı"),
        ('"limit": 5' in verify, "limit=5 kontrollü ayarlandı"),
    ]
    all_ok = True
    for ok, desc in checks:
        status = "OK" if ok else "BAŞARISIZ"
        print(f"  [{status}] {desc}")
        all_ok = all_ok and ok

    if not all_ok:
        print("Doğrulama başarısız, yedekten geri yükleniyor...")
        shutil.copy2(backup_path, TARGET)
        return 1

    print("\nPatch başarılı. Şimdi:")
    print("  1) python3 -c \"import celery_app\"   # syntax kontrolü")
    print("  2) Beat'i başlatın (aşağıdaki komut)")
    print("  3) git add celery_app.py && git commit -m 'EKLEME: Musteri Sorulari QnA sync Beat'e baglandi (15dk, limit=5)' && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
