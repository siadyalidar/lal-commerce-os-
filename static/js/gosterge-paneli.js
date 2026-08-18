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

  // ---------- Aylık Kâr Trendi (2 görünüm: Trend Analizi / Finansal Özet) ----------
  // monthlyProfitChart    -> "Trend Analizi" (line, mevcut sadeleştirilmiş grafik)
  // monthlyProfitBarChart -> "Finansal Özet" (CFO tarzı gruplanmış sütun)
  // İkisi de AYNI monthlyProfitMonths verisini ve AYNI monthlyProfitMetrics()
  // renk/etiket tanımını kullanır — tek veri kaynağı, sadece görselleştirme
  // katmanı farklı.
  let monthlyProfitChart, monthlyProfitBarChart, monthlyProfitLoaded = false;
  let monthlyProfitView = 'trend'; // 'trend' | 'cfo'
  let monthlyProfitMonths = [];
  const monthlyProfitTlFmt = new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY', maximumFractionDigits: 0 });

  // KRİTİK VERİ KURALI: panelde gösterilen "Net Kâr" HER ZAMAN
  // finance_engine.monthly_profit()'in döndürdüğü realNetProfit alanıdır
  // (sabit giderler dahil nihai gerçek net kâr). Eski ara `netProfit`
  // alanına SESSİZCE düşülmez (`?? netProfit` YOK) — realNetProfit bir ay
  // için eksikse bu, backend'de bir sorunun işaretidir ve konsola açıkça
  // loglanır; grafikte o ay için sessizce yanlış bir "net kâr" gösterilmez.
  // (Not: finance_engine.py monthly_profit() her ay için realNetProfit'i
  // her zaman hesaplayıp döndürür — bkz. "realNetProfit": round(net_profit
  // - fixed_expenses, 2) — fixed_expenses kayıt yoksa 0 varsayar, yani bu
  // alan normal koşullarda ASLA eksik olmaz. Bu kontrol yalnızca bir
  // güvenlik ağı.)
  function monthlyProfitRealNet(m) {
    if (m.realNetProfit === undefined || m.realNetProfit === null) {
      console.warn(
        `[LAL] Finansal Performans: "${m.month}" ayı için API yanıtında realNetProfit alanı yok — ` +
        `finance_engine.monthly_profit() / /api/monthly-profit kontrol edilmeli. ` +
        `Eski ara "netProfit" değeri Net Kâr olarak KULLANILMIYOR, bu ay için 0 gösteriliyor.`,
        m
      );
      return 0;
    }
    return m.realNetProfit;
  }

  // LAL token'larını canlı okuyan yardımcı — hard-coded hex yerine tema neyse onu döner.
  function lalToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // Görsel hiyerarşi (sadeleştirilmiş): Ciro en ince/en nötr arka plan
  // referansı — Brüt Kâr ikincil — Net Kâr (= "gerçek net kâr", panelin tek
  // "net kâr" tanımı) tek ve en güçlü vurgu, altı dolgulu.
  //
  // NOT: Eskiden "Net Kâr" (dönemsel giderler düşülmüş ama sabit giderler
  // düşülmemiş ara metrik) ile "Gerçek Net Kâr" (+ sabit giderler de
  // düşülmüş nihai rakam) AYRI iki çizgi olarak gösteriliyordu — ikisi
  // görsel olarak neredeyse çakışıyordu ve grafiği okunaksız kılıyordu.
  // Artık sadece TEK bir "Net Kâr" çizgisi var ve bu, verideki
  // realNetProfit (gerçek net kâr) değeridir — panelde kullanıcıya
  // gösterilen "net kâr" HER ZAMAN sabit giderler dahil nihai rakamdır.
  //
  // NOT / TEŞHİS: --lal-accent bu tema sisteminde kırmızı/bordo bir tondur
  // (#c12c3e koyu, #a92639 açık tema — bkz. tokens.css / precision-theme.css),
  // bu yüzden pozitif olması beklenen Net Kâr için kullanılmıyor —
  // --lal-green kullanılıyor (LAL'in 3 semantik renginden biri).
  function monthlyProfitMetrics() {
    return [
      { key: 'revenue', label: 'Ciro', width: 1.25, dash: [], pointStyle: 'circle', fill: false, color: () => lalToken('--lal-text-faint') },
      { key: 'grossProfit', label: 'Brüt Kâr', width: 2, dash: [5, 4], pointStyle: 'circle', fill: false, color: () => lalToken('--lal-amber') },
      { key: 'netProfit', label: 'Net Kâr', width: 2.75, dash: [], pointStyle: 'circle', fill: true, color: () => lalToken('--lal-green') },
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

  // 1000 TL üzeri değerleri "₺46,8B" (bin) gibi kısaltır — sütun üstü değer
  // etiketleri için kullanılır. Amaç: Ciro (~₺300B) yanında Net Kâr
  // (~₺30-50B) gibi küçük değerlerin de tam sayı ("₺46.765") yerine kısa
  // metinle ("₺46,8B") gösterilmesi, böylece bitişik sütunların etiketleri
  // birbirine çarpmasın. Eksende ve tooltip'te hâlâ tam TL tutarı (₺46.765)
  // gösteriliyor — bu sadece sütun üstü kısa etiket için.
  function monthlyProfitCompactTl(value) {
    const sign = value < 0 ? '-' : '';
    const abs = Math.abs(value);
    if (abs >= 10000) return `${sign}₺${Math.round(abs / 1000)}B`;
    if (abs >= 1000) return `${sign}₺${(abs / 1000).toLocaleString('tr-TR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}B`;
    return monthlyProfitTlFmt.format(value);
  }

  // Sütun üstü değer etiketleri — SADECE Finansal Özet (CFO bar) grafiğinde.
  // Çakışma önleme: bir çubuğun piksel yüksekliği belirli bir eşiğin
  // altındaysa (örn. çok küçük bir ay/metrik) üstüne etiket YAZILMAZ —
  // hem görsel gürültüyü azaltır hem de bitişik minik çubukların
  // etiketlerinin üst üste binmesini engeller (tam tutar zaten hover
  // tooltip'inde mevcut). Renk için token okumak yerine BİLEREK doğrudan
  // aktif temayı (document.documentElement.dataset.lalTheme) kontrol
  // ediyoruz ve koyu temada tam beyaz (#FFFFFF) kullanıyoruz — bu, hangi
  // token'ın hangi tonda tanımlı olduğuna bağlı olmayan, garantili en
  // yüksek kontrast sonucu verir (açık temada ise --lal-text-main zaten
  // koyu/okunur bir ton, bkz. precision-theme.css).
  const MONTHLY_PROFIT_LABEL_MIN_PX = 14;
  function monthlyProfitValueLabelColor() {
    return document.documentElement.dataset.lalTheme === 'dark'
      ? '#FFFFFF'
      : '#252424';
  }
  const monthlyProfitBarValueLabels = {
    id: 'mpcBarValueLabels',
    afterDatasetsDraw(chart) {
      const ctx = chart.ctx;
      const zeroY = chart.scales.y.getPixelForValue(0);
      ctx.save();
      ctx.font = "700 10px 'Inter', sans-serif";
      ctx.textAlign = 'center';
      ctx.fillStyle = monthlyProfitValueLabelColor();
      chart.data.datasets.forEach((ds, dsIndex) => {
        if (!chart.isDatasetVisible(dsIndex)) return;
        const meta = chart.getDatasetMeta(dsIndex);
        meta.data.forEach((bar, i) => {
          const value = ds.data[i];
          if (!value) return; // 0 / null / undefined -> etiket yok
          if (Math.abs(bar.y - zeroY) < MONTHLY_PROFIT_LABEL_MIN_PX) return; // çok kısa çubuk -> atla
          const isNeg = value < 0;
          ctx.textBaseline = isNeg ? 'top' : 'bottom';
          ctx.fillText(monthlyProfitCompactTl(value), bar.x, bar.y + (isNeg ? 4 : -4));
        });
      });
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
    const net = monthlyProfitRealNet(m);
    const rows = [
      { label: 'Ciro', value: rev, color: lalToken('--lal-text-faint'), diamond: false },
      { label: 'Brüt Kâr', value: gross, color: lalToken('--lal-amber'), diamond: false },
      { label: 'Net Kâr', value: net, color: lalToken('--lal-green'), diamond: true },
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
        // İki grafik görünümü de AYNI seri sırasını paylaşıyor (Ciro/Brüt
        // Kâr/Net Kâr) — bir seriyi kapatmak her iki görünümde de geçerli
        // olsun diye ikisine birden uygulanır.
        const ref = monthlyProfitChart || monthlyProfitBarChart;
        const nowVisible = ref ? !ref.isDatasetVisible(idx) : true;
        if (monthlyProfitChart) { monthlyProfitChart.setDatasetVisibility(idx, nowVisible); monthlyProfitChart.update(); }
        if (monthlyProfitBarChart) { monthlyProfitBarChart.setDatasetVisibility(idx, nowVisible); monthlyProfitBarChart.update(); }
        chip.classList.toggle('is-active', nowVisible);
        chip.classList.toggle('is-off', !nowVisible);
        chip.setAttribute('aria-pressed', String(nowVisible));
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
      renderMonthlyProfitViews(data.months || []);
      monthlyProfitLoaded = true;
      if (monthlyProfitChart) monthlyProfitChart.resize();
      if (monthlyProfitBarChart) monthlyProfitBarChart.resize();
    } catch (e) {
      showError('Aylık kâr trendi alınamadı: ' + e.message);
    }
  }

  // Her iki görünümü de aynı `months` verisiyle inşa eder/günceller — tek
  // fetch, tek veri kaynağı; sadece Trend Analizi (line) ve Finansal Özet
  // (CFO grouped-bar) render fonksiyonları farklıdır.
  function renderMonthlyProfitViews(months) {
    monthlyProfitMonths = months;

    renderMonthlyProfitInProgressBadge(months);

    renderMonthlyProfitLineChart(months);
    renderMonthlyProfitBarChart(months);

    // Chart.js instance'ları artık hazır. Mevcut view state'ini
    // canvas'lara tekrar uygula.
    setMonthlyProfitView(monthlyProfitView);
  }

  // ================================================================
  // GRAFİK GÖRÜNÜM SWITCH
  // ================================================================
  // Tek state noktası. Event delegation kullanıldığı için butonların
  // DOM/render sırası veya Chart.js instance'larının hazır olup olmaması
  // switch davranışını etkilemez.
  function setMonthlyProfitView(view) {
    if (view !== 'trend' && view !== 'cfo') return;

    monthlyProfitView = view;

    const container = document.getElementById('monthlyProfitViewSwitch');
    const lineCanvas = document.getElementById('monthlyProfitChart');
    const barCanvas = document.getElementById('monthlyProfitBarChart');

    if (!container || !lineCanvas || !barCanvas) return;

    const isTrend = view === 'trend';
    const isCfo = view === 'cfo';

    // Buton state
    container.querySelectorAll('.mpc-view-switch-btn').forEach((btn) => {
      const active = btn.dataset.view === view;

      btn.classList.toggle('is-active', active);
      btn.classList.toggle('is-inactive', !active);
      btn.setAttribute('aria-selected', String(active));
    });

    // Canvas state
    lineCanvas.classList.toggle('is-hidden', !isTrend);
    barCanvas.classList.toggle('is-hidden', !isCfo);

    // display:none'den çıkan canvas için Chart.js yeniden ölçülmeli.
    requestAnimationFrame(() => {
      if (isTrend && monthlyProfitChart) {
        monthlyProfitChart.resize();
        monthlyProfitChart.update('none');
      }

      if (isCfo && monthlyProfitBarChart) {
        monthlyProfitBarChart.resize();
        monthlyProfitBarChart.update('none');
      }
    });
  }

  function initMonthlyProfitViewSwitch() {
    // Event delegation:
    // Buton sonradan oluşturulsa bile click çalışır.
    document.addEventListener('click', (event) => {
      const btn = event.target.closest('.mpc-view-switch-btn');
      if (!btn) return;

      const container = btn.closest('#monthlyProfitViewSwitch');
      if (!container) return;

      event.preventDefault();

      const view = btn.dataset.view;
      if (!view) return;

      setMonthlyProfitView(view);
    });

    // İlk görünüm.
    setMonthlyProfitView(monthlyProfitView);
  }

  initMonthlyProfitViewSwitch();

  // Ortak yardımcı: her iki grafik için de aynı ay etiketleri + aynı
  // {revenue, grossProfit, netProfit} veri dizileri (netProfit HER ZAMAN
  // realNetProfit'ten gelir, bkz. monthlyProfitRealNet).
  function monthlyProfitBuildData(months) {
    const labels = months.map(m => {
      const [y, mo] = m.month.split('-');
      return new Date(y, mo - 1, 1).toLocaleDateString('tr-TR', { month: 'short', year: '2-digit' });
    });
    const dataByKey = {
      revenue: months.map(m => m.revenue || 0),
      grossProfit: months.map(m => m.grossProfit || 0),
      netProfit: months.map(monthlyProfitRealNet),
    };
    return { labels, dataByKey };
  }

  // ---------- GRAFİK 1: Trend Analizi (sade 3-çizgi, mevcut yaklaşım) ----------
  function renderMonthlyProfitLineChart(months) {
    const { labels, dataByKey } = monthlyProfitBuildData(months);

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
      // Sadece "Net Kâr" (headline metrik) altı dolgulu — bu, 3 çizgi
      // arasında gözün nereye bakacağını görsel ağırlıkla belirginleştirir
      // (dolgu rengi token'lardan türetilen yarı saydam bir katman, ham
      // hex yok — bkz. tokens.css --lal-green-dim).
      const fillColor = m.key === 'netProfit' ? lalToken('--lal-green-dim') : color;
      return {
        type: 'line',
        label: m.label,
        data: dataByKey[m.key],
        borderColor: color,
        backgroundColor: m.fill ? fillColor : color,
        fill: m.fill ? 'origin' : false,
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
        order: m.fill ? 0 : 1,
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
  }

  // ---------- GRAFİK 2: Finansal Özet (CFO tarzı gruplanmış sütun) ----------
  // Aynı monthlyProfitMetrics() renk/etiket kaynağını kullanır — marka
  // kimliği iki görünüm arasında birebir tutarlı (Ciro/Brüt Kâr/Net Kâr
  // her zaman aynı renk). Sade/düz dolgu (gradient yok), ince sütunlar,
  // gruplar arası boşluk — "executive reporting" hissi için abartısız.
  function renderMonthlyProfitBarChart(months) {
    const { labels, dataByKey } = monthlyProfitBuildData(months);

    if (monthlyProfitBarChart) {
      monthlyProfitBarChart.data.labels = labels;
      monthlyProfitMetrics().forEach((m, i) => { monthlyProfitBarChart.data.datasets[i].data = dataByKey[m.key]; });
      monthlyProfitBarChart.update();
      return;
    }

    const metrics = monthlyProfitMetrics();
    const ctx = document.getElementById('monthlyProfitBarChart').getContext('2d');

    const datasets = metrics.map((m) => ({
      type: 'bar',
      label: m.label,
      data: dataByKey[m.key],
      backgroundColor: m.color(),
      borderRadius: 3,
      borderSkipped: false,
      maxBarThickness: 20,
    }));

    monthlyProfitBarChart = new Chart(ctx, {
      data: { labels, datasets },
      options: {
        maintainAspectRatio: false,
        layout: { padding: { top: 20, right: 12, bottom: 4, left: 4 } },
        interaction: { mode: 'index', intersect: false },
        // Sütun grupları arası nefes payı — "sıkışık e-ticaret bar chart"
        // hissi yerine daha ferah, kurumsal/analitik bir grid.
        categoryPercentage: 0.62,
        barPercentage: 0.86,
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false, external: monthlyProfitExternalTooltip },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { color: lalToken('--lal-border-soft') },
            ticks: { font: { size: 11.5, family: "'Inter', sans-serif" }, color: lalToken('--lal-text-faint') },
          },
          y: {
            beginAtZero: true,
            // En yüksek çubuğun (Ciro) üstündeki değer etiketi için pay —
            // etiket, ölçeğin en üst gridline'ına yapışıp kırpılmasın diye.
            grace: '8%',
            grid: { color: lalToken('--lal-border-soft') },
            border: { display: false },
            ticks: { font: { size: 11, family: "'Inter', sans-serif" }, color: lalToken('--lal-text-faint'), maxTicksLimit: 6, callback: (v) => monthlyProfitTlFmt.format(v) },
          },
        },
      },
      plugins: [monthlyProfitBarValueLabels],
    });

    renderMonthlyProfitSelector(metrics);
  }

  // Tema değişince (açık/koyu) her iki grafiğin renklerini de tazeler —
  // tek, üst-seviye kayıt (grafikler henüz kurulmamış olsa bile güvenli,
  // her biri kendi null-check'ini yapar).
  document.addEventListener('lal:theme-change', () => {
    const freshMetrics = monthlyProfitMetrics();
    if (monthlyProfitChart) {
      freshMetrics.forEach((m, i) => {
        const color = m.color();
        const ds = monthlyProfitChart.data.datasets[i];
        ds.borderColor = color;
        ds.backgroundColor = m.fill ? lalToken('--lal-green-dim') : color;
        ds.pointBackgroundColor = color;
        ds.pointBorderColor = lalToken('--lal-surface');
      });
      monthlyProfitChart.options.scales.x.border.color = lalToken('--lal-border-soft');
      monthlyProfitChart.options.scales.x.ticks.color = lalToken('--lal-text-faint');
      monthlyProfitChart.options.scales.y.grid.color = lalToken('--lal-border-soft');
      monthlyProfitChart.options.scales.y.ticks.color = lalToken('--lal-text-faint');
      monthlyProfitChart.update();
    }
    if (monthlyProfitBarChart) {
      freshMetrics.forEach((m, i) => { monthlyProfitBarChart.data.datasets[i].backgroundColor = m.color(); });
      monthlyProfitBarChart.options.scales.x.border.color = lalToken('--lal-border-soft');
      monthlyProfitBarChart.options.scales.x.ticks.color = lalToken('--lal-text-faint');
      monthlyProfitBarChart.options.scales.y.grid.color = lalToken('--lal-border-soft');
      monthlyProfitBarChart.options.scales.y.ticks.color = lalToken('--lal-text-faint');
      monthlyProfitBarChart.update('none');
      monthlyProfitBarChart.draw();
    }
    renderMonthlyProfitSelector(freshMetrics);
  });

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
