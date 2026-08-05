// =====================================================================
// AYARLAR — Pazaryeri Resmi Ödeme Verisi Kimlik Bilgileri
// Faz 7.2: payout-calendar.js'ten taşındı. Hakediş Takvimi'nin (Finans
// sayfası) kendisiyle hiçbir bağı yok, sadece aynı /api/payout-external-*
// uçlarını kullanıyor — bu yüzden bağımsız bir modül olarak burada duruyor.
// shell.js'in yüklenmiş olmasına gerek yok (kendi başına çalışır), ama
// zararı da olmaz.
// =====================================================================
(function () {
  if (!document.getElementById('pcExternalSync')) return; // bu sayfada değiliz

  async function pcCredSave(marketplace, inputId, statusId) {
    const statusEl = document.getElementById(statusId);
    const value = document.getElementById(inputId).value.trim();
    if (!value) {
      statusEl.textContent = 'Değer boş olamaz.';
      return;
    }
    statusEl.textContent = 'Kaydediliyor…';
    try {
      const res = await fetch('/api/payout-external-credential', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ marketplace: marketplace, value: value }),
      });
      const data = await res.json();
      if (data.error) {
        statusEl.textContent = 'Hata: ' + data.error;
        return;
      }
      statusEl.textContent = 'Kaydedildi ✓';
      document.getElementById(inputId).value = '';
      pcLoadExternalStatus();
    } catch (e) {
      statusEl.textContent = 'Hata: kaydedilemedi.';
    }
  }

  document.getElementById('pcCredSaveTrendyol').addEventListener('click', function () {
    pcCredSave('trendyol', 'pcCredTrendyol', 'pcCredStatusTrendyol');
  });
  document.getElementById('pcCredSaveHepsiburada').addEventListener('click', function () {
    pcCredSave('hepsiburada', 'pcCredHepsiburada', 'pcCredStatusHepsiburada');
  });

  async function pcLoadExternalStatus() {
    try {
      const res = await fetch('/api/payout-external-status');
      const data = await res.json();
      if (data.error) return;
      ['trendyol', 'hepsiburada'].forEach(function (mp) {
        const info = data[mp] || {};
        const statusId = mp === 'trendyol' ? 'pcCredStatusTrendyol' : 'pcCredStatusHepsiburada';
        const el = document.getElementById(statusId);
        if (!el) return;
        let parts = [];
        parts.push(info.configured ? 'kayıtlı (' + (info.updatedAt || '') + ')' : 'kayıtlı değil');
        if (info.lastSuccessAt) parts.push('son başarılı çekim: ' + info.lastSuccessAt);
        if (info.lastError) parts.push('son hata: ' + info.lastError);
        el.textContent = parts.join(' · ');
      });
    } catch (e) { /* sessiz geç, sayfa çalışmaya devam etsin */ }
  }

  document.getElementById('pcExternalSync').addEventListener('click', async function () {
    const statusEl = document.getElementById('pcExternalSyncStatus');
    statusEl.textContent = 'Çekiliyor…';
    try {
      const res = await fetch('/api/payout-external-sync', { method: 'POST' });
      const data = await res.json();
      if (data.error) {
        statusEl.textContent = 'Hata: ' + data.error;
        return;
      }
      statusEl.textContent = 'Tetiklendi (' + (data.mode || '') + ') — durum birazdan güncellenecek…';
      // NOT: Hakediş Takvimi ayrı bir sayfada (Finans) olduğu için buradan
      // onu yenileyemiyoruz — kullanıcı oraya gittiğinde zaten taze veri
      // çekiliyor (bkz. payout-calendar.js pcLoad).
      setTimeout(function () {
        pcLoadExternalStatus();
        statusEl.textContent = 'Tamamlandı.';
      }, data.mode === 'async' ? 4000 : 300);
    } catch (e) {
      statusEl.textContent = 'Hata: çekim tetiklenemedi.';
    }
  });

  pcLoadExternalStatus();
})();
