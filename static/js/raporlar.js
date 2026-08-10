let reportsTrendChart = null;

function reportCell(value, className) {
  return `<td${className ? ` class="${className}"` : ''}>${value}</td>`;
}

function marketplaceLabel(value) {
  return value === 'trendyol' ? 'Trendyol' : value === 'hepsiburada' ? 'Hepsiburada' : value;
}

function renderReportsChart(daily) {
  const canvas = document.getElementById('reportsTrendChart');
  if (!canvas || typeof Chart === 'undefined') return;
  if (reportsTrendChart) reportsTrendChart.destroy();
  const styles = getComputedStyle(document.documentElement);
  reportsTrendChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: daily.map(item => fmtDateShort(item.date)),
      datasets: [
        { label: 'Ciro', data: daily.map(item => item.revenue), backgroundColor: styles.getPropertyValue('--lal-accent').trim(), borderRadius: 4 },
        { label: 'Kâr', data: daily.map(item => item.profit), type: 'line', borderColor: styles.getPropertyValue('--lal-green').trim(), backgroundColor: 'transparent', tension: .3, spanGaps: true },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: styles.getPropertyValue('--lal-text-main').trim() } } }, scales: { x: { ticks: { color: styles.getPropertyValue('--lal-text-muted').trim() }, grid: { display: false } }, y: { ticks: { color: styles.getPropertyValue('--lal-text-muted').trim(), callback: value => fmtTL(value) }, grid: { color: styles.getPropertyValue('--lal-border').trim() } } } },
  });
}

function renderReports(data) {
  const totals = data.totals;
  safeText('report-revenue', fmtTL(totals.grossRevenue));
  safeText('report-profit', fmtTL(totals.netProfit));
  safeText('report-margin', totals.grossRevenue ? fmtPct(totals.netProfit / totals.grossRevenue) : '–');
  safeText('report-quantity', fmtNum(data.daily.reduce((sum, item) => sum + item.quantity, 0)));

  renderReportsChart(data.daily);
  const mpEl = document.getElementById('report-marketplaces');
  const entries = Object.entries(data.byMarketplace || {});
  mpEl.innerHTML = entries.length ? entries.map(([name, values]) => `<div class="reports-marketplace-row"><span>${marketplaceLabel(name)}</span><strong>${fmtTL(values.netProfit)}</strong><small>${values.grossRevenue ? fmtPct(values.netProfit / values.grossRevenue) : '–'} marj · ${fmtNum(values.orderCount)} sipariş</small></div>`).join('') : '<div class="lal-empty-state">Seçili filtrede pazaryeri verisi yok.</div>';

  const products = document.getElementById('report-products');
  products.innerHTML = data.products.length ? data.products.map(item => `<tr>${reportCell(item.productName || item.sku || 'Bilinmeyen ürün')}${reportCell(fmtNum(item.quantity))}${reportCell(fmtTL(item.revenue))}${reportCell(fmtTL(item.profit), item.profit < 0 ? 'lal-profit-neg' : 'lal-profit-pos')}${reportCell(fmtPct(item.margin))}</tr>`).join('') : emptyStateRow(5, 'Bu aralıkta satılmış ürün yok.');

  const stock = document.getElementById('report-stock');
  stock.innerHTML = data.stock.items.length ? data.stock.items.map(item => `<tr>${reportCell(marketplaceLabel(item.marketplace))}${reportCell(item.sku || '–')}${reportCell(fmtNum(item.quantity), 'lal-profit-neg')}${reportCell(fmtNum(item.min_stock_threshold))}</tr>`).join('') : emptyStateRow(4, 'Minimum eşiğin altında ürün yok.');
  safeText('report-stock-note', data.stock.lowCount ? `${fmtNum(data.stock.lowCount)} ürün minimum eşiğin altında.` : 'Minimum eşiğin altında ürün yok.');

  const quality = data.quality;
  const notes = [];
  if (quality.lines_estimated_pending_settlement) notes.push(`${fmtNum(quality.lines_estimated_pending_settlement)} satırda kesin settlement henüz oluşmadığı için kâr tahminidir.`);
  if (quality.skus_missing_cost.length) notes.push(`${fmtNum(quality.skus_missing_cost.length)} SKU'nun maliyeti eksik; bu satırların kârı toplamı eksik gösterebilir.`);
  if (quality.orders_missing_cargo_invoice) notes.push(`${fmtNum(quality.orders_missing_cargo_invoice)} siparişin kargo faturası bulunamadı.`);
  document.getElementById('report-quality').textContent = notes.length ? notes.join(' ') : 'Veri kalitesi iyi: seçili aralıkta maliyet, settlement ve kargo eksikliği görünmüyor.';
}

async function loadReports() {
  const loading = document.getElementById('reports-loading');
  const content = document.getElementById('reports-content');
  if (!loading || !content) return;
  loading.classList.remove('is-hidden');
  content.classList.add('is-hidden');
  try {
    const response = await fetch(`/api/reports/overview?${rangeQueryParam()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Rapor yüklenemedi.');
    renderReports(data);
    content.classList.remove('is-hidden');
  } catch (error) {
    showError(error.message);
  } finally {
    loading.classList.add('is-hidden');
  }
}

document.addEventListener('lal:data-refresh', loadReports);
document.addEventListener('lal:theme-change', () => { if (document.getElementById('reports-content') && !document.getElementById('reports-content').classList.contains('is-hidden')) loadReports(); });
loadReports();
