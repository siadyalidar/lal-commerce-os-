function starsMarkup(star) {
  const filled = Math.max(0, Math.min(5, Math.round(star || 0)));
  let html = '';
  for (let i = 0; i < 5; i++) {
    html += i < filled ? '★' : '<span class="star-empty">★</span>';
  }
  return html;
}

function reviewCard(review, opts) {
  opts = opts || {};
  const content = review.content && review.content.trim()
    ? `<div class="review-card-content">${esc(review.content)}</div>`
    : '<div class="review-card-content is-empty">Yazılı yorum yok (sadece puan/medya)</div>';
  const badges = [];
  if (opts.isToday) badges.push('<span class="review-card-new-badge">Yeni</span>');
  if (review.is_purchase_verified) badges.push('<span class="review-card-verified-badge">Doğrulanmış Satın Alma</span>');

  return `<article class="review-card">
    <div class="review-card-header">
      <span class="review-card-stars">${starsMarkup(review.star)}</span>
      <div class="review-card-meta">${badges.join('')}<span class="review-card-date">${fmtDateShort(review.created_at)}</span></div>
    </div>
    ${content}
    <div class="review-card-sku">${esc(review.product_sku || '–')}</div>
  </article>`;
}

function esc(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function renderDistribution(distribution, totalCount) {
  const el = document.getElementById('review-distribution');
  if (!el) return;
  const rows = [5, 4, 3, 2, 1].map(star => {
    const count = (distribution && distribution[String(star)]) || 0;
    const pct = totalCount ? (count / totalCount) * 100 : 0;
    return `<div class="reviews-distribution-row">
      <span class="stars-label">${star} ★</span>
      <div class="reviews-distribution-bar-track"><div class="reviews-distribution-bar-fill" style="width:${pct}%"></div></div>
      <span class="count">${fmtNum(count)}</span>
    </div>`;
  });
  el.innerHTML = rows.join('');
}

function renderReviews(data) {
  const stats = data.stats || {};
  safeText('review-avg-star', stats.avgStar !== null && stats.avgStar !== undefined ? stats.avgStar.toFixed(2) : '–');
  safeText('review-total-count', fmtNum(stats.totalCount || 0));
  safeText('review-today-count', fmtNum((data.todayReviews || []).length));

  renderDistribution(stats.distribution, stats.totalCount);

  const todayPanel = document.getElementById('reviews-today-panel');
  const todayList = document.getElementById('reviews-today-list');
  const today = data.todayReviews || [];
  if (today.length) {
    todayList.innerHTML = today.map(r => reviewCard(r, { isToday: true })).join('');
    if (todayPanel) todayPanel.style.display = '';
  } else {
    todayList.innerHTML = '<div class="lal-empty-state">Bugün henüz yeni bir yorum eklenmedi.</div>';
  }

  const allList = document.getElementById('reviews-all-list');
  const reviews = data.reviews || [];
  allList.innerHTML = reviews.length
    ? reviews.map(r => reviewCard(r, { isToday: false })).join('')
    : '<div class="lal-empty-state">Henüz senkronize edilmiş yorum yok.</div>';
}

async function loadReviews() {
  const loading = document.getElementById('reviews-loading');
  const content = document.getElementById('reviews-content');
  if (!loading || !content) return;
  loading.classList.remove('is-hidden');
  content.classList.add('is-hidden');
  try {
    const response = await fetch('/api/reviews/overview');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Yorumlar yüklenemedi.');
    renderReviews(data);
    content.classList.remove('is-hidden');
  } catch (error) {
    showError(error.message);
  } finally {
    loading.classList.add('is-hidden');
  }
}

document.addEventListener('lal:data-refresh', loadReviews);
loadReviews();
