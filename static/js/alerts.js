// =====================================================================
// UYARI BANDI (alerts-band) — her sayfada global (Faz 3 IA kararı)
// =====================================================================
// Faz 5'te bu mantık loadAll()'un içine gömülüydü (yalnızca Gösterge
// Paneli'nde çalışırdı). Faz 7'de kendi bağımsız veri çekişine sahip,
// gerçekten her sayfada çalışan bir modül oldu. Sonucu 'lal:alerts-updated'
// event'iyle yayınlıyor — AI Genel Bakış sayfası bunu dinleyip Riskler/
// Fırsatlar kartlarını dolduruyor (bkz. pages/ai-genel-bakis.html).
(function () {
  const ICON_ALERT_TRIANGLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>';
  const ICON_INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>';
  const ICON_ALERT_CIRCLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-top:2px;width:15px;height:15px;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
  const ICON_ALERT_TRIANGLE_SM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-top:2px;width:15px;height:15px;"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>';
  const ICON_CHECK_CIRCLE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-top:2px;width:15px;height:15px;"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';

  function renderAlerts(dq, items) {
    dq = dq || {};
    const missingCost = items.filter(i => !i.hasCost);
    const negative = items.filter(i => i.hasCost && i.margin !== null && i.margin < 0);
    const lowMargin = items.filter(i => i.hasCost && i.margin !== null && i.margin >= 0 && i.margin < 0.10);
    const opportunities = items
      .filter(i => i.hasCost && i.margin !== null && i.margin >= 0.30)
      .sort((a, b) => (b.profit || 0) - (a.profit || 0));
    const estimatedCount = dq.lines_estimated_pending_settlement || 0;

    const band = document.getElementById('alerts-band');
    if (band) {
      const parts = [];
      if (negative.length) {
        parts.push(`
          <a href="/urunler?filter=negative" class="alert alert-danger alert-clickable">
            ${ICON_ALERT_CIRCLE}
            <span><strong>${negative.length} ürün zararına satılıyor</strong> (negatif marj): ${negative.slice(0, 12).map(i => i.sku).join(', ')}${negative.length > 12 ? '…' : ''}
            <span class="hint">Ürünler sayfasında görmek için tıkla →</span></span>
          </a>`);
      }
      if (missingCost.length) {
        parts.push(`
          <a href="/urunler?filter=missing-cost" class="alert alert-danger alert-clickable">
            ${ICON_ALERT_TRIANGLE}
            <span><strong>${missingCost.length} ürünün maliyeti tanımlı değil</strong>, kârı hesaba katılmadı: ${missingCost.slice(0, 12).map(i => i.sku).join(', ')}${missingCost.length > 12 ? '…' : ''}
            <span class="hint">Ürünler sayfasında görmek için tıkla →</span></span>
          </a>`);
      }
      if (lowMargin.length) {
        parts.push(`
          <a href="/urunler?filter=low-margin" class="alert alert-warning alert-clickable">
            ${ICON_ALERT_TRIANGLE_SM}
            <span><strong>${lowMargin.length} ürünün kâr marjı %10'un altında</strong>: ${lowMargin.slice(0, 12).map(i => i.sku).join(', ')}${lowMargin.length > 12 ? '…' : ''}
            <span class="hint">Ürünler sayfasında görmek için tıkla →</span></span>
          </a>`);
      }
      if (estimatedCount > 0) {
        parts.push(`
          <a href="/gosterge-paneli?linesFilter=estimated" class="alert alert-info alert-clickable">
            ${ICON_INFO}
            <span>${fmtNum(estimatedCount)} sipariş satırı için henüz Finans API'de settlement kaydı yok — tahmini komisyonla hesaplandı. ${fmtNum(dq.lines_with_real_settlement || 0)} satır gerçek veriyle hesaplandı.
            <span class="hint">Gösterge Paneli'nde görmek için tıkla →</span></span>
          </a>`);
      }
      if (!negative.length && !missingCost.length && !lowMargin.length && !estimatedCount) {
        parts.push(`
          <div class="alert alert-success">
            ${ICON_CHECK_CIRCLE}<span>Şu an için bir uyarı yok — tüm ürünlerin maliyeti tanımlı ve marjlar makul görünüyor.</span>
          </div>`);
      }
      band.innerHTML = parts.join('');
    }

    document.dispatchEvent(new CustomEvent('lal:alerts-updated', {
      detail: { missingCost, negative, lowMargin, opportunities, estimatedCount }
    }));
  }

  async function loadAlerts() {
    try {
      const rangeParam = rangeQueryParam();
      const [summaryRes, marginsRes] = await Promise.all([
        fetch(`/api/dashboard-summary?${rangeParam}`),
        fetch(`/api/product-performance?${rangeParam}&sort_by=profit&order=desc&limit=500`),
      ]);
      const summary = await summaryRes.json();
      const margins = await marginsRes.json();
      if (summary.error || margins.error) return;
      renderAlerts(summary.data_quality, margins.items || []);
    } catch (e) { /* uyarı bandı ikincil bir bileşen, sessiz geç */ }
  }

  loadAlerts();
  document.addEventListener('lal:data-refresh', loadAlerts);
})();
