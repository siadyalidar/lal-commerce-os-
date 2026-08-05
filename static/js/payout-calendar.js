// =====================================================================
// HAKEDİŞ TAKVİMİ
// =====================================================================
(function () {
  const PC_MP_LABEL = { trendyol: 'Trendyol', hepsiburada: 'Hepsiburada' };

  let pcAllDays = [];
  let pcUnplanned = null;
  let pcLoaded = false;

  const now = new Date();
  let pcViewYear = now.getFullYear();
  let pcViewMonth = now.getMonth(); // 0-indexli

  function pcFormatTL(n) {
    const sign = n < 0 ? '-' : '';
    return sign + Math.abs(Math.round(n)).toLocaleString('tr-TR') + ' ₺';
  }

  function pcDateKey(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function pcStartOfWeek(date) {
    // Pazartesi başlangıçlı hafta
    const d = new Date(date);
    const dow = (d.getDay() + 6) % 7; // Pazartesi = 0
    d.setDate(d.getDate() - dow);
    d.setHours(0, 0, 0, 0);
    return d;
  }

  function pcDayTotals(d) {
    let c = 0, o = 0, e = 0, l = 0, t = 0;
    Object.values(d.byMarketplace || {}).forEach(function (v) {
      c += v.confirmed || 0;
      o += v.official || 0;
      e += v.estimated || 0;
      l += v.lagEstimated || 0;
      t += v.total || 0;
    });
    return { confirmed: c, official: o, estimated: e, lagEstimated: l, total: t };
  }

  async function pcLoad() {
    const statusEl = document.getElementById('pcStatus');
    statusEl.textContent = 'Yükleniyor…';
    try {
      const mp = document.getElementById('pcMarketplaceFilter').value;
      const url = '/api/payout-calendar' + (mp ? ('?marketplace=' + encodeURIComponent(mp)) : '');
      const res = await fetch(url);
      const data = await res.json();
      if (data.error) {
        statusEl.textContent = 'Hata: ' + data.error;
        return;
      }
      pcAllDays = data.days || [];
      pcUnplanned = data.unplanned || null;
      statusEl.textContent = '';
      pcRenderWeekSummary();
      pcRenderCalendar();
      pcRenderUnplannedNote();
    } catch (e) {
      statusEl.textContent = 'Hata: veriler yüklenemedi.';
    }
  }

  function pcRenderWeekSummary() {
    const container = document.getElementById('pcWeekSummary');
    const thisWeekStart = pcStartOfWeek(new Date());
    const nextWeekStart = new Date(thisWeekStart); nextWeekStart.setDate(nextWeekStart.getDate() + 7);
    const weekAfterStart = new Date(nextWeekStart); weekAfterStart.setDate(weekAfterStart.getDate() + 7);
    const todayStart = new Date(); todayStart.setHours(0, 0, 0, 0);
    const farFuture = new Date(9999, 0, 1);

    function sumRange(start, end) {
      const sum = { confirmed: 0, official: 0, estimated: 0, lagEstimated: 0, total: 0 };
      pcAllDays.forEach(function (d) {
        const dt = new Date(d.date + 'T00:00:00');
        if (dt >= start && dt < end) {
          const t = pcDayTotals(d);
          sum.confirmed += t.confirmed;
          sum.official += t.official;
          sum.estimated += t.estimated;
          sum.lagEstimated += t.lagEstimated;
          sum.total += t.total;
        }
      });
      return sum;
    }

    const thisWeek = sumRange(thisWeekStart, nextWeekStart);
    const nextWeek = sumRange(nextWeekStart, weekAfterStart);
    // Bu haftadan itibaren, DB'de bilinen ne kadar gelecek varsa hepsi
    // (2 haftayla sınırlı değil) — tüm pazaryerleri.
    const allFuture = sumRange(thisWeekStart, farFuture);

    function card(title, sum) {
      return (
        '<div class="pc-week-card">' +
        '<div class="pc-week-card-title">' + title + '</div>' +
        '<div class="pc-week-card-total">' + pcFormatTL(sum.total) + '</div>' +
        '<div class="pc-week-card-breakdown">' +
        '<div><span class="pc-legend-dot pc-dot-confirmed"></span>Kesinleşmiş: ' + pcFormatTL(sum.confirmed) + '</div>' +
        '<div><span class="pc-legend-dot pc-dot-official"></span>Pazaryeri resmi verisi: ' + pcFormatTL(sum.official) + '</div>' +
        '<div><span class="pc-legend-dot pc-dot-estimated"></span>Bizim tahminimiz (tarihli): ' + pcFormatTL(sum.estimated) + '</div>' +
        '<div><span class="pc-legend-dot pc-dot-lag"></span>Bizim tahminimiz (kaba): ' + pcFormatTL(sum.lagEstimated) + '</div>' +
        '</div></div>'
      );
    }

    container.innerHTML = card('Bu Hafta (' + pcDateKey(thisWeekStart) + ' – ' + pcDateKey(new Date(nextWeekStart - 86400000)) + ')', thisWeek)
      + card('Gelecek Hafta (' + pcDateKey(nextWeekStart) + ' – ' + pcDateKey(new Date(weekAfterStart - 86400000)) + ')', nextWeek)
      + card('Tüm Gelecek Ödemeler (bugünden itibaren, tüm pazaryerleri)', allFuture);
  }

  function pcRenderCalendar() {
    const grid = document.getElementById('pcCalendarGrid');
    const label = document.getElementById('pcMonthLabel');
    const monthNames = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
    label.textContent = monthNames[pcViewMonth] + ' ' + pcViewYear;

    const firstOfMonth = new Date(pcViewYear, pcViewMonth, 1);
    const startOffset = (firstOfMonth.getDay() + 6) % 7; // Pazartesi = 0
    const daysInMonth = new Date(pcViewYear, pcViewMonth + 1, 0).getDate();

    const dayMap = {};
    pcAllDays.forEach(function (d) { dayMap[d.date] = d; });

    let headerHtml = '<div class="pc-grid-header">';
    ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz'].forEach(function (w) {
      headerHtml += '<div class="pc-grid-h">' + w + '</div>';
    });
    headerHtml += '</div>';

    let bodyHtml = '<div class="pc-grid-body">';
    for (let i = 0; i < startOffset; i++) {
      bodyHtml += '<div class="pc-cell pc-cell-empty"></div>';
    }

    const todayKey = pcDateKey(new Date());
    for (let day = 1; day <= daysInMonth; day++) {
      const dateObj = new Date(pcViewYear, pcViewMonth, day);
      const key = pcDateKey(dateObj);
      const d = dayMap[key];
      const isToday = key === todayKey;

      let cellClass = 'pc-cell';
      if (isToday) cellClass += ' pc-cell-today';
      let inner = '<div class="pc-cell-daynum">' + day + '</div>';

      if (d) {
        const t = pcDayTotals(d);
        // Öncelik: confirmed (kesin banka verisi) > official (pazaryerinin
        // kendi beyanı) > estimated (tarihli tahminimiz) > lag (kaba tahmin).
        let dominant = 'estimated';
        if (t.confirmed !== 0) dominant = 'confirmed';
        else if (t.official !== 0) dominant = 'official';
        else if (t.estimated !== 0) dominant = 'estimated';
        else if (t.lagEstimated !== 0) dominant = 'lag';
        cellClass += ' pc-cell-' + dominant;

        const overdue = Object.values(d.byMarketplace || {}).some(function (v) { return v.overdue; });
        if (overdue) cellClass += ' pc-cell-overdue';

        inner += '<div class="pc-cell-amount">' + pcFormatTL(t.total) + '</div>';
        const mpParts = Object.keys(d.byMarketplace).map(function (mp) {
          return (PC_MP_LABEL[mp] || mp) + ': ' + pcFormatTL(d.byMarketplace[mp].total);
        });
        inner += '<div class="pc-cell-mp">' + mpParts.join(' · ') + '</div>';
      }

      bodyHtml += '<div class="' + cellClass + '" title="' + key + '">' + inner + '</div>';
    }
    bodyHtml += '</div>';

    grid.innerHTML = headerHtml + bodyHtml;
  }

  function pcRenderUnplannedNote() {
    const el = document.getElementById('pcUnplannedNote');
    if (!pcUnplanned || !pcUnplanned.total) {
      el.textContent = '';
      return;
    }
    const parts = Object.keys(pcUnplanned.byMarketplace || {}).map(function (mp) {
      return (PC_MP_LABEL[mp] || mp) + ': ' + pcFormatTL(pcUnplanned.byMarketplace[mp]);
    });
    el.textContent = 'Tarihi hiç tahmin edilemeyen (işlem tarihi de eksik) ' + pcFormatTL(pcUnplanned.total) + ' tutar takvime dahil edilmedi. (' + parts.join(', ') + ')';
  }

  document.getElementById('pcPrevMonth').addEventListener('click', function () {
    pcViewMonth--;
    if (pcViewMonth < 0) { pcViewMonth = 11; pcViewYear--; }
    pcRenderCalendar();
  });
  document.getElementById('pcNextMonth').addEventListener('click', function () {
    pcViewMonth++;
    if (pcViewMonth > 11) { pcViewMonth = 0; pcViewYear++; }
    pcRenderCalendar();
  });
  document.getElementById('pcMarketplaceFilter').addEventListener('change', pcLoad);
  document.getElementById('pcRefresh').addEventListener('click', pcLoad);

  // NOT (Faz 7.2): Pazaryeri resmi verisi kimlik bilgisi (credential)
  // kaydetme + manuel çekim kodu artık burada değil — Ayarlar sayfasına
  // taşındı (bkz. static/js/ayarlar-credentials.js). Bu dosya yalnızca
  // takvimin kendisini (görüntüleme + ay gezinme + marketplace filtresi)
  // yönetiyor.

  // Faz 5: bu bölüm artık Finans sayfasının bir sekmesi — accordion'a bağlı
  // 'toggle' tetiklemesi yerine, sayfa yüklenince doğrudan bir kez veri çekiyoruz.
  if (document.getElementById('details-payout-calendar') && !pcLoaded) {
    pcLoaded = true;
    pcLoad();
  }
})();
