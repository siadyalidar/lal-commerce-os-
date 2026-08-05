// =====================================================================
// FİNANS — Sabit Giderler formu ve tablosu
// Faz 7.1: dashboard.js'ten ayrıştırıldı, yalnızca Finans sayfasında yüklenir.
// shell.js'teki ortak yardımcılara bağımlıdır.
// =====================================================================
(function () {
  if (!document.getElementById('fe-tbody')) return; // bu sayfada değiliz

  const ICON_CHECK_SM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;width:13px;height:13px;"><polyline points="20 6 9 17 4 12"/></svg>';

  function fmtMonthLabel(month) {
    const [y, mo] = month.split('-');
    return new Date(y, mo - 1, 1).toLocaleDateString('tr-TR', { month: 'long', year: 'numeric' });
  }

  function renderFixedExpensesTable(items) {
    const tbody = document.getElementById('fe-tbody');
    if (!items.length) {
      tbody.innerHTML = emptyStateRow(5, 'Henüz sabit gider girilmemiş.');
      return;
    }
    tbody.innerHTML = items.map(it => `
      <tr>
        <td>${fmtMonthLabel(it.month)}</td>
        <td>${it.label}</td>
        <td>${fmtTL(it.amount)}</td>
        <td style="color:var(--text-muted);">${it.note || ''}</td>
        <td><button class="inline-save-btn fe-delete" data-id="${it.id}" style="background:none; color:var(--red); border-color:var(--red);">Sil</button></td>
      </tr>
    `).join('');

    tbody.querySelectorAll('.fe-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Bu sabit gider kalemini silmek istediğine emin misin?')) return;
        btn.disabled = true; btn.textContent = '…';
        try {
          await fetch(`/api/fixed-expenses/${btn.dataset.id}`, { method: 'DELETE' });
          await loadFixedExpenses();
          // Aylık Kâr Trendi grafiği bu sayfada değil (Gösterge Paneli'nde);
          // orada açıksa bir sonraki ziyarette güncel veriyle yeniden çekilir.
        } catch (e) {
          showError('Sabit gider silinemedi: ' + e.message);
          btn.disabled = false; btn.textContent = 'Sil';
        }
      });
    });
  }

  async function loadFixedExpenses() {
    safeDisplay('fe-loading', 'none');
    safeDisplay('fe-table-wrap', 'block');
    document.getElementById('fe-tbody').innerHTML = skeletonTableRows(5, 3, [18, 22, 14, 30, 10]);
    try {
      const res = await fetch('/api/fixed-expenses');
      const data = await res.json();
      if (data.error) {
        showError(data.error);
        document.getElementById('fe-tbody').innerHTML = errorStateRow(5, data.error);
        return;
      }
      renderFixedExpensesTable(data.items || []);
    } catch (e) {
      showError('Sabit giderler alınamadı: ' + e.message);
      document.getElementById('fe-tbody').innerHTML = errorStateRow(5, e.message);
    }
  }

  onExist('fe-submit', 'click', async () => {
    const month = document.getElementById('fe-month').value;
    const label = document.getElementById('fe-label').value.trim();
    const amount = document.getElementById('fe-amount').value;
    const note = document.getElementById('fe-note').value.trim();
    const msgEl = document.getElementById('fe-form-msg');

    if (!month || !label || !amount) {
      msgEl.classList.remove('is-hidden');
      msgEl.style.color = 'var(--red)';
      msgEl.textContent = 'Ay, kalem ve tutar alanları zorunlu.';
      return;
    }

    try {
      const res = await fetch('/api/fixed-expenses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ month, label, amount: parseFloat(amount), note: note || undefined }),
      });
      const data = await res.json();
      msgEl.classList.remove('is-hidden');
      if (data.error) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = data.error;
      } else {
        msgEl.style.color = 'var(--green)';
        msgEl.innerHTML = `${ICON_CHECK_SM}<span style="vertical-align:middle; margin-left:5px;">"${label}" kaydedildi.</span>`;
        document.getElementById('fe-label').value = '';
        document.getElementById('fe-amount').value = '';
        document.getElementById('fe-note').value = '';
        await loadFixedExpenses();
      }
    } catch (e) {
      msgEl.classList.remove('is-hidden');
      msgEl.style.color = 'var(--red)';
      msgEl.textContent = 'Kaydetme hatası: ' + e.message;
    }
  });

  // Ay seçiciye varsayılan olarak içinde bulunulan ayı koy.
  (function setDefaultFeMonth() {
    const feMonth = document.getElementById('fe-month');
    if (!feMonth) return;
    const now = new Date();
    feMonth.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  })();

  // ---------- Başlangıç ----------
  loadFixedExpenses();
})();
