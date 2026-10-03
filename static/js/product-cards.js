(function () {
  'use strict';
  var wrap = document.getElementById('margins-table-wrap');
  if (!wrap) return;
  var panel = wrap.closest('.panel') || wrap.parentNode;
  var KEY = 'lal-products-view';
  var view = 'cards';
  try { view = localStorage.getItem(KEY) || 'cards'; } catch (e) {}
  var items = [], first = true, openSku = null;
  var sortBy = 'profit', sortOrder = 'desc';

  var esc = function (v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var tl = function (v) { return typeof fmtTL === 'function' ? fmtTL(v) : String(v); };
  var num = function (v) { return typeof fmtNum === 'function' ? fmtNum(v) : String(v); };
  var pct = function (v) { return v == null ? '–' : (typeof fmtPct === 'function' ? fmtPct(v) : String(v)); };
  var el = function (html) { var d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstChild; };

  var bar = el('<div class="lal-pc-bar"><div class="lal-pc-seg" role="group" aria-label="Görünüm"><button type="button" data-view="cards">Kartlar</button><button type="button" data-view="table">Tablo</button></div><div class="lal-pc-sort" role="group" aria-label="Sıralama"><span class="lal-pc-sort-label">Sırala</span><button type="button" data-sort="profit">Kâr</button><button type="button" data-sort="revenue">Ciro</button><button type="button" data-sort="margin">Marj</button><button type="button" data-sort="quantity">Adet</button></div></div>');
  var grid = el('<div class="lal-pc-grid" id="lal-pc-grid"></div>');
  wrap.parentNode.insertBefore(bar, wrap);
  wrap.parentNode.insertBefore(grid, wrap);

  var overlay = el('<div class="lal-pc-overlay"></div>');
  var drawer = el('<aside class="lal-pc-drawer" role="dialog" aria-modal="true" aria-label="Ürün maliyeti"></aside>');
  document.body.appendChild(overlay);
  document.body.appendChild(drawer);

  function applyView() {
    panel.classList.toggle('lal-pc-mode-cards', view === 'cards');
    bar.querySelectorAll('[data-view]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.view === view));
    });
  }

  function markSort() {
    bar.querySelectorAll('[data-sort]').forEach(function (b) {
      var on = b.dataset.sort === sortBy;
      b.classList.toggle('is-active', on);
      if (on) b.dataset.dir = sortOrder; else b.removeAttribute('data-dir');
    });
  }

  function status(i) {
    if (!i.hasCost) return 'missing';
    if (i.margin != null && i.margin < 0) return 'loss';
    if (i.margin != null && i.margin < 0.10) return 'low';
    return 'ok';
  }

  function media(i) {
    var ph = esc((i.sku || '?').slice(0, 2).toUpperCase());
    return i.imageUrl
      ? '<img src="' + esc(i.imageUrl) + '" alt="" loading="lazy" referrerpolicy="no-referrer" data-ph="' + ph + '">'
      : '<span class="lal-pc-ph">' + ph + '</span>';
  }

  function cardHtml(i, idx) {
    var st = status(i);
    var badgeTxt = st === 'missing' ? 'EKSİK' : pct(i.margin);
    var profitCls = (i.profit || 0) >= 0 ? 'is-pos' : 'is-neg';
    var w = i.margin == null ? 0 : Math.max(0, Math.min(1, i.margin / 0.4)) * 100;
    var color = st === 'loss' ? 'var(--lal-red)' : (st === 'low' ? 'var(--lal-amber)' : 'var(--lal-green)');
    var foot = st === 'missing'
      ? '<div class="lal-pc-cta">MALİYET GİR →</div>'
      : '<div class="lal-pc-foot"><span>Marj</span><span>' + esc(pct(i.margin)) + '</span></div><div class="lal-pc-meter"><i style="--w:' + w.toFixed(1) + '%;--c:' + color + '"></i></div>';
    return '<button type="button" class="lal-pc-card" data-sku="' + esc(i.sku) + '" style="--i:' + Math.min(idx, 18) + '">' +
      '<div class="lal-pc-media">' + media(i) + '<span class="lal-pc-badge is-' + st + '">' + esc(badgeTxt) + '</span></div>' +
      '<div class="lal-pc-body"><h3 class="lal-pc-name">' + esc(i.productName || i.sku || '') + '</h3>' +
      '<div class="lal-pc-sku">' + esc(i.sku || '–') + '</div><div class="lal-pc-perf"></div>' +
      '<div class="lal-pc-stats"><div><span>Ciro</span><b>' + esc(tl(i.revenue)) + '</b></div>' +
      '<div><span>Kâr</span><b class="' + profitCls + '">' + esc(tl(i.profit)) + '</b></div>' +
      '<div><span>Adet</span><b>' + esc(num(i.quantity)) + '</b></div></div>' + foot + '</div></button>';
  }

  function render(list) {
    items = list || [];
    if (!items.length) {
      grid.innerHTML = '<div class="lal-pc-empty">Bu filtreye uyan ürün yok.</div>';
    } else {
      grid.innerHTML = items.map(cardHtml).join('');
      if (first) {
        grid.classList.add('is-enter');
        setTimeout(function () { grid.classList.remove('is-enter'); }, 1800);
        first = false;
      }
    }
    if (openSku) {
      var cur = items.filter(function (x) { return x.sku === openSku; })[0];
      if (cur) drawer.innerHTML = drawerHtml(cur); else closeDrawer();
    }
  }

  function loading() {
    var s = '<div class="lal-pc-card lal-pc-skel"><div class="lal-pc-media"></div><div class="lal-pc-body"><i style="width:80%"></i><i style="width:35%"></i><div class="lal-pc-perf"></div><i style="width:100%"></i></div></div>';
    var out = '';
    for (var k = 0; k < 8; k++) out += s;
    grid.innerHTML = out;
  }

  function error(msg) { grid.innerHTML = '<div class="lal-pc-empty">' + esc(msg) + '</div>'; }

  function drawerHtml(i) {
    var st = status(i);
    var profitCls = (i.profit || 0) >= 0 ? 'is-pos' : 'is-neg';
    var mCls = st === 'loss' ? 'is-neg' : '';
    return '<div class="lal-pc-d-head"><span>Ürün maliyeti</span><button type="button" class="lal-pc-x" data-act="close" aria-label="Kapat">×</button></div>' +
      '<div class="lal-pc-media">' + media(i) + '</div>' +
      '<div><h3 class="lal-pc-d-name">' + esc(i.productName || i.sku || '') + '</h3><div class="lal-pc-sku">' + esc(i.sku || '–') + '</div></div>' +
      '<div class="lal-pc-d-stats"><div><span>Adet</span><b>' + esc(num(i.quantity)) + '</b></div><div><span>Ciro</span><b>' + esc(tl(i.revenue)) + '</b></div>' +
      '<div><span>Kâr</span><b class="' + profitCls + '">' + esc(tl(i.profit)) + '</b></div><div><span>Marj</span><b class="' + mCls + '">' + esc(pct(i.margin)) + '</b></div></div>' +
      '<div class="lal-pc-d-cost"><label for="lal-pc-cost">Maliyet (KDV dahil)</label>' +
      '<input type="number" step="0.01" id="lal-pc-cost" placeholder="Örn. 2145" value="' + esc(i.hasCost && i.costInclVat != null ? i.costInclVat : '') + '">' +
      '<div class="lal-pc-d-actions"><button type="button" class="lal-pc-btn is-primary" data-act="save">' + (i.hasCost ? 'Güncelle' : 'Kaydet') + '</button>' +
      (i.hasCost ? '<button type="button" class="lal-pc-btn is-danger" data-act="delete">Sil</button>' : '') + '</div></div>';
  }

  function openDrawer(sku) {
    var cur = items.filter(function (x) { return x.sku === sku; })[0];
    if (!cur) return;
    openSku = sku;
    drawer.innerHTML = drawerHtml(cur);
    overlay.classList.add('is-open');
    drawer.classList.add('is-open');
    setTimeout(function () { var inp = drawer.querySelector('#lal-pc-cost'); if (inp) inp.focus(); }, 300);
  }

  function closeDrawer() {
    openSku = null;
    overlay.classList.remove('is-open');
    drawer.classList.remove('is-open');
  }

  function rowNode(cls, sku) { return document.querySelector(cls + '[data-sku="' + CSS.escape(sku) + '"]'); }

  grid.addEventListener('click', function (e) {
    var c = e.target.closest('.lal-pc-card');
    if (c && c.dataset.sku) openDrawer(c.dataset.sku);
  });
  grid.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG') {
      var s = document.createElement('span');
      s.className = 'lal-pc-ph';
      s.textContent = t.dataset.ph || '?';
      t.replaceWith(s);
    }
  }, true);
  drawer.addEventListener('error', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG') {
      var s = document.createElement('span');
      s.className = 'lal-pc-ph';
      s.textContent = t.dataset.ph || '?';
      t.replaceWith(s);
    }
  }, true);

  drawer.addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]');
    if (!t) return;
    var act = t.dataset.act;
    if (act === 'close') { closeDrawer(); return; }
    var sku = openSku;
    if (!sku) return;
    if (act === 'save') {
      var inp = drawer.querySelector('#lal-pc-cost');
      var v = parseFloat(inp.value);
      if (!v || v <= 0) { inp.classList.add('is-invalid'); return; }
      inp.classList.remove('is-invalid');
      var rowInp = rowNode('.inline-cost-input', sku);
      var rowBtn = rowNode('.inline-cost-save', sku);
      if (!rowInp || !rowBtn) return;
      rowInp.value = inp.value;
      var orig = t.textContent;
      t.disabled = true; t.textContent = '…';
      rowBtn.click();
      setTimeout(function () { if (t.disabled) { t.disabled = false; t.textContent = orig; } }, 6000);
    } else if (act === 'delete') {
      var d = rowNode('.inline-cost-delete', sku);
      if (d) d.click();
    }
  });
  drawer.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target.id === 'lal-pc-cost') {
      var b = drawer.querySelector('[data-act="save"]');
      if (b) b.click();
    }
  });
  overlay.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && openSku) closeDrawer(); });

  bar.querySelectorAll('[data-view]').forEach(function (b) {
    b.addEventListener('click', function () {
      view = b.dataset.view;
      try { localStorage.setItem(KEY, view); } catch (e) {}
      applyView();
    });
  });
  bar.querySelectorAll('[data-sort]').forEach(function (b) {
    b.addEventListener('click', function () {
      var th = document.querySelector('#margins-table-wrap th[data-sort="' + b.dataset.sort + '"]');
      if (th) th.click();
    });
  });
  document.querySelectorAll('#margins-table-wrap th[data-sort]').forEach(function (th) {
    th.addEventListener('click', function () {
      var f = th.dataset.sort;
      if (sortBy === f) sortOrder = sortOrder === 'desc' ? 'asc' : 'desc';
      else { sortBy = f; sortOrder = 'desc'; }
      markSort();
    });
  });

  window.lalProductCards = { render: render, loading: loading, error: error };
  applyView();
  markSort();
})();
