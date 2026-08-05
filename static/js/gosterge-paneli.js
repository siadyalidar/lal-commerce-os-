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
    const labels = months.map(m => {
      const [y, mo] = m.month.split('-');
      return new Date(y, mo - 1, 1).toLocaleDateString('tr-TR', { month: 'short', year: '2-digit' });
    });
    const tlFmt = new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY', maximumFractionDigits: 0 });
    const revenueData = months.map(m => m.revenue || 0);
    const grossProfitData = months.map(m => m.grossProfit || 0);
    const netProfitData = months.map(m => m.netProfit || 0);
    const realNetProfitData = months.map(m => (m.realNetProfit ?? m.netProfit) || 0);

    if (monthlyProfitChart) {
      monthlyProfitChart.data.labels = labels;
      monthlyProfitChart.data.datasets[0].data = revenueData;
      monthlyProfitChart.data.datasets[1].data = grossProfitData;
      monthlyProfitChart.data.datasets[2].data = netProfitData;
      monthlyProfitChart.data.datasets[3].data = realNetProfitData;
      monthlyProfitChart.update();
      return;
    }

    const ctx = document.getElementById('monthlyProfitChart').getContext('2d');
    const revenueGrad = ctx.createLinearGradient(0, 0, 0, 300);
    revenueGrad.addColorStop(0, 'rgba(155,161,171,0.85)'); revenueGrad.addColorStop(1, 'rgba(155,161,171,0.35)');
    const grossGrad = ctx.createLinearGradient(0, 0, 0, 300);
    grossGrad.addColorStop(0, 'rgba(61,219,217,0.95)'); grossGrad.addColorStop(1, 'rgba(61,219,217,0.45)');
    const netFillGrad = ctx.createLinearGradient(0, 0, 0, 300);
    netFillGrad.addColorStop(0, 'rgba(52,211,153,0.35)'); netFillGrad.addColorStop(1, 'rgba(52,211,153,0.0)');

    if (window.ChartDataLabels) { Chart.register(ChartDataLabels); Chart.defaults.set('plugins.datalabels', { display: false }); }

    monthlyProfitChart = new Chart(ctx, {
      data: {
        labels,
        datasets: [
          { type: 'bar', label: 'Ciro', data: revenueData, backgroundColor: revenueGrad, borderColor: '#C9CDD3', borderWidth: 1.25, borderRadius: 5, borderSkipped: false, order: 3, barPercentage: 0.6, categoryPercentage: 0.7,
            datalabels: { display: true, anchor: 'end', align: 'end', offset: 2, color: '#C9CDD3', font: { size: 10, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },
          { type: 'bar', label: 'Brüt Kâr', data: grossProfitData, backgroundColor: grossGrad, borderColor: '#3DDBD9', borderWidth: 1.25, borderRadius: 5, borderSkipped: false, order: 2, barPercentage: 0.6, categoryPercentage: 0.7,
            datalabels: { display: true, anchor: 'end', align: 'end', offset: 2, color: '#3DDBD9', font: { size: 10, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },
          { type: 'line', label: 'Net Kâr', data: netProfitData, borderColor: '#34D399', backgroundColor: netFillGrad, borderWidth: 2.5, tension: 0.35, fill: true, order: 1, pointRadius: 3.5, pointHoverRadius: 7, pointBackgroundColor: '#34D399', pointBorderColor: '#0A0B0D', pointBorderWidth: 2,
            datalabels: { display: true, anchor: 'end', align: 'top', offset: 6, color: '#34D399', font: { size: 10, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },
          { type: 'line', label: 'Gerçek Net Kâr (sabit giderler dahil)', data: realNetProfitData, borderColor: '#F59E0B', backgroundColor: 'transparent', borderWidth: 2.5, borderDash: [6, 4], tension: 0.35, fill: false, order: 0, pointRadius: 3.5, pointHoverRadius: 7, pointBackgroundColor: '#F59E0B', pointBorderColor: '#0A0B0D', pointBorderWidth: 2,
            datalabels: { display: true, anchor: 'end', align: 'bottom', offset: 6, color: '#F59E0B', font: { size: 10, weight: '700', family: "'Inter', sans-serif" }, formatter: (v) => tlFmt.format(v) } },
        ]
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'top', align: 'end', labels: { usePointStyle: true, pointStyle: 'circle', boxWidth: 8, padding: 18, font: { size: 12.5, family: "'Inter', sans-serif" }, color: '#C9CDD3' } },
          tooltip: {
            backgroundColor: '#15171C', borderColor: '#3DDBD9', borderWidth: 1, padding: 12, cornerRadius: 10,
            titleColor: '#F2F3F5', titleFont: { size: 13.5, weight: '700' },
            bodyColor: '#F2F3F5', bodyFont: { size: 13.5, weight: '600' }, bodySpacing: 6,
            displayColors: true, boxWidth: 10, boxHeight: 10, boxPadding: 4,
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${tlFmt.format(ctx.parsed.y)}`,
              footer: (items) => {
                const rev = items.find(i => i.dataset.label === 'Ciro');
                const net = items.find(i => i.dataset.label === 'Net Kâr');
                if (!rev || !net || !rev.parsed.y) return '';
                const margin = (net.parsed.y / rev.parsed.y) * 100;
                return `Net Marj: %${margin.toFixed(1)}`;
              },
            },
            footerColor: '#8B8F98', footerFont: { size: 11.5, weight: '500' },
          },
        },
        scales: {
          x: { grid: { display: false }, border: { color: '#24272E' }, ticks: { font: { size: 11.5 }, color: '#5B5F68' } },
          y: { beginAtZero: true, grid: { color: '#1B1E24' }, border: { display: false }, ticks: { font: { size: 11 }, color: '#5B5F68', callback: (v) => tlFmt.format(v) } },
        },
      },
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
