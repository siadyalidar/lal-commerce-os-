// Müşteri Soruları paneli. yorumlar.js ile aynı yükleme deseni
// (loading/content toggle, lal:data-refresh dinleyicisi) + panele özel
// olarak: 60sn polling (30.08.2026 kararı) ve iki eylem:
//   1) NEEDS_CLARIFICATION kartları -> fact formu -> /api/qna/resolve-clarification
//   2) hazır taslak kartları -> düzenlenebilir kutu + "Gönderildi" -> /api/qna/finalize
// Draft-only mimari: BU DOSYA Trendyol/HB'ye hiçbir şey göndermez, sadece
// yerel state'i (sent=1) günceller -- gerçek gönderim Sidar'ın elle
// Trendyol panelinde yaptığı işlem.

let qnaPollTimer = null;

function qnaCardTemplate(q) {
  const dateStr = q.source_created_at ? fmtDateShort(q.source_created_at) : '';
  const isClarification = q.needs_clarification === true;
  const badge = isClarification
    ? '<span class="qna-card-badge is-clarification">Bilgi Gerekiyor</span>'
    : (q.draft_text ? '<span class="qna-card-badge is-ready">Taslak Hazır</span>' : '');

  const bodyHtml = isClarification
    ? `<div class="qna-clarification-note">${esc(q.clarification_prompt || 'Bu konuda bilgi tabanında kayıt yok.')}</div>
       <form class="qna-fact-form" data-sku="${esc(q.sku || '')}">
         <label>Konu (opsiyonel)</label>
         <input type="text" class="qna-fact-topic" placeholder="örn. pil, garanti, üretim yeri">
         <label>Cevabınız</label>
         <textarea class="qna-fact-text" placeholder="Bu bilgiyi buraya yazın — bir daha sorulmayacak, kalıcı olarak hatırlanır."></textarea>
         <div class="qna-card-actions">
           <button type="submit" class="qna-btn is-primary">Kaydet ve Taslak Üret</button>
         </div>
         <div class="qna-card-status-note is-hidden"></div>
       </form>`
    : `<textarea class="qna-draft-box" ${q.draft_text ? '' : 'placeholder="Henüz taslak üretilmedi."'}>${esc(q.draft_text || '')}</textarea>
       <div class="qna-card-actions">
         <button type="button" class="qna-btn qna-copy-btn">Kopyala</button>
         <button type="button" class="qna-btn is-primary qna-finalize-btn">Gönderildi Olarak İşaretle</button>
       </div>
       <div class="qna-card-status-note is-hidden"></div>`;

  return `<article class="qna-card ${isClarification ? 'needs-clarification' : ''}"
                    data-marketplace="${esc(q.marketplace)}" data-question-id="${esc(q.question_id)}">
    <div class="qna-card-header">
      <span class="qna-card-sku">${esc(q.sku || 'SKU eşleşmedi')}</span>
      <div style="display:flex;gap:8px;align-items:center;">
        ${badge}
        <span class="qna-card-date">${dateStr}</span>
      </div>
    </div>
    <div class="qna-card-question"><strong>Müşteri sorusu</strong>${esc(q.question_text)}</div>
    ${bodyHtml}
  </article>`;
}

function renderQna(data) {
  const stats = data.stats || {};
  safeText('qna-pending-count', fmtNum(stats.pendingCount || 0));
  safeText('qna-clarification-count', fmtNum(stats.needsClarificationCount || 0));

  const list = document.getElementById('qna-list');
  const questions = data.questions || [];
  list.innerHTML = questions.length
    ? questions.map(qnaCardTemplate).join('')
    : '<div class="lal-empty-state">Bekleyen soru yok. 🎉</div>';
}

async function loadQna() {
  const loading = document.getElementById('qna-loading');
  const content = document.getElementById('qna-content');
  if (!loading || !content) return;
  try {
    const response = await fetch(`/api/qna/overview?marketplace=${currentMarketplace}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Sorular yüklenemedi.');
    renderQna(data);
    content.classList.remove('is-hidden');
    loading.classList.add('is-hidden');
  } catch (error) {
    showError(error.message);
    loading.classList.add('is-hidden');
  }
}

function showCardStatus(card, message, isError) {
  const note = card.querySelector('.qna-card-status-note');
  if (!note) return;
  note.textContent = message;
  note.style.color = isError ? '#d0342c' : 'var(--lal-text-muted)';
  note.classList.remove('is-hidden');
}

async function handleFactSubmit(e) {
  e.preventDefault();
  const form = e.target;
  const card = form.closest('.qna-card');
  const sku = form.dataset.sku;
  const topic = form.querySelector('.qna-fact-topic').value.trim();
  const factText = form.querySelector('.qna-fact-text').value.trim();
  if (!factText) return;

  const submitBtn = form.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Kaydediliyor…';

  try {
    const response = await fetch('/api/qna/resolve-clarification', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku, topic: topic || undefined, fact_text: factText }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Kaydedilemedi.');
    showCardStatus(card, `Kaydedildi — ${data.regenerated || 0} soru için taslak yeniden üretildi.`, false);
    setTimeout(loadQna, 1200);
  } catch (error) {
    showCardStatus(card, error.message, true);
    submitBtn.disabled = false;
    submitBtn.textContent = 'Kaydet ve Taslak Üret';
  }
}

async function handleCopyClick(e) {
  const card = e.target.closest('.qna-card');
  const textarea = card.querySelector('.qna-draft-box');
  try {
    await navigator.clipboard.writeText(textarea.value);
    showCardStatus(card, 'Kopyalandı.', false);
  } catch (error) {
    showCardStatus(card, 'Kopyalanamadı, metni elle seçip kopyalayın.', true);
  }
}

async function handleFinalizeClick(e) {
  const card = e.target.closest('.qna-card');
  const marketplace = card.dataset.marketplace;
  const questionId = card.dataset.questionId;
  const textarea = card.querySelector('.qna-draft-box');
  const finalText = textarea.value.trim();
  if (!finalText) {
    showCardStatus(card, 'Boş taslak gönderilemez.', true);
    return;
  }

  const btn = e.target;
  btn.disabled = true;
  btn.textContent = 'Kaydediliyor…';

  try {
    const response = await fetch('/api/qna/finalize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ marketplace, question_id: questionId, final_text: finalText }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Kaydedilemedi.');
    card.remove();
    loadQna();
  } catch (error) {
    showCardStatus(card, error.message, true);
    btn.disabled = false;
    btn.textContent = 'Gönderildi Olarak İşaretle';
  }
}

document.addEventListener('submit', (e) => {
  if (e.target.classList.contains('qna-fact-form')) handleFactSubmit(e);
});
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('qna-copy-btn')) handleCopyClick(e);
  if (e.target.classList.contains('qna-finalize-btn')) handleFinalizeClick(e);
});

document.addEventListener('lal:data-refresh', loadQna);
loadQna();

// 30.08.2026 kararı: 60 saniyede bir otomatik yenileme. Sayfa başka bir
// panele geçildiğinde (DOM'dan kaldırıldığında) interval'in sonsuza kadar
// çalışmaya devam etmesini önlemek için visibilitychange ile duraklat/devam.
qnaPollTimer = setInterval(() => {
  if (document.getElementById('qna-content')) loadQna();
  else clearInterval(qnaPollTimer);
}, 60000);
