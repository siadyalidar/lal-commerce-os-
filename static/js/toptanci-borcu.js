// static/js/toptanci-borcu.js
// ----------------------------------------------------------------------
// Finans sayfasındaki "Toptancı Borcu" sekmesi. Bağımsız, küçük bir sayfa
// script'i (finans-giderler.js / payout-calendar.js ile aynı desen) —
// başka hiçbir modüle bağımlı değil, kendi fetch çağrılarını yapar.
//
// Backend: blueprints/supplier_routes.py + cost_routes.py'deki
// /api/product-cost/<sku>/tedarikci endpoint'i.
// ----------------------------------------------------------------------
(function () {
  var tbody = document.getElementById('tb-tbody');
  var toplamBorcEl = document.getElementById('tb-toplam-borc');
  var supplierNameInput = document.getElementById('tb-supplier-name');
  var supplierAddBtn = document.getElementById('tb-supplier-add');
  var formMsg = document.getElementById('tb-form-msg');

  var skuInput = document.getElementById('tb-sku-input');
  var skuList = document.getElementById('tb-sku-list');
  var skuSupplierSelect = document.getElementById('tb-sku-supplier-select');
  var skuAssignBtn = document.getElementById('tb-sku-assign');
  var backfillBtn = document.getElementById('tb-backfill');
  var skuMsg = document.getElementById('tb-sku-msg');

  var ledgerPanel = document.getElementById('tb-ledger-panel');
  var ledgerTitle = document.getElementById('tb-ledger-title');
  var ledgerTbody = document.getElementById('tb-ledger-tbody');
  var ledgerCloseBtn = document.getElementById('tb-ledger-close');

  if (!tbody) return; // bu sekme DOM'da yoksa (başka sayfa) sessizce çık

  var currencyFmt = new Intl.NumberFormat('tr-TR', {
    style: 'currency', currency: 'TRY', minimumFractionDigits: 2, maximumFractionDigits: 2,
  });

  function showMsg(el, text, isError) {
    if (!el) return;
    el.textContent = text;
    el.classList.remove('is-hidden');
    el.style.color = isError ? 'var(--lal-red)' : 'var(--lal-green)';
    setTimeout(function () { el.classList.add('is-hidden'); }, 4000);
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    // 'YYYY-MM-DD HH:MM:SS' -> DD.MM.YYYY HH:MM
    var parts = iso.split(' ');
    var d = (parts[0] || '').split('-');
    if (d.length !== 3) return iso;
    return d[2] + '.' + d[1] + '.' + d[0] + (parts[1] ? ' ' + parts[1].slice(0, 5) : '');
  }

  function loadSuppliers() {
    fetch('/api/tedarikciler')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        tbody.innerHTML = '';
        skuSupplierSelect.innerHTML = '';

        if (!items.length) {
          tbody.innerHTML = '<tr><td colspan="3" class="lal-status-text">Henüz tedarikçi eklenmedi.</td></tr>';
        }

        items.forEach(function (s) {
          var tr = document.createElement('tr');

          var tdAd = document.createElement('td');
          tdAd.textContent = s.ad;
          tr.appendChild(tdAd);

          var tdBakiye = document.createElement('td');
          tdBakiye.textContent = currencyFmt.format(s.bakiye || 0);
          tdBakiye.style.color = (s.bakiye || 0) > 0 ? 'var(--lal-amber)' : 'var(--lal-text-muted)';
          tr.appendChild(tdBakiye);

          var tdActions = document.createElement('td');

          var payBtn = document.createElement('button');
          payBtn.className = 'lal-btn';
          payBtn.textContent = 'Ödeme Ekle';
          payBtn.addEventListener('click', function () { addPayment(s.id, s.ad); });
          tdActions.appendChild(payBtn);

          var ledgerBtn = document.createElement('button');
          ledgerBtn.className = 'lal-btn';
          ledgerBtn.textContent = 'Hareketler';
          ledgerBtn.style.marginLeft = '8px';
          ledgerBtn.addEventListener('click', function () { showLedger(s.id, s.ad); });
          tdActions.appendChild(ledgerBtn);

          var delBtn = document.createElement('button');
          delBtn.className = 'lal-btn';
          delBtn.textContent = 'Sil';
          delBtn.style.marginLeft = '8px';
          delBtn.addEventListener('click', function () { deleteSupplier(s.id, s.ad); });
          tdActions.appendChild(delBtn);

          tr.appendChild(tdActions);
          tbody.appendChild(tr);

          var opt = document.createElement('option');
          opt.value = s.id;
          opt.textContent = s.ad;
          skuSupplierSelect.appendChild(opt);
        });

        toplamBorcEl.textContent = currencyFmt.format(data.toplam_borc || 0);
      })
      .catch(function () {
        tbody.innerHTML = '<tr><td colspan="3" class="lal-status-text">Yüklenemedi.</td></tr>';
      });
  }

  function addSupplier() {
    var ad = (supplierNameInput.value || '').trim();
    if (!ad) {
      showMsg(formMsg, 'Tedarikçi adı gerekli.', true);
      return;
    }
    fetch('/api/tedarikciler', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ad: ad }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showMsg(formMsg, data.error, true); return; }
        supplierNameInput.value = '';
        showMsg(formMsg, 'Tedarikçi eklendi: ' + data.ad, false);
        loadSuppliers();
      })
      .catch(function () { showMsg(formMsg, 'Eklenemedi.', true); });
  }

  function deleteSupplier(id, ad) {
    if (!confirm('"' + ad + '" silinsin mi? Borç geçmişi de silinir, SKU ataması kalkar.')) return;
    fetch('/api/tedarikciler/' + id, { method: 'DELETE' })
      .then(function () { loadSuppliers(); });
  }

  function addPayment(id, ad) {
    var tutarStr = prompt('"' + ad + '" için ödeme tutarı (₺):');
    if (tutarStr === null) return;
    var tutar = parseFloat(tutarStr.replace(',', '.'));
    if (isNaN(tutar) || tutar <= 0) {
      alert('Geçerli bir tutar girmelisin.');
      return;
    }
    var aciklama = prompt('Açıklama (opsiyonel):') || '';
    fetch('/api/tedarikciler/' + id + '/odeme', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tutar: tutar, aciklama: aciklama }),
    })
      .then(function (r) { return r.json(); })
      .then(function () { loadSuppliers(); });
  }

  function showLedger(id, ad) {
    ledgerTitle.textContent = 'Hareketler — ' + ad;
    ledgerTbody.innerHTML = '<tr><td colspan="4" class="lal-status-text">Yükleniyor…</td></tr>';
    ledgerPanel.classList.remove('is-hidden');
    ledgerPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    fetch('/api/tedarikciler/' + id + '/hareketler')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        ledgerTbody.innerHTML = '';
        if (!items.length) {
          ledgerTbody.innerHTML = '<tr><td colspan="4" class="lal-status-text">Hareket yok.</td></tr>';
          return;
        }
        items.forEach(function (h) {
          var tr = document.createElement('tr');
          var tdTarih = document.createElement('td'); tdTarih.textContent = fmtDate(h.tarih); tr.appendChild(tdTarih);
          var tdTip = document.createElement('td');
          tdTip.textContent = h.tip === 'satis' ? 'Satış (borç)' : 'Ödeme';
          tdTip.style.color = h.tip === 'satis' ? 'var(--lal-amber)' : 'var(--lal-green)';
          tr.appendChild(tdTip);
          var tdTutar = document.createElement('td'); tdTutar.textContent = currencyFmt.format(h.tutar || 0); tr.appendChild(tdTutar);
          var tdAciklama = document.createElement('td'); tdAciklama.textContent = h.aciklama || '—'; tr.appendChild(tdAciklama);
          ledgerTbody.appendChild(tr);
        });
      });
  }

  function loadSkuOptions() {
    fetch('/api/product-costs')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = data.items || [];
        skuList.innerHTML = '';
        items.forEach(function (p) {
          if (!p.sku) return;
          var opt = document.createElement('option');
          opt.value = p.sku;
          opt.textContent = p.product_name || p.sku;
          skuList.appendChild(opt);
        });
      });
  }

  function assignSkuSupplier() {
    var sku = (skuInput.value || '').trim();
    var tedarikciId = skuSupplierSelect.value;
    if (!sku) { showMsg(skuMsg, 'SKU gerekli.', true); return; }
    if (!tedarikciId) { showMsg(skuMsg, 'Önce en az bir tedarikçi ekle.', true); return; }

    fetch('/api/product-cost/' + encodeURIComponent(sku) + '/tedarikci', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tedarikci_id: parseInt(tedarikciId, 10) }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { showMsg(skuMsg, data.error, true); return; }
        showMsg(skuMsg, sku + ' atandı. Geçmiş satışları da saymak için "Geçmişi Tara"ya bas.', false);
        skuInput.value = '';
      })
      .catch(function () { showMsg(skuMsg, 'Atama başarısız.', true); });
  }

  function backfill() {
    backfillBtn.disabled = true;
    backfillBtn.textContent = 'Taranıyor…';
    fetch('/api/tedarikciler/backfill', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        showMsg(skuMsg, (data.taranan_satir || 0) + ' satır tarandı, bakiyeler güncellendi.', false);
        loadSuppliers();
      })
      .finally(function () {
        backfillBtn.disabled = false;
        backfillBtn.textContent = 'Geçmişi Tara';
      });
  }

  supplierAddBtn.addEventListener('click', addSupplier);
  skuAssignBtn.addEventListener('click', assignSkuSupplier);
  backfillBtn.addEventListener('click', backfill);
  ledgerCloseBtn.addEventListener('click', function () { ledgerPanel.classList.add('is-hidden'); });

  // Sekmeye ilk kez geçildiğinde değil, sayfa yüklenirken hemen çekiyoruz —
  // sekme gizli olsa da veri hazır bekliyor, geçince ekstra bekleme olmuyor.
  loadSuppliers();
  loadSkuOptions();
})();
