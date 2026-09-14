#!/usr/bin/env node
/* Runs against the Desktop copy. Creates .bak copies before editing. */
const fs = require('fs');
const path = require('path');

const root = process.argv[2] || '/Users/s1dar/Desktop/lal-commerce-os-guncel';
const dashboardPath = path.join(root, 'static/js/gosterge-paneli.js');
const growthPath = path.join(root, 'static/js/gelir-kacaklari.js');
const backupSuffix = '.bak-20260830';

function replaceOnce(source, before, after, label) {
  if (!source.includes(before)) throw new Error(`Beklenen kod bulunamadı: ${label}`);
  return source.replace(before, after);
}
function backup(file) {
  const target = file + backupSuffix;
  if (!fs.existsSync(target)) fs.copyFileSync(file, target);
}

let dashboard = fs.readFileSync(dashboardPath, 'utf8');
let growth = fs.readFileSync(growthPath, 'utf8');

dashboard = replaceOnce(dashboard,
`  let firstLoadDone = false;
  let linesCache = [];
  const LINES_THEAD_STANDARD`,
`  let firstLoadDone = false;
  let linesCache = [];
  let refreshGeneration = 0;
  const requestControllers = {};
  const LINES_THEAD_STANDARD`, 'istek durumu');

dashboard = replaceOnce(dashboard,
`  function renderProfitSummary(t) {`,
`  function startRequest(name) {
    if (requestControllers[name]) requestControllers[name].abort();
    const controller = new AbortController();
    requestControllers[name] = controller;
    return controller;
  }

  function isCurrentRequest(name, controller, generation) {
    return requestControllers[name] === controller && !controller.signal.aborted && generation === refreshGeneration;
  }

  function setVisible(id, visible) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('is-hidden', !visible);
  }

  function renderProfitSummary(t) {`, 'yardımcı fonksiyonlar');

dashboard = replaceOnce(dashboard,
`          <td>\${l.orderNumber || ''}</td>
          <td>\${l.sku || '–'}</td>
          <td>\${l.productName || ''}</td>`,
`          <td>\${esc(l.orderNumber || '')}</td>
          <td>\${esc(l.sku || '–')}</td>
          <td>\${esc(l.productName || '')}</td>`, 'sipariş satırı escape');

const oldSummary = dashboard.match(/  async function loadSummary\(\) \{[\s\S]*?\n  \}\n\n  async function loadTodayOrderCount\(\) \{[\s\S]*?\n  \}\n\n  \/\/ ---------- Aylık Kâr Trendi/);
if (!oldSummary) throw new Error('Beklenen kod bulunamadı: özet yükleme fonksiyonları');
const newSummary = `  async function loadSummary(generation = refreshGeneration) {
    const controller = startRequest('summary');
    if (!firstLoadDone) { setVisible('summary-loading', true); setVisible('summary-content', false); }
    hideError();
    void loadTodayOrderCount(generation);
    try {
      const rangeParam = rangeQueryParam();
      const [summaryRes, orderCountRes] = await Promise.all([
        fetch(\`/api/dashboard-summary?\${rangeParam}\`, { signal: controller.signal }),
        fetch(\`/api/orders?\${rangeParam}&page=1&page_size=1\`, { signal: controller.signal }),
      ]);
      const summary = await summaryRes.json();
      if (!isCurrentRequest('summary', controller, generation)) return;
      try {
        const orderCountData = await orderCountRes.json();
        if (!isCurrentRequest('summary', controller, generation)) return;
        safeText('stat-orders', orderCountData.error ? '—' : fmtNum(orderCountData.total));
      } catch (e) {
        if (isCurrentRequest('summary', controller, generation)) safeText('stat-orders', '—');
      }
      if (summary.error) { showError(summary.error); setVisible('summary-loading', false); return; }
      renderProfitSummary(summary.totals);
      renderLines(summary.lines || summary.orders || []);
      loadMonthlyProfitChart(generation);
      setVisible('summary-loading', false);
      setVisible('summary-content', true);
      firstLoadDone = true;
    } catch (e) {
      if (!isCurrentRequest('summary', controller, generation) || e.name === 'AbortError') return;
      setVisible('summary-loading', false);
      showError('Beklenmeyen bir hata oluştu: ' + e.message);
    }
  }

  async function loadTodayOrderCount(generation = refreshGeneration) {
    const controller = startRequest('todayOrderCount');
    const el = document.getElementById('today-order-count');
    try {
      const res = await fetch('/api/today-order-count', { signal: controller.signal });
      const data = await res.json();
      if (!isCurrentRequest('todayOrderCount', controller, generation)) return;
      el.textContent = data.error ? '—' : fmtNum(data.count);
    } catch (e) {
      if (isCurrentRequest('todayOrderCount', controller, generation) && e.name !== 'AbortError') el.textContent = '—';
    }
  }

  // ---------- Aylık Kâr Trendi`;
dashboard = dashboard.replace(oldSummary[0], newSummary);

const oldMonthly = dashboard.match(/  async function loadMonthlyProfitChart\(\) \{[\s\S]*?\n  \}\n\n  \/\/ Her iki görünümü/);
if (!oldMonthly) throw new Error('Beklenen kod bulunamadı: aylık grafik yükleme fonksiyonu');
const newMonthly = `  async function loadMonthlyProfitChart(generation = refreshGeneration) {
    if (monthlyProfitLoaded) return;
    const controller = startRequest('monthlyProfit');
    try {
      const res = await fetch(\`/api/monthly-profit?full_history=true&marketplace=\${currentMarketplace}\`, { signal: controller.signal });
      const data = await res.json();
      if (!isCurrentRequest('monthlyProfit', controller, generation)) return;
      if (data.error) { showError(data.error); return; }
      setVisible('monthly-loading', false);
      setVisible('monthlyProfitChartWrap', true);
      renderMonthlyProfitViews(data.months || []);
      monthlyProfitLoaded = true;
      if (monthlyProfitChart) monthlyProfitChart.resize();
      if (monthlyProfitBarChart) monthlyProfitBarChart.resize();
    } catch (e) {
      if (!isCurrentRequest('monthlyProfit', controller, generation) || e.name === 'AbortError') return;
      showError('Aylık kâr trendi alınamadı: ' + e.message);
    }
  }

  // Her iki görünümü`;
dashboard = dashboard.replace(oldMonthly[0], newMonthly);

dashboard = replaceOnce(dashboard,
`  const darkChartDefaults = {
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: '#15171C', borderColor: '#3DDBD9', borderWidth: 1, padding: 12, cornerRadius: 10, titleColor: '#F2F3F5', titleFont: { size: 13, weight: '600' }, bodyColor: '#F2F3F5', bodyFont: { size: 13.5, weight: '600' }, displayColors: false }
    },
    scales: {
      x: { grid: { display: false }, border: { color: '#24272E' }, ticks: { color: '#5B5F68', font: { size: 11 } } },
      y: { beginAtZero: true, grid: { color: '#1B1E24' }, border: { display: false }, ticks: { color: '#5B5F68', font: { size: 11 } } },
    },
    interaction: { mode: 'index', intersect: false },
  };`,
`  function currentDailyChartDefaults() {
    const textMain = lalToken('--lal-text-main');
    const textMuted = lalToken('--lal-text-muted');
    const textFaint = lalToken('--lal-text-faint');
    const surface2 = lalToken('--lal-surface-2');
    const border = lalToken('--lal-border');
    const borderSoft = lalToken('--lal-border-soft');
    const green = lalToken('--lal-green');
    return {
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: surface2, borderColor: green, borderWidth: 1, padding: 12, cornerRadius: 10, titleColor: textMain, titleFont: { size: 13, weight: '600' }, bodyColor: textMain, bodyFont: { size: 13.5, weight: '600' }, displayColors: false }
      },
      scales: {
        x: { grid: { display: false }, border: { color: border }, ticks: { color: textFaint, font: { size: 11 } } },
        y: { beginAtZero: true, grid: { color: borderSoft }, border: { display: false }, ticks: { color: textMuted, font: { size: 11 } } },
      },
      interaction: { mode: 'index', intersect: false },
    };
  }`, 'günlük grafik tema ayarları');

dashboard = replaceOnce(dashboard,
`  let salesChart, ordersChart, returnsChart;

  function renderCharts(daily, returnsDaily) {`,
`  let salesChart, ordersChart, returnsChart;
  let chartDaily = [], chartReturnsDaily = [];

  function renderCharts(daily, returnsDaily) {
    chartDaily = daily;
    chartReturnsDaily = returnsDaily;
    const darkChartDefaults = currentDailyChartDefaults();
    const green = lalToken('--lal-green');
    const greenDim = lalToken('--lal-green-dim');
    const red = lalToken('--lal-red');
    const redDim = lalToken('--lal-red-dim');
    const surface = lalToken('--lal-surface');`, 'günlük grafik veri önbelleği');

dashboard = dashboard
  .replaceAll("salesGrad.addColorStop(0, 'rgba(61,219,217,0.4)'); salesGrad.addColorStop(1, 'rgba(61,219,217,0.0)');", "salesGrad.addColorStop(0, greenDim); salesGrad.addColorStop(1, 'transparent');")
  .replaceAll("borderColor: '#3DDBD9', backgroundColor: salesGrad", 'borderColor: green, backgroundColor: salesGrad')
  .replaceAll("pointHoverBackgroundColor: '#3DDBD9', pointHoverBorderColor: '#0A0B0D'", 'pointHoverBackgroundColor: green, pointHoverBorderColor: surface')
  .replaceAll("ordersGrad.addColorStop(0, 'rgba(61,219,217,0.55)'); ordersGrad.addColorStop(1, 'rgba(61,219,217,0.15)');", 'ordersGrad.addColorStop(0, green); ordersGrad.addColorStop(1, greenDim);')
  .replaceAll("returnsGrad.addColorStop(0, 'rgba(240,102,90,0.55)'); returnsGrad.addColorStop(1, 'rgba(240,102,90,0.15)');", 'returnsGrad.addColorStop(0, red); returnsGrad.addColorStop(1, redDim);');

const oldCharts = dashboard.match(/  async function loadChartsSection\(\) \{[\s\S]*?\n  \}\n\n  \/\/ ---------- Başlangıç ----------/);
if (!oldCharts) throw new Error('Beklenen kod bulunamadı: günlük grafik yükleme fonksiyonu');
const newCharts = `  async function loadChartsSection(generation = refreshGeneration) {
    const controller = startRequest('charts');
    setVisible('charts-loading', true);
    setVisible('charts-content', false);
    try {
      const rangeParam = rangeQueryParam();
      const [salesRes, returnsRes] = await Promise.all([
        fetch(\`/api/daily-sales?\${rangeParam}\`, { signal: controller.signal }),
        fetch(\`/api/daily-returns?\${rangeParam}\`, { signal: controller.signal })
      ]);
      const salesData = await salesRes.json();
      const returnsData = await returnsRes.json();
      if (!isCurrentRequest('charts', controller, generation)) return;
      if (salesData.error) { showError(salesData.error); setVisible('charts-loading', false); return; }
      setVisible('charts-loading', false);
      setVisible('charts-content', true);
      renderCharts(salesData.daily, returnsData.daily || []);
      [salesChart, ordersChart, returnsChart].forEach(c => c && c.resize());
      if (returnsData.error) showError('İade verileri alınamadı: ' + returnsData.error);
    } catch (e) {
      if (!isCurrentRequest('charts', controller, generation) || e.name === 'AbortError') return;
      setVisible('charts-loading', false);
      showError('Beklenmeyen bir hata oluştu: ' + e.message);
    }
  }

  // ---------- Başlangıç ----------`;
dashboard = dashboard.replace(oldCharts[0], newCharts);

dashboard = replaceOnce(dashboard,
`  document.addEventListener('lal:data-refresh', function () {
    monthlyProfitLoaded = false;
    loadSummary();
    loadChartsSection();
  });`,
`  document.addEventListener('lal:data-refresh', function () {
    refreshGeneration += 1;
    monthlyProfitLoaded = false;
    loadSummary(refreshGeneration);
    loadChartsSection(refreshGeneration);
  });
  document.addEventListener('lal:theme-change', function () {
    if (chartDaily.length || chartReturnsDaily.length) renderCharts(chartDaily, chartReturnsDaily);
  });`, 'veri yenileme olayı');

growth = replaceOnce(growth,
`  const esc = value => String(value == null ? '' : value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
`, '', 'yerel esc');
growth = replaceOnce(growth, 'data-id="${q.id}"', 'data-id="${esc(q.id)}"', 'soru kimliği escape');

backup(dashboardPath);
backup(growthPath);
fs.writeFileSync(dashboardPath, dashboard);
fs.writeFileSync(growthPath, growth);
console.log('Tamamlandı. Yedekler: *.bak-20260830');
