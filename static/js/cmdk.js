(function () {
  'use strict';

  function norm(s) {
    return String(s || '').toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }
  function click(id) { var e = document.getElementById(id); if (e) e.click(); }

  var overlay = document.createElement('div');
  overlay.className = 'lal-cmdk-overlay';
  overlay.innerHTML = '<div class="lal-cmdk" role="dialog" aria-modal="true" aria-label="Komut paleti">' +
    '<input class="lal-cmdk-input" type="text" placeholder="Sayfa veya eylem ara…" aria-label="Ara" autocomplete="off" spellcheck="false">' +
    '<ul class="lal-cmdk-list" role="listbox"></ul>' +
    '<div class="lal-cmdk-foot"><span>↑↓ Gez</span><span>↵ Seç</span><span>Esc Kapat</span></div></div>';
  document.body.appendChild(overlay);
  var input = overlay.querySelector('.lal-cmdk-input');
  var list = overlay.querySelector('.lal-cmdk-list');
  var all = [], shown = [], idx = 0, prev = null;

  function build() {
    var out = [];
    document.querySelectorAll('#lal-sidebar a.lal-nav-item').forEach(function (a) {
      var l = a.querySelector('.lal-nav-label');
      if (l) out.push({ g: 'Sayfalar', t: l.textContent.trim(), k: 'git sayfa', run: function () { location.href = a.getAttribute('href'); } });
    });
    out.push({ g: 'Filtreler', t: 'Zararına satılan ürünler', k: 'urunler zarar negatif', run: function () { location.href = '/urunler?filter=negative'; } });
    out.push({ g: 'Filtreler', t: 'Maliyeti eksik ürünler', k: 'urunler maliyet eksik', run: function () { location.href = '/urunler?filter=missing-cost'; } });
    out.push({ g: 'Filtreler', t: 'Düşük marjlı ürünler', k: 'urunler marj dusuk', run: function () { location.href = '/urunler?filter=low-margin'; } });
    if (document.getElementById('sync-btn')) out.push({ g: 'Eylemler', t: 'Verileri senkronize et', k: 'sync yenile', run: function () { click('sync-btn'); } });
    if (document.getElementById('lal-theme-toggle')) out.push({ g: 'Eylemler', t: 'Temayı değiştir', k: 'acik koyu tema', run: function () { click('lal-theme-toggle'); } });
    if (document.getElementById('sidebar-collapse-btn')) out.push({ g: 'Eylemler', t: 'Menüyü daralt / genişlet', k: 'kenar cubugu sidebar', run: function () { click('sidebar-collapse-btn'); } });
    document.querySelectorAll('#mp-switch .mp-switch-btn').forEach(function (b) {
      out.push({ g: 'Pazaryeri', t: 'Pazaryeri: ' + b.textContent.trim(), k: 'filtre', run: function () { b.click(); } });
    });
    var sel = document.getElementById('range-select');
    if (sel) Array.prototype.forEach.call(sel.options, function (o) {
      out.push({ g: 'Tarih aralığı', t: 'Tarih: ' + o.textContent.trim(), k: 'aralik filtre', run: function () {
        sel.value = o.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      } });
    });
    return out;
  }

  function render() {
    var q = norm(input.value).split(/\s+/).filter(Boolean);
    shown = all.filter(function (it) {
      var hay = norm(it.t + ' ' + it.k + ' ' + it.g);
      return q.every(function (w) { return hay.indexOf(w) !== -1; });
    });
    if (idx >= shown.length) idx = 0;
    if (!shown.length) { list.innerHTML = '<li class="lal-cmdk-empty">Sonuç yok</li>'; return; }
    var html = '', last = '';
    shown.forEach(function (it, i) {
      if (it.g !== last) { html += '<li class="lal-cmdk-group">' + it.g + '</li>'; last = it.g; }
      html += '<li class="lal-cmdk-item' + (i === idx ? ' is-active' : '') + '" role="option" data-i="' + i + '"><span></span></li>';
    });
    list.innerHTML = html;
    list.querySelectorAll('.lal-cmdk-item').forEach(function (li) {
      li.firstChild.textContent = shown[+li.dataset.i].t;
    });
    var act = list.querySelector('.is-active');
    if (act) act.scrollIntoView({ block: 'nearest' });
  }

  function open() {
    prev = document.activeElement;
    all = build(); idx = 0; input.value = '';
    render();
    overlay.classList.add('is-open');
    setTimeout(function () { input.focus(); }, 30);
  }
  function close() {
    overlay.classList.remove('is-open');
    if (prev && prev.focus) prev.focus();
  }
  function run(i) {
    var it = shown[i];
    if (!it) return;
    close();
    setTimeout(it.run, 60);
  }

  input.addEventListener('input', function () { idx = 0; render(); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(shown.length - 1, idx + 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(0, idx - 1); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); run(idx); }
    else if (e.key === 'Tab') { e.preventDefault(); }
  });
  list.addEventListener('mousemove', function (e) {
    var li = e.target.closest('.lal-cmdk-item');
    if (li && +li.dataset.i !== idx) {
      var cur = list.querySelector('.is-active');
      if (cur) cur.classList.remove('is-active');
      li.classList.add('is-active');
      idx = +li.dataset.i;
    }
  });
  list.addEventListener('click', function (e) {
    var li = e.target.closest('.lal-cmdk-item');
    if (li) run(+li.dataset.i);
  });
  overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (overlay.classList.contains('is-open')) close(); else open();
    } else if (e.key === 'Escape' && overlay.classList.contains('is-open')) {
      close();
    }
  });

  var ctr = document.querySelector('.lal-topbar-controls');
  if (ctr) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'lal-cmdk-btn';
    b.setAttribute('aria-label', 'Komut paletini aç');
    var mac = /Mac|iPhone|iPad/.test(navigator.platform);
    b.innerHTML = '<span>Ara</span><kbd>' + (mac ? '⌘K' : 'Ctrl K') + '</kbd>';
    b.addEventListener('click', open);
    ctr.insertBefore(b, ctr.firstChild);
  }
})();
