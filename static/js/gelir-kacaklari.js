(function () {
  if (!document.getElementById('questions-list')) return;
  const days = () => document.getElementById('growth-days').value;
  const error = message => { const el = document.getElementById('growth-error'); el.textContent = message; el.style.display = 'block'; };
  const clearError = () => document.getElementById('growth-error').style.display = 'none';
  async function json(url, options) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok || data.error) throw new Error(data.error || 'İstek başarısız.'); return data; }
  async function loadQuestions() {
    clearError(); const btn = document.getElementById('questions-sync'); btn.disabled = true; btn.textContent = 'Getiriliyor…';
    try { const data = await json('/api/growth/questions/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({days:Number(days())})});
      document.getElementById('questions-kpi').textContent = fmtNum(data.total);
      document.getElementById('questions-list').innerHTML = data.items.length ? data.items.map(q => `<article class="growth-item"><div><strong>${esc(q.productName || 'Ürün')}</strong><p>${esc(q.text)}</p><small>${esc(q.action)}</small></div><button class="lal-btn lal-btn-ghost question-answer" data-id="${esc(q.id)}" data-question="${esc(q.text)}">Yanıtla</button></article>`).join('') : 'Bekleyen soru yok.';
      document.querySelectorAll('.question-answer').forEach(button => button.addEventListener('click', () => answer(button)));
    } catch (e) { error(e.message); } finally { btn.disabled = false; btn.textContent = 'Soruları getir'; }
  }
  async function answer(button) {
    const text = prompt('Trendyol’a gönderilecek yanıtı yaz:', ''); if (text === null) return;
    if (!confirm('Bu yanıt Trendyol’da yayınlanmak üzere gönderilecek. Devam edilsin mi?')) return;
    try { await json(`/api/growth/questions/${button.dataset.id}/answer`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text, confirm:true})}); button.textContent = 'Gönderildi'; button.disabled = true; } catch (e) { error(e.message); }
  }
  async function loadReturns() {
    clearError(); try { const data = await json(`/api/growth/return-actions?days=${days()}`); const count = data.items.reduce((sum, item) => sum + (item.count || 0), 0); document.getElementById('returns-kpi').textContent = fmtNum(count);
      document.getElementById('returns-list').innerHTML = data.items.length ? data.items.map(i => `<article class="growth-item"><div><strong>${esc(i.sku)} · ${esc(i.reason)}</strong><p>${fmtNum(i.count)} iade · ${esc(i.productName || '')}</p><small>${esc(i.action)}</small></div></article>`).join('') : 'Bu dönemde iade nedeni bulunamadı.'; } catch (e) { error(e.message); }
  }
  async function loadCampaigns() {
    clearError(); try { const data = await json(`/api/growth/campaign-profit?days=${days()}`); const complete = data.items.filter(i => i.hasCompleteCost).reduce((sum, i) => sum + (i.estimatedContribution || 0), 0); document.getElementById('campaign-kpi').textContent = fmtTL(complete);
      document.getElementById('campaign-table').innerHTML = data.items.length ? data.items.map(i => `<tr><td>${esc(i.campaignId)}</td><td>${esc(i.sku)}</td><td>${fmtNum(i.quantity)}</td><td>${fmtTL(i.revenue)}</td><td>${fmtTL(i.sellerDiscount)}</td><td class="${i.estimatedContribution < 0 ? 'is-danger' : ''}">${fmtTL(i.estimatedContribution)}</td></tr>`).join('') : '<tr><td colspan="6">Bu dönemde kampanyalı sipariş yok.</td></tr>'; } catch (e) { error(e.message); }
  }
  document.getElementById('questions-sync').addEventListener('click', loadQuestions); document.getElementById('returns-load').addEventListener('click', loadReturns); document.getElementById('campaign-load').addEventListener('click', loadCampaigns);
}());
