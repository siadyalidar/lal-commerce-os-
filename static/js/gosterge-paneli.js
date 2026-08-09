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
  function lalTokenRgba(name, alpha) {
    const hex = lalToken(name).replace('#', '');
    const full = hex.length === 3 ? hex.split('').map(c => c + c).join('') : hex;
    const n = parseInt(full, 16);
    if (Number.isNaN(n)) return `rgba(0,0,0,${alpha})`;
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  }

  // Görsel hiyerarşi (brief madde 6-7): Ciro nötr/ince ve kendi eksenine
  // ayrılmış (küçük kâr serilerini ezmesin diye) — Brüt Kâr ikincil — Net Kâr
  // pozitif ana vurgulu — Gerçek Net Kâr en güçlü vurgu (marka accent'i +
  // kesikli çizgi + eşkenar nokta, sadece renkle ayrışmasın diye).
  function monthlyProfitMetrics() {
    return [
      { key: 'revenue', label: 'Ciro', axis: 'yRevenue', width: 1.5, dash: [], fillAlpha: 0, pointStyle: 'circle', color: () => lalToken('--lal-text-faint') },
      { key: 'grossProfit', label: 'Brüt Kâr', axis: 'y', width: 1.75, dash: [], fillAlpha: 0, pointStyle: 'circle', color: () => lalToken('--lal-text-muted') },
      { key: 'netProfit', label: 'Net Kâr', axis: 'y', width: 2.25, dash: [], fillAlpha: .14, pointStyle: 'circle', color: () => lalToken('--lal-green') },
      { key: 'realNetProfit', label: 'Gerçek Net Kâr', axis: 'y', width: 2.5, dash: [6, 4], fillAlpha: 0, pointStyle: 'rectRot', color: () => lalToken('--lal-accent') },
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
      { label: 'Ciro', value: rev, color: lalToken('--lal-text-faint') },
      { label: 'Brüt Kâr', value: gross, color: lalToken('--lal-text-muted') },
      { label: 'Net Kâr', value: net, color: lalToken('--lal-green') },
      { label: 'Gerçek Net Kâr', value: real, color: lalToken('--lal-accent') },
    ];
    const marginLine = (label, value) => rev
      ? `<div class="mpc-tooltip-margin"><span>${label}</span><span>%${((value / rev) * 100).toFixed(1)}</span></div>` : '';

    el.innerHTML = `
      <div class="mpc-tooltip-title">${chart.data.labels[idx]}</div>
      ${rows.map(r => `
        <div class="mpc-tooltip-row">
          <span class="mpc-tooltip-label"><span class="mpc-tooltip-dot" style="background:${r.color}"></span>${r.label}</span>
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
        <span class="mpc-chip-dot" style="background:${m.color()}"></span>${m.label}
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
    const profitMax = Math.max(1, ...dataByKey.grossProfit, ...dataByKey.netProfit, ...dataByKey.realNetProfit);
    const revenueMax = Math.max(1, ...dataByKey.revenue);

    const datasets = metrics.map((m) => {
      const color = m.color();
      return {
        type: 'line',
        label: m.label,
        data: dataByKey[m.key],
        yAxisID: m.axis,
        borderColor: color,
        backgroundColor: m.fillAlpha ? lalTokenRgba(m.key === 'netProfit' ? '--lal-green' : '--lal-accent', m.fillAlpha) : 'transparent',
        fill: !!m.fillAlpha,
        borderWidth: m.width,
        borderDash: m.dash,
        tension: 0.3,
        pointStyle: m.pointStyle,
        pointRadius: m.axis === 'yRevenue' ? 0 : 2.5,
        pointHoverRadius: m.axis === 'yRevenue' ? 3 : 5,
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
          // Kâr serileri (Brüt/Net/Gerçek Net) kendi ekseninde — Ciro'nun
          // büyük ölçeği artık küçük kâr değerlerini görsel olarak ezmiyor
          // (brief madde 7).
          y: {
            beginAtZero: true,
            suggestedMax: profitMax * 1.35,
            position: 'left',
            grid: { color: lalToken('--lal-border-soft') },
            border: { display: false },
            ticks: { font: { size: 11, family: "'Inter', sans-serif" }, color: lalToken('--lal-text-faint'), maxTicksLimit: 6, callback: (v) => monthlyProfitTlFmt.format(v) },
          },
          // Ciro sadece arka plan bağlamı için — kendi (gizli) ekseninde,
          // kâr ekseniyle karışmaz.
          yRevenue: {
            beginAtZero: true,
            suggestedMax: revenueMax * 1.15,
            position: 'right',
            display: false,
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
        ds.pointBackgroundColor = color;
        ds.pointBorderColor = lalToken('--lal-surface');
        if (m.fillAlpha) ds.backgroundColor = lalTokenRgba(m.key === 'netProfit' ? '--lal-green' : '--lal-accent', m.fillAlpha);
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
