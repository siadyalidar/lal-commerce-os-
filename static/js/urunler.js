// =====================================================================
// ÜRÜNLER — Ürün Performansı & Maliyet Yönetimi + Fiyatlandırma Hesaplayıcısı
// Faz 7.1: dashboard.js'ten ayrıştırıldı, yalnızca bu sayfada yüklenir.
// shell.js'teki ortak yardımcılara (fmtTL, fmtNum, fmtPct, safeText,
// safeDisplay, onExist, showError, hideError, rangeQueryParam) bağımlıdır —
// bu dosyadan ÖNCE base.html'de yüklenmiş olmalı.
// =====================================================================
(function () {
  if (!document.getElementById('margins-tbody')) return; // bu sayfada değiliz

  const ICON_CHECK_SM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;width:13px;height:13px;"><polyline points="20 6 9 17 4 12"/></svg>';

  // ---------- Ürün Performansı & Maliyet Yönetimi (birleşik tablo) ----------
  let marginSortBy = 'profit';
  let marginSortOrder = 'desc';
  let marginItemsCache = [];

  // alerts.js artık /urunler?filter=negative|missing-cost|low-margin
  // linkine yönlendiriyor (tam sayfa navigasyonu) — bu yüzden filtre artık
  // URL'den okunuyor, aynı sayfa içi buton tıklamasından değil.
  let marginFilter = null; // null | 'negative' | 'missing-cost' | 'low-margin'
  (function readFilterFromUrl() {
    const params = new URLSearchParams(location.search);
    const f = params.get('filter');
    if (f === 'negative' || f === 'missing-cost' || f === 'low-margin') {
      marginFilter = f;
    }
  })();

  function filterLabel(filter) {
    return {
      negative: 'Zararına satılanlar',
      'missing-cost': 'Maliyeti eksik olanlar',
      'low-margin': 'Düşük marjlılar (< %10)',
    }[filter] || '';
  }

  function applyMarginFilter(items) {
    if (!marginFilter) return items;
    if (marginFilter === 'negative') return items.filter(i => i.hasCost && i.margin !== null && i.margin < 0);
    if (marginFilter === 'missing-cost') return items.filter(i => !i.hasCost);
    if (marginFilter === 'low-margin') return items.filter(i => i.hasCost && i.margin !== null && i.margin >= 0 && i.margin < 0.10);
    return items;
  }

  function renderMarginsTable(allItems) {
    const chipEl = document.getElementById('margins-filter-chip');
    if (marginFilter) {
      chipEl.classList.remove('is-hidden');
      chipEl.querySelector('.chip-label').textContent = filterLabel(marginFilter);
    } else {
      chipEl.classList.add('is-hidden');
    }

    const items = applyMarginFilter(allItems);

    if (!items.length) {
      document.getElementById('margins-tbody').innerHTML = emptyStateRow(7, 'Bu filtreye uyan ürün yok.');
      return;
    }

    document.getElementById('margins-tbody').innerHTML = items.map(i => `
      <tr>
        <td>${i.sku || '–'}</td>
        <td>${i.productName || ''}</td>
        <td>${fmtNum(i.quantity)}</td>
        <td>${fmtTL(i.revenue)}</td>
        <td style="color:${(i.profit ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${fmtTL(i.profit)}</td>
        <td style="color:${i.margin !== null && i.margin < 0.10 ? 'var(--red)' : 'inherit'}">${fmtPct(i.margin)}</td>
        <td>${i.hasCost
          ? `<span class="pill pill-real" style="margin-right:6px;">Tanımlı</span>
             <input type="number" step="0.01" class="inline-cost-input" data-sku="${i.sku}" value="${i.costInclVat ?? ''}" style="width:90px;">
             <button class="inline-save-btn inline-cost-save" data-sku="${i.sku}">Güncelle</button>
             <button class="inline-save-btn inline-cost-delete" data-sku="${i.sku}" style="background:none; color:var(--red); border-color:var(--red);">Sil</button>`
          : `<span class="pill pill-missing" style="margin-right:6px;">Eksik</span>
             <input type="number" step="0.01" class="inline-cost-input" data-sku="${i.sku}" placeholder="Maliyet">
             <button class="inline-save-btn inline-cost-save" data-sku="${i.sku}">Kaydet</button>`
        }</td>
      </tr>
    `).join('');

    document.querySelectorAll('.inline-cost-save').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sku = btn.dataset.sku;
        const input = document.querySelector(`.inline-cost-input[data-sku="${CSS.escape(sku)}"]`);
        const cost = parseFloat(input.value);
        if (!cost || cost <= 0) { input.style.borderColor = 'var(--red)'; return; }
        const originalLabel = btn.textContent;
        btn.disabled = true; btn.textContent = '…';
        try {
          const res = await fetch('/api/product-cost', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sku, cost_incl_vat: cost }),
          });
          const data = await res.json();
          if (!res.ok || data.error) throw new Error(data.error || `Sunucu hatası (${res.status})`);
          loadMargins();
        } catch (e) {
          showError('Maliyet kaydedilemedi: ' + e.message);
          btn.disabled = false; btn.textContent = originalLabel;
        }
      });
    });

    document.querySelectorAll('.inline-cost-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sku = btn.dataset.sku;
        if (!confirm(`"${sku}" için tanımlı maliyeti silmek istediğine emin misin?`)) return;
        btn.disabled = true; btn.textContent = '…';
        try {
          await fetch(`/api/product-cost/${encodeURIComponent(sku)}`, { method: 'DELETE' });
          loadMargins();
        } catch (e) {
          showError('Maliyet silinemedi: ' + e.message);
          btn.disabled = false; btn.textContent = 'Sil';
        }
      });
    });
  }

  async function loadMargins() {
    hideError();
    // Faz 8.1: skeleton — tablo görünür kalıyor, içine gerçek satırların
    // şekline yakın placeholder'lar konuyor (metin "Yükleniyor…" yerine).
    document.getElementById('margins-tbody').innerHTML = skeletonTableRows(7, 6, [14, 30, 8, 12, 12, 10, 20]);
    safeDisplay('margins-loading', 'none');
    safeDisplay('margins-table-wrap', 'block');
    try {
      const rangeParam = rangeQueryParam();
      const res = await fetch(`/api/product-performance?${rangeParam}&sort_by=${marginSortBy}&order=${marginSortOrder}&limit=500`);
      const data = await res.json();
      if (data.error) {
        showError(data.error);
        document.getElementById('margins-tbody').innerHTML = errorStateRow(7, data.error);
        return;
      }
      marginItemsCache = data.items || [];
      renderMarginsTable(marginItemsCache);
    } catch (e) {
      showError('Ürün performansı alınamadı: ' + e.message);
      document.getElementById('margins-tbody').innerHTML = errorStateRow(7, e.message);
    }
  }

  onExist('margins-filter-clear', 'click', () => {
    marginFilter = null;
    renderMarginsTable(marginItemsCache);
    // URL'i de temizle ki sayfa yenilenince filtre geri gelmesin.
    const url = new URL(location.href);
    url.searchParams.delete('filter');
    history.replaceState(null, '', url);
  });

  document.querySelectorAll('#margins-table-wrap th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const field = th.dataset.sort;
      if (marginSortBy === field) {
        marginSortOrder = marginSortOrder === 'desc' ? 'asc' : 'desc';
      } else {
        marginSortBy = field;
        marginSortOrder = 'desc';
      }
      loadMargins();
    });
  });

  // ---------- "+ SKU ekle" formu ----------
  onExist('cf-toggle', 'click', () => {
    const form = document.getElementById('cf-form');
    const isOpen = !form.classList.contains('is-hidden');
    form.classList.toggle('is-hidden', isOpen);
    document.getElementById('cf-toggle').textContent = isOpen
      ? '+ Tabloda görünmeyen bir SKU için maliyet ekle'
      : '− Formu kapat';
  });

  onExist('cf-submit', 'click', async () => {
    const sku = document.getElementById('cf-sku').value.trim();
    const name = document.getElementById('cf-name').value.trim();
    const cost = document.getElementById('cf-cost').value;
    const msgEl = document.getElementById('cost-form-msg');

    if (!sku || !cost) {
      msgEl.classList.remove('is-hidden');
      msgEl.style.color = 'var(--red)';
      msgEl.textContent = 'SKU ve maliyet alanları zorunlu.';
      return;
    }

    try {
      const res = await fetch('/api/product-cost', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sku, product_name: name || undefined, cost_incl_vat: parseFloat(cost) }),
      });
      const data = await res.json();
      msgEl.classList.remove('is-hidden');
      if (data.error) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = data.error;
      } else {
        msgEl.style.color = 'var(--green)';
        msgEl.innerHTML = `${ICON_CHECK_SM}<span style="vertical-align:middle; margin-left:5px;">"${sku}" için maliyet kaydedildi.</span>`;
        document.getElementById('cf-sku').value = '';
        document.getElementById('cf-name').value = '';
        document.getElementById('cf-cost').value = '';
        const scrollBefore = window.scrollY;
        await loadMargins();
        window.scrollTo(0, scrollBefore);
        loadPricingSkuOptions();
      }
    } catch (e) {
      msgEl.classList.remove('is-hidden');
      msgEl.style.color = 'var(--red)';
      msgEl.textContent = 'Kaydetme hatası: ' + e.message;
    }
  });

  // ---------- Fiyatlandırma Hesaplayıcısı ----------
  let pricingItemsCache = [];

  async function loadPricingSkuOptions() {
    const select = document.getElementById('pc-sku-select');
    if (!select) return;
    try {
      const res = await fetch('/api/product-performance?full_history=true&limit=1000');
      const data = await res.json();
      pricingItemsCache = data.items || [];
      select.innerHTML = '<option value="">— seç —</option>' + pricingItemsCache
        .map(i => `<option value="${i.sku}">${i.sku} — ${(i.productName || '').slice(0, 40)}</option>`)
        .join('');
    } catch (e) { /* sessiz geç */ }
  }

  onExist('pc-sku-select', 'change', async (e) => {
    const sku = e.target.value;
    if (!sku) return;
    const item = pricingItemsCache.find(i => i.sku === sku);
    if (item && item.avgCommissionRate !== null) {
      document.getElementById('pc-commission').value = item.avgCommissionRate;
    }
    try {
      const res = await fetch('/api/product-costs');
      const data = await res.json();
      const costRow = (data.items || []).find(c => c.sku === sku);
      if (costRow) document.getElementById('pc-cost').value = costRow.cost_incl_vat;
    } catch (e) { /* sessiz geç */ }
  });

  onExist('pc-calc', 'click', () => {
    const cost = parseFloat(document.getElementById('pc-cost').value);
    const commission = parseFloat(document.getElementById('pc-commission').value) || 0;
    const targetMargin = parseFloat(document.getElementById('pc-margin').value) || 0;
    const resultEl = document.getElementById('pc-result');

    if (!cost || cost <= 0) {
      resultEl.classList.remove('is-hidden');
      resultEl.style.background = 'rgba(240,102,90,0.12)';
      resultEl.style.borderColor = 'rgba(240,102,90,0.3)';
      resultEl.innerHTML = '<span style="color:#FF9188;">Geçerli bir maliyet girin.</span>';
      return;
    }

    const denom = 1 - (commission / 100) - (targetMargin / 100);
    resultEl.classList.remove('is-hidden');

    if (denom <= 0) {
      resultEl.style.background = 'rgba(240,102,90,0.12)';
      resultEl.style.borderColor = 'rgba(240,102,90,0.3)';
      resultEl.innerHTML = '<span style="color:#FF9188;">Komisyon + hedef marj toplamı %100\'ü geçemez — bu kombinasyonla hiçbir fiyat hedefe ulaşamaz. Hedef marjı veya komisyonu düşürün.</span>';
      return;
    }

    const suggestedPrice = cost / denom;
    const commissionAmount = suggestedPrice * (commission / 100);
    const netAfterCommission = suggestedPrice - commissionAmount;
    const profit = netAfterCommission - cost;

    resultEl.style.background = 'rgba(52,211,153,0.12)';
    resultEl.style.borderColor = 'rgba(52,211,153,0.3)';
    resultEl.innerHTML = `
      <div style="font-size:13px; color:var(--text-muted); margin-bottom:6px;">Önerilen Satış Fiyatı (KDV Dahil)</div>
      <div style="font-size:26px; font-weight:700; color:var(--green); margin-bottom:12px;">${fmtTL2(suggestedPrice)}</div>
      <div style="font-size:13px; color:var(--text-muted); line-height:1.7;">
        Tahmini komisyon (%${commission}): ${fmtTL2(commissionAmount)}<br>
        Komisyon sonrası net: ${fmtTL2(netAfterCommission)}<br>
        Ürün maliyeti: ${fmtTL2(cost)}<br>
        Tahmini kâr: <strong style="color:var(--green);">${fmtTL2(profit)}</strong> (satış fiyatının %${targetMargin}'i)
      </div>
      <div style="font-size:11.5px; color:var(--text-muted); margin-top:10px;">
        <strong>Not:</strong> Bu hesap kargo, stopaj, erken ödeme kesintisi gibi diğer maliyetleri içermez — kaba bir tahmindir.
      </div>
    `;
  });

  // ---------- Başlangıç ----------
  loadMargins();
  loadPricingSkuOptions();
  document.addEventListener('lal:data-refresh', loadMargins);
})();
