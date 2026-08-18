// =====================================================================
// SİPARİŞLER — Sipariş listesi, arama, durum filtresi, sayfalama
// Faz 7.1: dashboard.js'ten ayrıştırıldı, yalnızca bu sayfada yüklenir.
// shell.js'teki ortak yardımcılara bağımlıdır.
// =====================================================================
(function () {
  if (!document.getElementById('orders-table-wrap')) return; // bu sayfada değiliz

  let currentPage = 1;
  const PAGE_SIZE = 50;
  const ORDER_COLUMN_COUNT = 9; // ▸, Tarih, Sipariş No, Pazaryeri, Müşteri, Durum, Kargo, Net Tutar, Net Kâr

  function marketplaceBadge(marketplace) {
    // Faz 8.4: eskiden burada legacy .mp-badge/.mp-trendyol/.mp-hepsiburada
    // (ham hex renkli, legacy.css) kullanılıyordu — ama components.css'te
    // aynı işi gören, token'lara bağlı .lal-badge-mp-trendyol/hepsiburada
    // zaten Faz 2'den beri tanımlıydı ve hiç kullanılmıyordu. İki paralel
    // sistem oluşmuştu; burada LDL'nin kendi component'ine geçildi.
    const key = (marketplace || '').toLowerCase();
    if (key === 'trendyol') return '<span class="lal-badge lal-badge-mp-trendyol">Trendyol</span>';
    if (key === 'hepsiburada') return '<span class="lal-badge lal-badge-mp-hepsiburada">Hepsiburada</span>';
    return `<span class="lal-badge lal-badge-soon">${marketplace || '—'}</span>`;
  }

  function statusPill(status) {
    const map = {
      Delivered: ['pill-delivered', 'Teslim Edildi'],
      Cancelled: ['pill-cancelled', 'İptal'],
      UnSupplied: ['pill-cancelled', 'Karşılanamadı'],
    };
    const [cls, label] = map[status] || ['pill-other', status || '—'];
    return `<span class="pill ${cls}">${label}</span>`;
  }

  // DÜZELTME (09.08.2026): sipariş bazlı Net Kâr hücresi.
  // - netProfit null ise (ör. ürün maliyeti tanımlı değil) '—' gösterilir,
  //   sessizce 0 gibi davranıp yanıltmaz (bkz. order_routes.py profitMissingCost).
  // - profitEstimated true ise (gerçek settlement henüz oluşmamış, tahmini
  //   komisyonla hesaplanmış) rakamın yanına '~' ön eki ve bir tooltip eklenir —
  //   dashboard'daki "tahmini komisyonla hesaplandı" uyarısıyla tutarlı.
  function netProfitCell(o) {
    if (o.netProfit === null || o.netProfit === undefined) {
      return '<span style="color:var(--text-muted);" title="Bu sipariş için ürün maliyeti tanımlı değil, kâr hesaplanamıyor.">—</span>';
    }
    const cls = o.netProfit > 0 ? 'lal-profit-pos' : (o.netProfit < 0 ? 'lal-profit-neg' : '');
    const prefix = o.profitEstimated ? '~' : '';
    const title = o.profitEstimated
      ? 'Tahmini: bu sipariş için Finans API\'de henüz gerçek settlement kaydı yok, satır fiyatı ve tahmini komisyon oranıyla hesaplandı.'
      : 'Gerçek: Finans API settlement kaydına göre hesaplandı.';
    return `<span class="${cls}" title="${title}">${prefix}${fmtTL2(o.netProfit)}</span>`;
  }

  function renderOrderLines(lines) {
    if (!lines || !lines.length) return '<div style="color:var(--text-muted); font-size:12.5px;">Satır detayı yok.</div>';
    return `
      <table>
        <thead><tr><th>SKU</th><th>Ürün</th><th>Adet</th><th>Birim Fiyat</th><th>Komisyon %</th></tr></thead>
        <tbody>
          ${lines.map(ln => `
            <tr>
              <td>${ln.merchantSku || '—'}</td>
              <td>${ln.productName || '—'}</td>
              <td>${ln.quantity ?? '—'}</td>
              <td>${fmtTL2(ln.lineUnitPrice)}</td>
              <td>${ln.commissionRate ?? '—'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderOrders(orders) {
    const tbody = document.getElementById('orders-tbody');
    if (!orders.length) {
      tbody.innerHTML = emptyStateRow(ORDER_COLUMN_COUNT, 'Bu aralıkta sipariş bulunamadı.');
      return;
    }
    tbody.innerHTML = orders.map((o, i) => `
      <tr class="order-row" data-idx="${i}">
        <td>▸</td>
        <td>${fmtDateTime(o.orderDate)}</td>
        <td>${o.orderNumber || '—'}</td>
        <td>${marketplaceBadge(o.marketplace)}</td>
        <td>${o.customer || '—'}</td>
        <td>${statusPill(o.status)}</td>
        <td>${o.cargoProvider || '—'}</td>
        <td>${fmtTL2(o.netAmount)}</td>
        <td>${netProfitCell(o)}</td>
      </tr>
      <tr class="detail-row" id="detail-${i}">
        <td colspan="${ORDER_COLUMN_COUNT}"><div class="detail-inner">${renderOrderLines(o.lines)}</div></td>
      </tr>
    `).join('');

    tbody.querySelectorAll('tr.order-row').forEach(row => {
      row.addEventListener('click', () => {
        const idx = row.dataset.idx;
        const detail = document.getElementById(`detail-${idx}`);
        const arrow = row.querySelector('td:first-child');
        detail.classList.toggle('open');
        arrow.textContent = detail.classList.contains('open') ? '▾' : '▸';
      });
    });
  }

  function renderPagination(total, page, pageSize) {
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const el = document.getElementById('pagination');
    if (total <= pageSize) { el.classList.add('is-hidden'); return; }
    el.classList.remove('is-hidden');
    document.getElementById('page-info').textContent = `Sayfa ${page} / ${totalPages}`;
    document.getElementById('prev-btn').disabled = page <= 1;
    document.getElementById('next-btn').disabled = page >= totalPages;
  }

  async function loadOrders(page) {
    currentPage = page || 1;
    // Faz 8.1: "Yükleniyor…" düz metni yerine tabloyu görünür tutup içine
    // gerçek satırların şekline yakın iskelet (skeleton) satırlar koyuyoruz.
    safeDisplay('orders-loading', 'none');
    safeDisplay('orders-table-wrap', 'block');
    document.getElementById('orders-tbody').innerHTML = skeletonTableRows(ORDER_COLUMN_COUNT, 6, [4, 16, 14, 10, 14, 10, 12, 10, 10]);
    document.getElementById('pagination').classList.add('is-hidden');
    hideError();

    const status = document.getElementById('status-select').value;
    const q = document.getElementById('search-input').value.trim();
    const params = new URLSearchParams();
    rangeQueryParam().split('&').forEach(p => { const [k, v] = p.split('='); params.set(k, v); });
    params.set('page', currentPage);
    params.set('page_size', PAGE_SIZE);
    if (status) params.set('status', status);
    if (q) params.set('q', q);

    try {
      const res = await fetch(`/api/orders?${params.toString()}`);
      const data = await res.json();
      if (data.error) {
        showError(data.error);
        document.getElementById('orders-tbody').innerHTML = errorStateRow(ORDER_COLUMN_COUNT, data.error);
        return;
      }
      renderOrders(data.orders);
      document.getElementById('result-note').textContent = `${data.start_date} — ${data.end_date} arası, toplam ${data.total} sipariş bulundu.`;
      renderPagination(data.total, data.page, data.page_size);
    } catch (e) {
      showError('Beklenmeyen bir hata oluştu: ' + e.message);
      document.getElementById('orders-tbody').innerHTML = errorStateRow(ORDER_COLUMN_COUNT, e.message);
    }
  }

  onExist('prev-btn', 'click', () => { if (currentPage > 1) loadOrders(currentPage - 1); });
  onExist('next-btn', 'click', () => loadOrders(currentPage + 1));
  onExist('status-select', 'change', () => loadOrders(1));
  onExist('export-btn', 'click', () => {
    const status = document.getElementById('status-select').value;
    const q = document.getElementById('search-input').value.trim();
    const params = new URLSearchParams();
    params.set('full_history', 'true');
    if (status) params.set('status', status);
    if (q) params.set('q', q);
    window.location.href = `/api/orders/export?${params.toString()}`;
  });

  let searchTimeout;
  onExist('search-input', 'input', () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => loadOrders(1), 400);
  });

  // ---------- Başlangıç ----------
  loadOrders(1);
  document.addEventListener('lal:data-refresh', () => loadOrders(1));
})();
