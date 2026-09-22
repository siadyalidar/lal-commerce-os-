"""
test_sync_core_cargo_reconciliation.py
---------------------------------------
Manuel senkronizasyon ("Verileri Senkronize Et" butonu -> _run_full_sync)
artık Celery beat'in ayakta olup olmadığından TAMAMEN bağımsız olarak, her
çalıştığında reconcile_cargo_costs()'u da (geniş 180 günlük pencere) tetiklemeli.

Arka plan (22.09.2026): Celery beat process'i 15.09.2026 09:32'den itibaren
sessizce ölmüştü ve nightly-cargo-reconciliation görevi 7 gün boyunca hiç
tetiklenmedi -- kullanıcı bunu Siparişler sayfasında Net Kâr'ın "—" görünmesi
üzerine fark etti. Kalıcı çözüm (launchd ile beat/worker'ı servis yapmak)
beat'in tekrar ölme ihtimalini ortadan kaldırmıyor; bu yüzden kargo
reconciliation'ı beat'ten TAMAMEN bağımsız ikinci bir tetikleyiciye
(manuel sync akışı) da bağlıyoruz -- Sidar ne zaman manuel sync yaparsa
yapsın, kargo verisi asla haftalarca geride kalmaz.
"""

from datetime import datetime
from unittest.mock import patch

import sync_core


def test_run_full_sync_calls_wide_window_cargo_reconciliation(db):
    """_run_full_sync, normal dar-pencereli sync_finance_data'nın YANI SIRA
    reconcile_cargo_costs(lookback_days=180) çağırmalı -- Celery beat'in
    çalışıp çalışmadığından bağımsız olarak."""
    fake_finance_result = {
        "settlement_count": 3,
        "other_financial_count": 5,
        "other_financial_failures": [],
        "cargo_invoice_count": 1,
        "cargo_item_count": 2,
    }
    fake_reconcile_result = {
        "other_financial_count": 7,
        "other_financial_failures": [],
        "cargo_invoice_count": 4,
        "cargo_item_count": 9,
    }

    with patch.object(
        sync_core, "sync_orders_to_db", return_value=(0, 0)
    ), patch.object(
        sync_core, "sync_finance_data", return_value=fake_finance_result
    ), patch.object(
        sync_core, "reconcile_cargo_costs", return_value=fake_reconcile_result
    ) as mock_reconcile:
        sync_core._run_full_sync(
            datetime(2026, 9, 1), datetime(2026, 9, 22), incremental_ok=False
        )

    mock_reconcile.assert_called_once()
    _, kwargs = mock_reconcile.call_args
    assert kwargs.get("lookback_days") == 180


def test_run_full_sync_survives_reconciliation_failure(db):
    """Geniş pencereli reconciliation bir istisna fırlatırsa (örn. Trendyol API
    hatası), bu dar-pencereli normal sync'in başarısını GEÇERSİZ KILMAMALI --
    tıpkı other_financial_failures'ın tek tek parça hatalarını yutup devam
    etmesi gibi. Sync 'başarısız' olarak işaretlenmemeli."""
    fake_finance_result = {
        "settlement_count": 3,
        "other_financial_count": 5,
        "other_financial_failures": [],
        "cargo_invoice_count": 1,
        "cargo_item_count": 2,
    }

    with patch.object(
        sync_core, "sync_orders_to_db", return_value=(0, 0)
    ), patch.object(
        sync_core, "sync_finance_data", return_value=fake_finance_result
    ), patch.object(
        sync_core, "reconcile_cargo_costs", side_effect=RuntimeError("Trendyol API 500")
    ):
        # İstisna dışarı sızmamalı -- _run_full_sync kendi içinde yakalayıp
        # fail_sync_progress yerine finish_sync_progress ile (uyarı notuyla)
        # tamamlanmalı.
        sync_core._run_full_sync(
            datetime(2026, 9, 1), datetime(2026, 9, 22), incremental_ok=False
        )

    with sync_core.get_connection() as conn:
        row = conn.execute(
            "SELECT status, message FROM sync_progress WHERE id=1"
        ).fetchone()
    assert row["status"] == "done"
    assert "kargo mutabakatı" in row["message"].lower() or "reconcil" in row["message"].lower()
