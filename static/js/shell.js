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
