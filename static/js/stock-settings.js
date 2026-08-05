// =====================================================================
// ÜRÜN AYARLARI — Düşük Stok Uyarısı
// =====================================================================
(function () {
  let psAllItems = [];
  let psLoaded = false;

  async function psLoad() {
    const tbody = document.getElementById('psTableBody');
    tbody.innerHTML = skeletonTableRows(6, 6, [16, 22, 12, 14, 18, 10]);
    try {
      const res = await fetch('/api/product-stock');
      const data = await res.json();
      if (data.error) { tbody.innerHTML = errorStateRow(6, data.error); return; }
      psAllItems = data.items || [];
      psRender();
    } catch (e) {
      tbody.innerHTML = errorStateRow(6, e.message);
    }
  }

  function psRender() {
    const tbody = document.getElementById('psTableBody');
    const mpFilter = document.getElementById('psMarketplaceFilter').value;
    const onlyLow = document.getElementById('psOnlyLow').checked;

    let items = psAllItems;
    if (mpFilter) items = items.filter(function (it) { return it.marketplace === mpFilter; });
    if (onlyLow) items = items.filter(function (it) { return it.lowStock; });

    if (!items.length) {
      tbody.innerHTML = emptyStateRow(6, 'Kayıt bulunamadı. Önce stok senkronu çalıştırın.');
      return;
    }

    tbody.innerHTML = items.map(function (it) {
      const rowStyle = it.lowStock ? ' style="background:rgba(248,113,113,0.15);"' : '';
      return (
        '<tr data-marketplace="' + it.marketplace + '" data-sku="' + it.sku + '"' + rowStyle + '>' +
        '<td>' + (it.marketplace === 'trendyol' ? 'Trendyol' : 'Hepsiburada') + '</td>' +
        '<td>' + it.sku + '</td>' +
        '<td>' + (it.quantity ?? '—') + '</td>' +
        '<td><input type="number" class="ps-threshold" value="' + (it.min_stock_threshold ?? '') + '" style="width:70px; color:var(--text-main); background:var(--surface-2); border:1px solid var(--border);"></td>' +
        '<td>' + (it.stock_updated_at || '—') + '</td>' +
        '<td><button class="inline-save-btn ps-save-btn">Kaydet</button></td>' +
        '</tr>'
      );
    }).join('');

    tbody.querySelectorAll('.ps-save-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const tr = btn.closest('tr');
        const payload = {
          marketplace: tr.getAttribute('data-marketplace'),
          sku: tr.getAttribute('data-sku'),
          min_stock_threshold: tr.querySelector('.ps-threshold').value,
        };
        btn.textContent = 'Kaydediliyor…';
        fetch('/api/product-stock-threshold', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
          .then(function (r) { return r.json(); })
          .then(function () { btn.textContent = 'Kaydedildi ✓'; psLoad(); })
          .catch(function () { btn.textContent = 'Hata'; });
      });
    });
  }

  function psSync(marketplace, btn) {
    const statusEl = document.getElementById('psStatus');
    btn.disabled = true;
    statusEl.textContent = (marketplace === 'trendyol' ? 'Trendyol' : 'Hepsiburada') + ' stoğu çekiliyor…';
    fetch('/api/stock-sync/' + marketplace, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          statusEl.textContent = 'Hata: ' + data.error;
        } else {
          statusEl.textContent = data.synced + ' ürün güncellendi.';
          psLoad();
        }
      })
      .catch(function () { statusEl.textContent = 'Senkron sırasında hata oluştu.'; })
      .finally(function () { btn.disabled = false; });
  }

  document.getElementById('psSyncTrendyol').addEventListener('click', function () {
    psSync('trendyol', this);
  });
  document.getElementById('psSyncHB').addEventListener('click', function () {
    psSync('hepsiburada', this);
  });
  document.getElementById('psMarketplaceFilter').addEventListener('change', psRender);
  document.getElementById('psOnlyLow').addEventListener('change', psRender);

  // Faz 5: bu bölüm artık kendi sayfası (Stok) — accordion'a bağlı 'toggle'
  // tetiklemesi yerine, sayfa yüklenince doğrudan bir kez veri çekiyoruz.
  if (document.getElementById('details-product-settings') && !psLoaded) {
    psLoaded = true;
    psLoad();
  }
})();
