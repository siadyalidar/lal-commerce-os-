#!/bin/bash
# LAL Commerce OS — Finansal Performans grafigi (final tasarim)
# Repo kok dizininde calistir: bash uygula_grafik_final.sh
set -e

mkdir -p "$(dirname "static/js/gosterge-paneli.js")"
cat > "static/js/gosterge-paneli.js" << 'MPC_EOF_MARKER'
// =====================================================================
// GÖSTERGE PANELİ — yalnızca bu sayfada yüklenir (Faz 7)
// =====================================================================
(function () {
  if (!document.getElementById('net-profit-hero')) return; // bu sayfada değiliz

  // ---------- KPI'lar + Sipariş Satırı Detayı ----------
  let firstLoadDone = false;
  let linesCache = [];
  const LINES_THEAD_STANDARD = '<th>Tarih</th><th>Sipariş No</th><th>SKU</th><th>Ürün</th><th>Adet</th><th>Ciro</th><th>Komisyon</th><th>Hizmet Bedeli</th><th>Net Hakediş</th><th>Kargo</th><th>Maliyet</th><th>Kâr</th><th>Durum</th>';

  function renderProfitSummary(t) {
    document.getElementById('stat-gross-revenue').textContent = fmtTL(t.gross_revenue);
    document.getElementById('stat-revenue').textContent = fmtTL(t.revenue);
    document.getElementById('stat-gross-profit').textContent = fmtTL(t.gross_profit);
    document.getElementById('stat-commission').textContent = fmtTL(t.commission);
    document.getElementById('stat-service-fee').textContent = fmtTL(t.service_fee);
    document.getElementById('stat-stoppage').textContent = fmtTL(t.stoppage);
    document.getElementById('stat-platform-fee').textContent = fmtTL(t.platform_service_fee);
    document.getElementById('stat-cash-advance').textContent = fmtTL(t.cash_advance_cost);
    const netEl = document.getElementById('stat-net-profit');
    netEl.textContent = fmtTL(t.net_profit);
    netEl.className = 'hero-stat-value ' + (t.net_profit >= 0 ? 'green' : 'red');
    document.getElementById('net-profit-hero').classList.toggle('negative', t.net_profit < 0);
    document.getElementById('stat-return').textContent = fmtTL(t.return_amount);
    document.getElementById('stat-return-count').textContent = t.return_count ? `${fmtNum(t.return_count)} iade işlemi` : '';
    document.getElementById('stat-payment-order').textContent = fmtTL(t.payment_order_net);
    const avgMargin = t.gross_revenue ? (t.net_profit / t.gross_revenue) : null;
    document.getElementById('stat-avg-margin').textContent = fmtPct(avgMargin);
  }

  function applyLinesFilter() {
    const onlyEstimated = document.getElementById('lines-estimated-only').checked;
    let rows = onlyEstimated ? linesCache.filter(l => l.estimated) : linesCache;
    rows = [...rows].sort((a, b) => {
      if (a.estimated !== b.estimated) return a.estimated ? -1 : 1;
      return (b.orderDate || 0) - (a.orderDate || 0);
    });

    document.getElementById('lines-body').innerHTML = rows.slice(0, 300).map(l => {
      let pill = l.estimated ? '<span class="pill pill-estimated">Tahmini</span>' : '<span class="pill pill-real">Gerçek</span>';
      if (l.missingCost) pill += ' <span class="pill pill-missing">Maliyet eksik</span>';
      return `
        <tr>
          <td>${l.orderDate ? new Date(l.orderDate).toLocaleDateString('tr-TR') : '—'}</td>
          <td>${l.orderNumber || ''}</td>
          <td>${l.sku || '–'}</td>
          <td>${l.productName || ''}</td>
          <td>${fmtNum(l.quantity)}</td>
          <td>${fmtTL(l.grossRevenue)}</td>
          <td>${fmtTL(l.commission)}</td>
          <td>${fmtTL(l.serviceFee)}</td>
          <td>${fmtTL(l.netHakedis)}</td>
          <td>${fmtTL(l.cargo)}</td>
          <td>${l.cogs === null ? '–' : fmtTL(l.cogs)}</td>
          <td style="color:${(l.profit ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'}">${l.profit === null ? '–' : fmtTL(l.profit)}</td>
          <td>${pill}</td>
        </tr>
      `;
    }).join('');

    if (onlyEstimated && !rows.length) {
      document.getElementById('lines-body').innerHTML = emptyStateRow(13, 'Şu an tahmini (settlement bekleyen) satır yok.');
    }
  }
  onExist('lines-estimated-only', 'change', applyLinesFilter);

  function renderLines(lines) {
    linesCache = lines;
    document.getElementById('lines-thead-row').innerHTML = LINES_THEAD_STANDARD;
    applyLinesFilter();
  }

  async function loadSummary() {
    if (!firstLoadDone) { safeDisplay('summary-loading', 'block'); safeDisplay('summary-content', 'none'); }
    hideError();
    try {
      const rangeParam = rangeQueryParam();
      const [summaryRes, orderCountRes] = await Promise.all([
        fetch(`/api/dashboard-summary?${rangeParam}`),
        fetch(`/api/orders?${rangeParam}&page=1&page_size=1`),
      ]);
      const summary = await summaryRes.json();
      try {
        const orderCountData = await orderCountRes.json();
        safeText('stat-orders', orderCountData.error ? '—' : fmtNum(orderCountData.total));
      } catch (e) { safeText('stat-orders', '—'); }

      if (summary.error) { showError(summary.error); safeDisplay('summary-loading', 'none'); return; }

      renderProfitSummary(summary.totals);
      renderLines(summary.lines || summary.orders || []);
      loadMonthlyProfitChart();

      safeDisplay('summary-loading', 'none');
      safeDisplay('summary-content', 'block');
      firstLoadDone = true;
    } catch (e) {
      safeDisplay('summary-loading', 'none');
      showError('Beklenmeyen bir hata oluştu: ' + e.message);
    }
    loadTodayOrderCount();
  }

  async function loadTodayOrderCount() {
    const el = document.getElementById('today-order-count');
    try {
      const res = await fetch('/api/today-order-count');
      const data = await res.json();
      el.textContent = data.error ? '—' : fmtNum(data.count);
    } catch (e) { el.textContent = '—'; }
  }

  // ---------- Aylık Kâr Trendi ----------
  let monthlyProfitChart, monthlyProfitLoaded = false;
  let monthlyProfitMonths = [];
  const monthlyProfitTlFmt = new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY', maximumFractionDigits: 0 });

  // LAL token'larını canlı okuyan yardımcı — hard-coded hex yerine tema neyse onu döner.
  function lalToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // Görsel hiyerarşi (final karar): Ciro nötr/en ince — Brüt Kâr ikincil —
  // Net Kâr birincil pozitif vurgu — Gerçek Net Kâr en güçlü vurgu.
  //
  // NOT / TEŞHİS: --lal-accent bu tema sisteminde kırmızı/bordo bir tondur
  // (#c12c3e koyu, #a92639 açık tema — bkz. tokens.css / precision-theme.css).
  // Brief'te "Gerçek Net Kâr pozitif bir sonuçsa KIRMIZI KULLANMA" kuralı var.
  // Bu nedenle en güçlü vurgu için --lal-accent KULLANILMADI — yerine LAL'de
  // zaten "en yüksek kontrastlı temel değer" anlamında kullanılan
  // --lal-text-main tercih edildi (bkz. .card .value, .lal-kpi-value aynı
  // token'ı kullanıyor). Ayırt edicilik renk dışında da sağlanıyor: en kalın
  // çizgi + kesikli çizgi + eşkenar dörtgen (rectRot) nokta.
  function monthlyProfitMetrics() {
    return [
      { key: 'revenue', label: 'Ciro', width: 1.25, dash: [], pointStyle: 'circle', color: () => lalToken('--lal-text-faint') },
      { key: 'grossProfit', label: 'Brüt Kâr', width: 1.75, dash: [], pointStyle: 'circle', color: () => lalToken('--lal-text-muted') },
      { key: 'netProfit', label: 'Net Kâr', width: 2, dash: [], pointStyle: 'circle', color: () => lalToken('--lal-green') },
      { key: 'realNetProfit', label: 'Gerçek Net Kâr', width: 2.5, dash: [6, 4], pointStyle: 'rectRot', color: () => lalToken('--lal-text-main') },
    ];
  }

  // Aktif noktada dikey rehber çizgi — sadece bu grafiğe özel plugin olarak
  // geçiliyor (Chart.register değil), diğer grafikleri etkilemez.
  const monthlyProfitGuidePlugin = {
    id: 'mpcGuide',
    afterDraw(chart) {
      const active = chart.tooltip && chart.tooltip._active;
      if (!active || !active.length) return;
      const x = active[0].element.x;
      const { top, bottom } = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.lineWidth = 1;
      ctx.strokeStyle = lalToken('--lal-border');
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.restore();
    },
  };

  function monthlyProfitTooltipEl(chart) {
    const wrap = chart.canvas.closest('.mpc-chart-wrap');
    let el = wrap.querySelector('.mpc-tooltip');
    if (!el) {
      el = document.createElement('div');
      el.className = 'mpc-tooltip';
      wrap.appendChild(el);
    }
    return el;
  }

  // Grafik üzerinde kalıcı rakam bırakmıyoruz (brief madde 8) — tüm ay
  // detayı sadece hover'da, mevcut LAL panel/kart stiliyle uyumlu bu HTML
  // tooltip içinde gösteriliyor.
  function monthlyProfitExternalTooltip(context) {
    const { chart, tooltip } = context;
    const el = monthlyProfitTooltipEl(chart);
    if (!tooltip || tooltip.opacity === 0 || !tooltip.dataPoints || !tooltip.dataPoints.length) {
      el.classList.remove('is-visible');
      return;
    }
    const idx = tooltip.dataPoints[0].dataIndex;
    const m = monthlyProfitMonths[idx];
    if (!m) { el.classList.remove('is-visible'); return; }

    const rev = m.revenue || 0;
    const gross = m.grossProfit || 0;
    const net = m.netProfit || 0;
    const real = (m.realNetProfit ?? m.netProfit) || 0;
    const rows = [
      { label: 'Ciro', value: rev, color: lalToken('--lal-text-faint'), diamond: false },
      { label: 'Brüt Kâr', value: gross, color: lalToken('--lal-text-muted'), diamond: false },
      { label: 'Net Kâr', value: net, color: lalToken('--lal-green'), diamond: false },
      { label: 'Gerçek Net Kâr', value: real, color: lalToken('--lal-text-main'), diamond: true },
    ];
    const marginLine = (label, value) => rev
      ? `<div class="mpc-tooltip-margin"><span>${label}</span><span>%${((value / rev) * 100).toFixed(1)}</span></div>` : '';

    el.innerHTML = `
      <div class="mpc-tooltip-title">${chart.data.labels[idx]}</div>
      ${rows.map(r => `
        <div class="mpc-tooltip-row">
          <span class="mpc-tooltip-label"><span class="mpc-tooltip-dot${r.diamond ? ' is-diamond' : ''}" style="background:${r.color}"></span>${r.label}</span>
          <span class="mpc-tooltip-value">${monthlyProfitTlFmt.format(r.value)}</span>
        </div>`).join('')}
      <div class="mpc-tooltip-divider"></div>
      <div class="mpc-tooltip-margins">
        ${marginLine('Net Marj', net)}
        ${marginLine('Gerçek Net Marj', real)}
      </div>`;

    el.style.left = tooltip.caretX + 'px';
    el.style.top = tooltip.caretY + 'px';
    el.classList.add('is-visible');

    // Kapsayıcı dışına taşmasın diye (mobil/dar ekran) — brief madde 14.
    requestAnimationFrame(() => {
      const wrap = chart.canvas.closest('.mpc-chart-wrap');
      if (!wrap) return;
      const wrapRect = wrap.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      let left = parseFloat(el.style.left);
      if (elRect.right > wrapRect.right) left -= (elRect.right - wrapRect.right) + 8;
      if (elRect.left < wrapRect.left) left += (wrapRect.left - elRect.left) + 8;
      el.style.left = left + 'px';
    });
  }

  // Klasik Chart.js legend yerine LAL "chip" stiliyle metrik seçici (brief
  // madde 10). Mevcut interaction modelini bozmamak için en düşük riskli
  // davranış: her chip ilgili seriyi aç/kapatır (Chart.js setDatasetVisibility).
  function renderMonthlyProfitSelector(metrics) {
    const container = document.getElementById('monthlyProfitMetricSelector');
    if (!container) return;
    container.innerHTML = metrics.map((m, i) => `
      <button type="button" class="mpc-chip is-active" data-idx="${i}" aria-pressed="true">
        <span class="mpc-chip-dot${m.pointStyle === 'rectRot' ? ' is-diamond' : ''}" style="background:${m.color()}"></span>${m.label}
      </button>`).join('');
    container.querySelectorAll('.mpc-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const idx = Number(chip.dataset.idx);
        const nowVisible = !monthlyProfitChart.isDatasetVisible(idx);
        monthlyProfitChart.setDatasetVisibility(idx, nowVisible);
        chip.classList.toggle('is-active', nowVisible);
        chip.classList.toggle('is-off', !nowVisible);
        chip.setAttribute('aria-pressed', String(nowVisible));
        monthlyProfitChart.update();
      });
    });
  }

  function renderMonthlyProfitInProgressBadge(months) {
    const badge = document.getElementById('monthlyProfitInProgressBadge');
    if (!badge || !months.length) { if (badge) badge.classList.add('is-hidden'); return; }
    const last = months[months.length - 1];
    const now = new Date();
    const currentKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    if (last.month === currentKey) {
      const [y, mo] = last.month.split('-');
      const label = new Date(y, mo - 1, 1).toLocaleDateString('tr-TR', { month: 'short', year: '2-digit' });
      badge.textContent = `${label} · devam ediyor`;
      badge.classList.remove('is-hidden');
    } else {
      badge.classList.add('is-hidden');
    }
  }

  async function loadMonthlyProfitChart() {
    if (monthlyProfitLoaded) return;
    try {
      const res = await fetch(`/api/monthly-profit?full_history=true&marketplace=${currentMarketplace}`);
      const data = await res.json();
      if (data.error) { showError(data.error); return; }
      document.getElementById('monthly-loading').style.display = 'none';
      document.getElementById('monthlyProfitChartWrap').style.display = 'block';
      renderMonthlyProfitChart(data.months || []);
      monthlyProfitLoaded = true;
      if (monthlyProfitChart) monthlyProfitChart.resize();
    } catch (e) {
      showError('Aylık kâr trendi alınamadı: ' + e.message);
    }
  }

  function renderMonthlyProfitChart(months) {
    monthlyProfitMonths = months;
    const labels = months.map(m => {
      const [y, mo] = m.month.split('-');
      return new Date(y, mo - 1, 1).toLocaleDateString('tr-TR', { month: 'short', year: '2-digit' });
    });
    const dataByKey = {
      revenue: months.map(m => m.revenue || 0),
      grossProfit: months.map(m => m.grossProfit || 0),
      netProfit: months.map(m => m.netProfit || 0),
      realNetProfit: months.map(m => (m.realNetProfit ?? m.netProfit) || 0),
    };
    renderMonthlyProfitInProgressBadge(months);

    if (monthlyProfitChart) {
      monthlyProfitChart.data.labels = labels;
      monthlyProfitMetrics().forEach((m, i) => { monthlyProfitChart.data.datasets[i].data = dataByKey[m.key]; });
      monthlyProfitChart.update();
      return;
    }

    const metrics = monthlyProfitMetrics();
    const ctx = document.getElementById('monthlyProfitChart').getContext('2d');

    const datasets = metrics.map((m) => {
      const color = m.color();
      return {
        type: 'line',
        label: m.label,
        data: dataByKey[m.key],
        borderColor: color,
        backgroundColor: color,
        fill: false,
        borderWidth: m.width,
        borderDash: m.dash,
        tension: 0.3,
        pointStyle: m.pointStyle,
        pointRadius: 2.5,
        pointHoverRadius: 5,
        pointBackgroundColor: color,
        pointBorderColor: lalToken('--lal-surface'),
        pointBorderWidth: 1.5,
        clip: false,
      };
    });

    monthlyProfitChart = new Chart(ctx, {
      data: { labels, datasets },
      options: {
        maintainAspectRatio: false,
        layout: { padding: { top: 12, right: 12, bottom: 4, left: 4 } },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false }, // Legend yerine mpc-selector chip'leri kullanılıyor.
          tooltip: { enabled: false, external: monthlyProfitExternalTooltip },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: lalToken('--lal-border-soft') },
            ticks: { font: { size: 11.5, family: "'Inter', sans-serif" }, color: lalToken('--lal-text-faint') },
          },
          // Tek eksen (brief final karar: dual-axis yok). Ciro/kâr ölçek
          // farkı, veri normalize edilerek değil; Ciro'nun en ince/en nötr
          // çizgi olarak düşük visual weight taşımasıyla ve metrik
          // seçiciyle istenen seriyi tek başına görebilme imkânıyla çözülüyor.
          y: {
            beginAtZero: true,
            grid: { color: lalToken('--lal-border-soft') },
            border: { display: false },
            ticks: { font: { size: 11, family: "'Inter', sans-serif" }, color: lalToken('--lal-text-faint'), maxTicksLimit: 6, callback: (v) => monthlyProfitTlFmt.format(v) },
          },
        },
      },
      plugins: [monthlyProfitGuidePlugin],
    });

    renderMonthlyProfitSelector(metrics);

    document.addEventListener('lal:theme-change', () => {
      if (!monthlyProfitChart) return;
      const freshMetrics = monthlyProfitMetrics();
      freshMetrics.forEach((m, i) => {
        const color = m.color();
        const ds = monthlyProfitChart.data.datasets[i];
        ds.borderColor = color;
        ds.backgroundColor = color;
        ds.pointBackgroundColor = color;
        ds.pointBorderColor = lalToken('--lal-surface');
      });
      monthlyProfitChart.options.scales.x.border.color = lalToken('--lal-border-soft');
      monthlyProfitChart.options.scales.x.ticks.color = lalToken('--lal-text-faint');
      monthlyProfitChart.options.scales.y.grid.color = lalToken('--lal-border-soft');
      monthlyProfitChart.options.scales.y.ticks.color = lalToken('--lal-text-faint');
      monthlyProfitChart.update();
      renderMonthlyProfitSelector(freshMetrics);
    });
  }
  window.__lal_refreshMonthlyProfitChart = function () { monthlyProfitLoaded = false; loadMonthlyProfitChart(); };

  // ---------- Detaylı Grafikler (günlük satış/sipariş/iade) ----------
  const darkChartDefaults = {
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: '#15171C', borderColor: '#3DDBD9', borderWidth: 1, padding: 12, cornerRadius: 10, titleColor: '#F2F3F5', titleFont: { size: 13, weight: '600' }, bodyColor: '#F2F3F5', bodyFont: { size: 13.5, weight: '600' }, displayColors: false }
    },
    scales: {
      x: { grid: { display: false }, border: { color: '#24272E' }, ticks: { color: '#5B5F68', font: { size: 11 } } },
      y: { beginAtZero: true, grid: { color: '#1B1E24' }, border: { display: false }, ticks: { color: '#5B5F68', font: { size: 11 } } },
    },
    interaction: { mode: 'index', intersect: false },
  };
  const tlFmtShort = new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY', maximumFractionDigits: 0 });
  let salesChart, ordersChart, returnsChart;

  function renderCharts(daily, returnsDaily) {
    const labels = daily.map(d => fmtDateShort(d.date));
    const netData = daily.map(d => d.net_amount);
    const orderData = daily.map(d => d.order_count);
    const returnLabels = returnsDaily.map(d => fmtDateShort(d.date));
    const returnCounts = returnsDaily.map(d => d.claim_count);

    const salesCtx = document.getElementById('salesChart').getContext('2d');
    const salesGrad = salesCtx.createLinearGradient(0, 0, 0, 260);
    salesGrad.addColorStop(0, 'rgba(61,219,217,0.4)'); salesGrad.addColorStop(1, 'rgba(61,219,217,0.0)');
    if (salesChart) salesChart.destroy();
    salesChart = new Chart(salesCtx, {
      type: 'line',
      data: { labels, datasets: [{ label: 'Net Satış (₺)', data: netData, borderColor: '#3DDBD9', backgroundColor: salesGrad, fill: true, tension: 0.4, cubicInterpolationMode: 'monotone', pointRadius: 0, pointHoverRadius: 6, pointHoverBackgroundColor: '#3DDBD9', pointHoverBorderColor: '#0A0B0D', pointHoverBorderWidth: 2, borderWidth: 2.5, borderCapStyle: 'round', borderJoinStyle: 'round' }] },
      options: { ...darkChartDefaults, scales: { ...darkChartDefaults.scales, y: { ...darkChartDefaults.scales.y, beginAtZero: true } }, plugins: { ...darkChartDefaults.plugins, tooltip: { ...darkChartDefaults.plugins.tooltip, callbacks: { label: (ctx) => `Net Satış: ${tlFmtShort.format(ctx.parsed.y)}` } } } }
    });

    const ordersCtx = document.getElementById('ordersChart').getContext('2d');
    const ordersGrad = ordersCtx.createLinearGradient(0, 0, 0, 260);
    ordersGrad.addColorStop(0, 'rgba(61,219,217,0.55)'); ordersGrad.addColorStop(1, 'rgba(61,219,217,0.15)');
    if (ordersChart) ordersChart.destroy();
    ordersChart = new Chart(ordersCtx, {
      type: 'bar',
      data: { labels, datasets: [{ label: 'Sipariş Adedi', data: orderData, backgroundColor: ordersGrad, borderRadius: 4, borderSkipped: false }] },
      options: { ...darkChartDefaults, scales: { ...darkChartDefaults.scales, y: { ...darkChartDefaults.scales.y, beginAtZero: true, ticks: { ...darkChartDefaults.scales.y.ticks, precision: 0 } } }, plugins: { ...darkChartDefaults.plugins, tooltip: { ...darkChartDefaults.plugins.tooltip, callbacks: { label: (ctx) => `Sipariş: ${ctx.parsed.y} adet` } } } }
    });

    const returnsCtx = document.getElementById('returnsChart').getContext('2d');
    const returnsGrad = returnsCtx.createLinearGradient(0, 0, 0, 260);
    returnsGrad.addColorStop(0, 'rgba(240,102,90,0.55)'); returnsGrad.addColorStop(1, 'rgba(240,102,90,0.15)');
    if (returnsChart) returnsChart.destroy();
    returnsChart = new Chart(returnsCtx, {
      type: 'bar',
      data: { labels: returnLabels, datasets: [{ label: 'İade Adedi', data: returnCounts, backgroundColor: returnsGrad, borderRadius: 4, borderSkipped: false }] },
      options: { ...darkChartDefaults, scales: { ...darkChartDefaults.scales, y: { ...darkChartDefaults.scales.y, beginAtZero: true, ticks: { ...darkChartDefaults.scales.y.ticks, precision: 0 } } }, plugins: { ...darkChartDefaults.plugins, tooltip: { ...darkChartDefaults.plugins.tooltip, callbacks: { label: (ctx) => `İade: ${ctx.parsed.y} adet` } } } }
    });
  }

  async function loadChartsSection() {
    document.getElementById('charts-loading').style.display = 'block';
    document.getElementById('charts-content').style.display = 'none';
    try {
      const rangeParam = rangeQueryParam();
      const [salesRes, returnsRes] = await Promise.all([
        fetch(`/api/daily-sales?${rangeParam}`),
        fetch(`/api/daily-returns?${rangeParam}`)
      ]);
      const salesData = await salesRes.json();
      const returnsData = await returnsRes.json();
      if (salesData.error) { showError(salesData.error); document.getElementById('charts-loading').style.display = 'none'; return; }
      document.getElementById('charts-loading').style.display = 'none';
      document.getElementById('charts-content').style.display = 'block';
      renderCharts(salesData.daily, returnsData.daily || []);
      [salesChart, ordersChart, returnsChart].forEach(c => c && c.resize());
      if (returnsData.error) showError('İade verileri alınamadı: ' + returnsData.error);
    } catch (e) {
      document.getElementById('charts-loading').style.display = 'none';
      showError('Beklenmeyen bir hata oluştu: ' + e.message);
    }
  }

  // ---------- Başlangıç ----------
  loadSummary();
  loadChartsSection();
  document.addEventListener('lal:data-refresh', function () {
    monthlyProfitLoaded = false;
    loadSummary();
    loadChartsSection();
  });

  // Uyarı bandındaki "settlement bekleyen satırlar" linkinden gelindiyse,
  // sayfa yüklenince otomatik olarak o filtreyi uygula ve tabloya kaydır.
  const params = new URLSearchParams(location.search);
  if (params.get('linesFilter') === 'estimated') {
    const tryApply = () => {
      const cb = document.getElementById('lines-estimated-only');
      if (cb && linesCache.length) {
        cb.checked = true;
        applyLinesFilter();
        document.getElementById('details-charts').scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else {
        setTimeout(tryApply, 300);
      }
    };
    tryApply();
  }
})();
MPC_EOF_MARKER

mkdir -p "$(dirname "static/js/shell.js")"
cat > "static/js/shell.js" << 'MPC_EOF_MARKER'
// =====================================================================
// LAL SHELL — tüm sayfalarda ortak çekirdek (Faz 7)
// =====================================================================
// Bu dosya base.html üzerinden HER sayfada yükleniyor. Sayfa-özel modüller
// (gosterge-paneli.js, urunler.js, siparisler.js, finans-giderler.js)
// kendi verilerini bu dosyanın tetiklediği 'lal:data-refresh' custom
// event'ini dinleyerek yeniliyor — böylece shell, hangi sayfada hangi
// modülün yüklü olduğunu bilmek zorunda kalmıyor (gevşek bağlı mimari).

// ---------- Ortak biçimlendirme yardımcıları ----------
const fmtTL = (n) => n === null || n === undefined ? '–' : new Intl.NumberFormat('tr-TR', {style:'currency', currency:'TRY', maximumFractionDigits: 0}).format(n);
const fmtTL2 = (n) => n === null || n === undefined ? '—' : '₺' + Number(n).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtNum = (n) => n === null || n === undefined ? '–' : new Intl.NumberFormat('tr-TR').format(n);
const fmtPct = (n) => n === null || n === undefined ? '–' : (n * 100).toFixed(1) + '%';
const fmtDateShort = (d) => new Date(d).toLocaleDateString('tr-TR', {day:'2-digit', month:'short'});
function fmtDateTime(ms) { if (!ms) return '—'; const d = new Date(ms); return d.toLocaleDateString('tr-TR') + ' ' + d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }); }

// Bir sayfada bulunmayan elementlere erişilmeye çalışıldığında hata
// fırlatmak yerine sessizce çıkmayı sağlayan güvenli erişim yardımcıları.
function safeText(id, val) { const el = document.getElementById(id); if (el) el.textContent = val; }
function safeDisplay(id, val) { const el = document.getElementById(id); if (el) el.style.display = val; }
function onExist(id, evt, fn) { const el = document.getElementById(id); if (el) el.addEventListener(evt, fn); }

// ---------- Tema tercihi ----------
// Aynı LAL kimliği koyu ve sıcak-açık yüzeyde çalışır; seçim cihazda saklanır.
function applyTheme(theme) {
  const isLight = theme === 'light';
  document.documentElement.dataset.lalTheme = isLight ? 'light' : 'dark';
  const btn = document.getElementById('lal-theme-toggle');
  if (btn) {
    btn.textContent = isLight ? 'Koyu tema' : 'Açık tema';
    btn.setAttribute('aria-pressed', String(isLight));
    btn.setAttribute('aria-label', isLight ? 'Koyu temaya geç' : 'Açık temaya geç');
  }
  // Canvas tabanlı grafikler CSS değişkenlerini otomatik izleyemez (renkler JS'e
  // string olarak geçiyor) — tema değiştiğinde ilgilenen component'ler bu event'i
  // dinleyip kendi renklerini yeniden okuyabilir. Dinleyici yoksa hiçbir etkisi yok.
  document.dispatchEvent(new CustomEvent('lal:theme-change', { detail: { theme: isLight ? 'light' : 'dark' } }));
}
applyTheme(localStorage.getItem('lal-theme') || 'dark');
onExist('lal-theme-toggle', 'click', () => {
  const next = document.documentElement.dataset.lalTheme === 'light' ? 'dark' : 'light';
  localStorage.setItem('lal-theme', next);
  applyTheme(next);
});

// ---------- Skeleton loading (Faz 8.1) ----------
// Tablo satırları veri gelene kadar "Yükleniyor…" düz metni yerine bu
// placeholder satırları gösteriyor. widths: her hücre için yaklaşık genişlik
// yüzdesi (gerçek içeriğin şekline benzesin diye) — verilmezse hepsi %70.
function skeletonTableRows(colCount, rowCount, widths) {
  rowCount = rowCount || 5;
  const cellWidths = widths && widths.length === colCount ? widths : Array(colCount).fill(70);
  let rows = '';
  for (let r = 0; r < rowCount; r++) {
    const cells = cellWidths.map(w => {
      // Satırdan satıra hafif genişlik varyasyonu — hepsi birebir aynı
      // görünmesin diye (gerçek veri hissi).
      const jitter = 8 - (r % 3) * 4;
      const pct = Math.max(30, Math.min(96, w - jitter));
      return `<td><span class="lal-skeleton lal-skeleton-line" style="width:${pct}%"></span></td>`;
    }).join('');
    rows += `<tr class="lal-skeleton-row">${cells}</tr>`;
  }
  return rows;
}

// ---------- Boş durum (empty state) — Faz 8.1 ----------
// Gerçek bir hata değil, sadece "bu filtreye/aralığa uyan veri yok"
// durumu için — sayfa üstündeki #error-banner ile karıştırılmamalı (o,
// istek başarısız olduğunda kullanılıyor, bu değişmedi).
const ICON_EMPTY_BOX = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>';
function emptyStateRow(colCount, message, title) {
  return `<tr><td colspan="${colCount}">
    <div class="lal-empty-state">
      ${ICON_EMPTY_BOX}
      ${title ? `<span class="lal-empty-state-title">${title}</span>` : ''}
      <span>${message}</span>
    </div>
  </td></tr>`;
}
const ICON_ERROR_TRIANGLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>';
function errorStateRow(colCount, message) {
  return `<tr><td colspan="${colCount}">
    <div class="lal-error-state">
      ${ICON_ERROR_TRIANGLE}
      <span class="lal-empty-state-title">Veri yüklenemedi</span>
      <span>${message}</span>
    </div>
  </td></tr>`;
}

function showError(msg) {
  const el = document.getElementById('error-banner');

  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
}
function hideError() { const el = document.getElementById('error-banner'); if (el) el.style.display = 'none'; }

// ---------- Global filtre durumu (marketplace + tarih aralığı) ----------
let currentMarketplace = 'all';

// "Bugün" için start_date=YYYY-MM-DD üretirken YEREL tarihi kullanıyoruz.
// toISOString() UTC'ye çevirir — Türkiye (UTC+3) için gece yarısından
// sonraki ilk birkaç saatte bir önceki günün tarihini döndürür, bu da
// backend'in "yerel saat, gece yarısı" varsayımıyla çelişir.
function localDateStr(d) {
  d = d || new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function rangeQueryParam() {
  const val = document.getElementById('range-select').value;
  let base;
  if (val === 'all') {
    base = 'full_history=true';
  } else if (val === 'today') {
    base = `start_date=${localDateStr()}`;
  } else {
    base = `days=${val}`;
  }
  return `${base}&marketplace=${currentMarketplace}`;
}

// Sayfa-özel modüllerin dinleyeceği tek olay: marketplace/tarih değişti
// veya bir senkronizasyon tamamlandı — "verini tazele" anlamına gelir.
function notifyDataRefresh() {
  document.dispatchEvent(new CustomEvent('lal:data-refresh'));
}

onExist('mp-switch', 'click', (e) => {
  const btn = e.target.closest('.mp-switch-btn');
  if (!btn || btn.classList.contains('active')) return;
  document.querySelectorAll('.mp-switch-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentMarketplace = btn.dataset.mp;
  notifyDataRefresh();
});

onExist('range-select', 'change', notifyDataRefresh);

// ---------- Config / bağlantı durumu ----------
async function checkConfig() {
  const res = await fetch('/api/config-status');
  const data = await res.json();
  if (!data.configured) {
    safeDisplay('config-warning', 'flex');
    safeText('env-label', 'Yapılandırma bekleniyor');
    return false;
  }
  safeDisplay('config-warning', 'none');
  safeText('env-label', `Ortam: ${data.env} · Satıcı ID: ${data.supplier_id}`);
  return true;
}

// ---------- Son senkronizasyon özeti (her zaman görünür, küçük) ----------
async function loadLastSyncInfo() {
  const el = document.getElementById('last-sync-label');
  if (!el) return;
  try {
    const res = await fetch('/api/sync-status?marketplace=trendyol');
    const data = await res.json();
    const ts = data.finishedAt || data.finished_at || data.lastSyncAt || data.last_sync_at
      || data.updatedAt || data.updated_at || data.timestamp || null;
    if (data.status === 'running') { el.style.display = 'none'; return; }
    if (ts) {
      const t = typeof ts === 'number' ? ts : Date.parse(ts);
      if (!isNaN(t)) {
        el.textContent = `Son senkronizasyon: ${fmtDateTime(t)}`;
        el.style.display = 'block';
        return;
      }
    }
    if (data.message && data.status && data.status !== 'idle') {
      el.textContent = `Son senkronizasyon: ${data.message}`;
      el.style.display = 'block';
      return;
    }
    el.style.display = 'none';
  } catch (e) {
    el.style.display = 'none';
  }
}

// ---------- Senkronizasyon ----------
async function runSync() {
  const btn = document.getElementById('sync-btn');
  const statusEl = document.getElementById('sync-status');
  const rangeParam = rangeQueryParam();
  const rangeLabel = document.getElementById('range-select').selectedOptions[0].textContent;

  btn.disabled = true;
  btn.textContent = 'Senkronize ediliyor…';
  statusEl.style.display = 'block';
  statusEl.textContent = `${rangeLabel} için siparişler ve Finans API verisi çekiliyor — bu birkaç dakika sürebilir…`;
  hideError();

  try {
    const res = await fetch(`/api/sync-finance?${rangeParam}`, { method: 'POST' });
    const data = await res.json();
    if (data.error) {
      showError(data.error);
      statusEl.style.display = 'none';
      btn.disabled = false;
      btn.textContent = 'Verileri Senkronize Et';
      return;
    }

    const hbActive = !!data.hepsiburada_started;

    while (true) {
      const [tyRes, hbRes] = await Promise.all([
        fetch('/api/sync-status?marketplace=trendyol'),
        hbActive ? fetch('/api/sync-status?marketplace=hepsiburada') : Promise.resolve(null),
      ]);
      const tyProgress = await tyRes.json();
      const hbProgress = hbActive ? await hbRes.json() : null;

      const tyRunning = tyProgress.status === 'running';
      const hbRunning = hbActive && hbProgress.status === 'running';

      const parts = [];
      if (tyProgress.message) parts.push(`Trendyol: ${tyProgress.message}`);
      if (hbProgress && hbProgress.message) parts.push(`Hepsiburada: ${hbProgress.message}`);
      statusEl.textContent = parts.join('  •  ') || 'Senkronize ediliyor…';

      if (!tyRunning && !hbRunning) {
        if (tyProgress.status === 'error') showError(`Trendyol: ${tyProgress.error || 'Senkronizasyon hatası.'}`);
        if (hbProgress && hbProgress.status === 'error') showError(`Hepsiburada: ${hbProgress.error || 'Senkronizasyon hatası.'}`);

        notifyDataRefresh();
        loadLastSyncInfo();
        break;
      }
      await new Promise(r => setTimeout(r, 1000));
    }
  } catch (e) {
    showError('Senkronizasyon hatası: ' + e.message);
    statusEl.style.display = 'none';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Verileri Senkronize Et';
  }
}
onExist('sync-btn', 'click', runSync);

// ---------- Başlangıç (her sayfada) ----------
checkConfig();
loadLastSyncInfo();
MPC_EOF_MARKER

mkdir -p "$(dirname "static/css/monthly-profit-chart.css")"
cat > "static/css/monthly-profit-chart.css" << 'MPC_EOF_MARKER'
/* ============================================================================
   AYLIK KÂR TRENDİ — grafik-özel stiller
   ----------------------------------------------------------------------------
   Sadece bu component'e ait. Tamamen tokens.css değişkenlerinden türetilmiştir;
   ham hex/px değeri barındırmaz (grafik legend nokta renkleri hariç — onlar
   JS'te aynı --lal-* token'larından hesaplanıp inline stil olarak yazılır).
   Dark/light farkı burada tanımlanmaz; tokens zaten html[data-lal-theme] ile
   değişir, bu dosya sadece o token'ları tüketir.
   ============================================================================ */

.mpc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--lal-space-4);
  flex-wrap: wrap;
  margin-bottom: var(--lal-space-4);
}
.mpc-head-right {
  display: flex;
  align-items: center;
  gap: var(--lal-space-3);
  flex-wrap: wrap;
}

/* ---------- Metrik seçici (klasik Chart.js legend'in yerini alır) ---------- */
.mpc-selector {
  display: flex;
  flex-wrap: wrap;
  gap: var(--lal-space-2);
}
.mpc-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 11px;
  border: 1px solid var(--lal-border-soft);
  border-radius: var(--lal-radius-full);
  background: transparent;
  color: var(--lal-text-muted);
  font: var(--lal-text-small);
  cursor: pointer;
  user-select: none;
  line-height: 1.2;
  transition: background var(--lal-dur-fast) var(--lal-ease-out),
              color var(--lal-dur-fast) var(--lal-ease-out),
              border-color var(--lal-dur-fast) var(--lal-ease-out),
              opacity var(--lal-dur-fast) var(--lal-ease-out);
}
.mpc-chip:hover { border-color: var(--lal-border); color: var(--lal-text-main); }
.mpc-chip.is-active { background: var(--lal-surface-2); color: var(--lal-text-main); border-color: var(--lal-border); }
.mpc-chip.is-off { opacity: .4; }
.mpc-chip-dot { width: 7px; height: 7px; border-radius: var(--lal-radius-full); flex-shrink: 0; }
.mpc-chip-dot.is-diamond { border-radius: 1px; transform: rotate(45deg); width: 6px; height: 6px; }

/* ---------- "Devam ediyor" rozeti (mevcut ay henüz tamamlanmadıysa) ---------- */
.mpc-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--lal-radius-full);
  background: var(--lal-amber-dim);
  color: var(--lal-amber);
  font: var(--lal-text-micro);
  text-transform: uppercase;
  letter-spacing: .05em;
}
.mpc-badge::before {
  content: '';
  width: 5px; height: 5px;
  border-radius: var(--lal-radius-full);
  background: currentColor;
  animation: mpc-pulse 1.7s ease-in-out infinite;
}
@keyframes mpc-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }
@media (prefers-reduced-motion: reduce) {
  .mpc-badge::before { animation: none; }
}

/* ---------- Grafik alanı + özel tooltip konteyneri ---------- */
.mpc-chart-wrap {
  position: relative;
  height: 340px;
}
.mpc-chart-wrap canvas { position: relative; z-index: 1; }

.mpc-tooltip {
  position: absolute;
  top: 0; left: 0;
  z-index: 5;
  min-width: 216px;
  background: var(--lal-surface);
  border: 1px solid var(--lal-border-soft);
  border-radius: var(--lal-radius-lg);
  box-shadow: var(--lal-shadow-2);
  padding: var(--lal-space-3) var(--lal-space-4);
  pointer-events: none;
  opacity: 0;
  transform: translate(-50%, calc(-100% - 14px));
  transition: opacity var(--lal-dur-fast) var(--lal-ease-out);
}
.mpc-tooltip.is-visible { opacity: 1; }
.mpc-tooltip-title {
  font: var(--lal-text-small);
  font-weight: 600;
  color: var(--lal-text-main);
  margin-bottom: var(--lal-space-2);
}
.mpc-tooltip-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--lal-space-4);
  padding: 2.5px 0;
  font-size: 13px;
}
.mpc-tooltip-label { display: inline-flex; align-items: center; gap: 7px; color: var(--lal-text-muted); }
.mpc-tooltip-dot { width: 7px; height: 7px; border-radius: var(--lal-radius-full); flex-shrink: 0; }
.mpc-tooltip-dot.is-diamond { border-radius: 1px; transform: rotate(45deg); width: 6px; height: 6px; }
.mpc-tooltip-value {
  font-family: var(--lal-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--lal-text-main);
  font-weight: 600;
}
.mpc-tooltip-divider { border-top: 1px solid var(--lal-border-soft); margin: var(--lal-space-2) 0; }
.mpc-tooltip-margins { display: flex; flex-direction: column; gap: 2px; }
.mpc-tooltip-margin { font-size: 11.5px; color: var(--lal-text-faint); display: flex; justify-content: space-between; gap: var(--lal-space-3); }

@media (max-width: 720px) {
  .mpc-chart-wrap { height: 280px; }
  .mpc-tooltip { min-width: 180px; }
}
MPC_EOF_MARKER

mkdir -p "$(dirname "templates/pages/gosterge-paneli.html")"
cat > "templates/pages/gosterge-paneli.html" << 'MPC_EOF_MARKER'
{% extends "base.html" %}
{% block title %}Gösterge Paneli — LAL{% endblock %}
{% block page_title %}Gösterge Paneli{% endblock %}

{% block extra_css %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/monthly-profit-chart.css') }}">
{% endblock %}

{% block content %}
  <div id="summary-loading" class="loading">Veriler yükleniyor…</div>
  <div id="summary-content" class="is-hidden">

    <section class="lal-command-greeting">
      <div>
        <p>Bugünün özeti</p>
        <h1>Hoş geldin, <strong>Sidar.</strong></h1>
      </div>
      <span>Mağazan sağlam ilerliyor. Bugün odaklanman gereken iki hareket var.</span>
    </section>

    <div class="hero-row">
      <div class="hero-stat" id="net-profit-hero">
        <div class="hero-stat-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 18V6"/></svg></div>
        <div>
          <div class="hero-stat-label">Net Kâr</div>
          <div class="hero-stat-value" id="stat-net-profit">–</div>
        </div>
      </div>
      <div class="hero-stat neutral">
        <div class="hero-stat-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg></div>
        <div>
          <div class="hero-stat-label">Ciro</div>
          <div class="hero-stat-value" id="stat-gross-revenue">–</div>
        </div>
      </div>
      <div class="hero-stat neutral">
        <div class="hero-stat-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M2 12h20"/></svg></div>
        <div>
          <div class="hero-stat-label">Net Hakediş <span class="hint" title="Ciro − Komisyon − Hizmet Bedeli">ⓘ</span></div>
          <div class="hero-stat-value" id="stat-revenue">–</div>
        </div>
      </div>
      <div class="hero-stat neutral">
        <div class="hero-stat-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/></svg></div>
        <div>
          <div class="hero-stat-label">Brüt Kâr</div>
          <div class="hero-stat-value" id="stat-gross-profit">–</div>
        </div>
      </div>
    </div>

    <div class="summary-grid">
      <div class="card"><div class="label">Bugünün Siparişi</div><div class="value" id="today-order-count">–</div></div>
      <div class="card"><div class="label">Toplam Sipariş</div><div class="value" id="stat-orders">–</div></div>
      <div class="card"><div class="label">Ortalama Marj <span class="hint" title="Net Kâr / Ciro">ⓘ</span></div><div class="value" id="stat-avg-margin">–</div></div>
      <div class="card"><div class="label">Komisyon</div><div class="value red" id="stat-commission">–</div></div>
      <div class="card"><div class="label">Hizmet Bedeli</div><div class="value red" id="stat-service-fee">–</div></div>
      <div class="card"><div class="label">Stopaj</div><div class="value red" id="stat-stoppage">–</div></div>
      <div class="card"><div class="label">Platform Hizmet Bedeli / Kesinti (dönemsel)</div><div class="value red" id="stat-platform-fee">–</div></div>
      <div class="card"><div class="label">Erken Ödeme Maliyeti</div><div class="value red" id="stat-cash-advance">–</div></div>
      <div class="card"><div class="label">İade Tutarı <span class="hint" title="Net Kâr'dan düşülmüştür">ⓘ</span></div><div class="value red" id="stat-return">–</div><div class="sub-note" id="stat-return-count"></div></div>
      <div class="card"><div class="label">Dönem İçi Hakediş Ödemesi <span class="hint" title="Kasaya bu dönem içinde fiilen geçen tutar">ⓘ</span></div><div class="value" id="stat-payment-order">–</div></div>
    </div>

    <!-- === AYLIK KÂR TRENDİ === -->
    <div class="panel">
      <div class="mpc-head">
        <div>
          <h2>Finansal Performans</h2>
          <p class="panel-note">Cirodan gerçek net kâra kadar aylık finansal performans. Bu grafik yukarıdaki tarih filtresinden bağımsızdır — her zaman tüm geçmişi gösterir.</p>
        </div>
        <div class="mpc-head-right">
          <span id="monthlyProfitInProgressBadge" class="mpc-badge is-hidden"></span>
          <div id="monthlyProfitMetricSelector" class="mpc-selector"></div>
        </div>
      </div>
      <div id="monthly-loading" class="loading loading-sm">Yükleniyor…</div>
      <div id="monthlyProfitChartWrap" class="mpc-chart-wrap is-hidden">
        <canvas id="monthlyProfitChart"></canvas>
      </div>
    </div>
  </div>

  <!-- === DETAYLI GRAFİKLER (Faz 5: artık accordion değil, sayfanın sabit bir bölümü) === -->
  <div class="panel" id="details-charts">
    <div class="panel-header-row">
      <h2><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg> Detaylı Grafikler &amp; Satır Dökümü</h2>
    </div>
    <div class="details-body">
      <div id="charts-loading" class="loading">Yükleniyor…</div>
      <div id="charts-content" class="is-hidden">
        <div class="panel"><h2>Günlük Net Satış (₺)</h2><canvas id="salesChart"></canvas></div>
        <div class="panel"><h2>Günlük Sipariş Adedi</h2><canvas id="ordersChart"></canvas></div>
        <div class="panel"><h2>Günlük İadeler</h2><canvas id="returnsChart"></canvas></div>
        <div class="panel">
          <h2>Sipariş Satırı Detayı</h2>
          <p class="panel-note" id="lines-note-standard">
            <span class="pill pill-real">Gerçek</span> = Finans API'den gelen kesin hakediş ·
            <span class="pill pill-estimated">Tahmini</span> = henüz settlement oluşmamış ·
            <span class="pill pill-missing">Maliyet eksik</span> = bu SKU için ürün maliyeti tanımlı değil
          </p>
          <label id="lines-estimated-filter-label" class="lal-checkbox-label lal-mb-3">
            <input type="checkbox" id="lines-estimated-only"> Sadece "Tahmini" satırları göster
          </label>
          <div class="table-scroll">
            <table>
              <thead><tr id="lines-thead-row"><th>Tarih</th><th>Sipariş No</th><th>SKU</th><th>Ürün</th><th>Adet</th><th>Ciro</th><th>Komisyon</th><th>Hizmet Bedeli</th><th>Net Hakediş</th><th>Kargo</th><th>Maliyet</th><th>Kâr</th><th>Durum</th></tr></thead>
              <tbody id="lines-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
{% endblock %}

{% block page_scripts %}
<script src="{{ url_for('static', filename='js/gosterge-paneli.js') }}"></script>
{% endblock %}
MPC_EOF_MARKER

echo "OK: dosyalar guncellendi."

git add static/js/gosterge-paneli.js static/js/shell.js static/css/monthly-profit-chart.css templates/pages/gosterge-paneli.html
git commit -m "redesign(gosterge-paneli): finansal performans grafigi - tek eksen, permanent label yok, kirmizi olmayan gercek net kar vurgusu"
git push
