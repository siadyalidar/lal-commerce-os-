(function () {
  'use strict';

  var API = '/api/notifications';
  var POLL_MS = 30000;
  var STRIP_MS = 10000;
  var LAST_KEY = 'lal-notif-last-id';
  var SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  var KINDS = {
    order: { svg: '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>' },
    review: { svg: '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>' },
    profit_final: { svg: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>' }
  };
  var BELL = '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>';
  var FILTERS = [['all', 'Tümü'], ['order', 'Sipariş'], ['review', 'Yorum'], ['profit_final', 'Net kâr']];
  var MP = { trendyol: 'Trendyol', hepsiburada: 'Hepsiburada' };
  var money = new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY' });

  var items = [];
  var unread = 0;
  var filter = 'all';
  var expandedId = null;
  var lastId = null;
  var bell, badge, backdrop, drawer, list, markAll, chips = [];
  var strip = null, stripTimer = null, stripLeft = STRIP_MS, stripStart = 0;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function icon(kind) {
    var s = el('span', 'lal-nicon is-' + kind);
    s.innerHTML = SVG + (KINDS[kind] || KINDS.order).svg + '</svg>';
    return s;
  }

  function getLast() {
    try { var v = localStorage.getItem(LAST_KEY); return v === null ? null : (parseInt(v, 10) || 0); } catch (e) { return null; }
  }
  function setLast(n) { try { localStorage.setItem(LAST_KEY, String(n)); } catch (e) {} }

  function parseTs(s) {
    if (!s) return null;
    var d = new Date(String(s).replace(' ', 'T'));
    return isNaN(d.getTime()) ? null : d;
  }
  function ago(d) {
    var m = Math.floor(Math.max(0, Date.now() - d.getTime()) / 60000);
    if (m < 1) return 'şimdi';
    if (m < 60) return m + ' dk önce';
    var h = Math.floor(m / 60);
    if (h < 24) return h + ' sa önce';
    return Math.floor(h / 24) + ' gün önce';
  }
  function dayLabel(d) {
    var n = new Date();
    var a = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
    var b = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    var diff = Math.round((a - b) / 86400000);
    if (diff === 0) return 'Bugün';
    if (diff === 1) return 'Dün';
    return d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'long' });
  }

  function api(url, body) {
    var opt = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (body) {
      opt.method = 'POST';
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    return fetch(url, opt).then(function (r) {
      var ct = r.headers.get('content-type') || '';
      if (!r.ok || ct.indexOf('json') < 0) throw new Error('http ' + r.status);
      return r.json();
    });
  }

  function updateBell() {
    badge.textContent = unread > 99 ? '99+' : String(unread);
    bell.classList.toggle('has-unread', unread > 0);
    bell.setAttribute('aria-label', unread > 0 ? 'Bildirimler, ' + unread + ' okunmamış' : 'Bildirimler');
  }
  function ring() {
    bell.classList.remove('is-ring');
    void bell.offsetWidth;
    bell.classList.add('is-ring');
  }

  /* ---- Ust orta serit (toast) ---- */
  function hideStrip(instant) {
    clearTimeout(stripTimer);
    if (!strip) return;
    var s = strip;
    strip = null;
    if (instant) { if (s.parentNode) s.parentNode.removeChild(s); return; }
    s.classList.add('is-out');
    setTimeout(function () { if (s.parentNode) s.parentNode.removeChild(s); }, 320);
  }
  function armStrip() {
    stripStart = Date.now();
    stripTimer = setTimeout(function () { hideStrip(false); }, stripLeft);
  }
  function showStrip(kind, title, body) {
    hideStrip(true);
    var s = el('button', 'lal-nstrip');
    s.type = 'button';
    s.setAttribute('role', 'status');
    s.appendChild(icon(kind));
    s.appendChild(el('span', 'lal-nstrip-title', title));
    if (body) s.appendChild(el('span', 'lal-nstrip-body', body));
    s.addEventListener('click', function () { openDrawer(); });
    s.addEventListener('mouseenter', function () {
      clearTimeout(stripTimer);
      stripLeft -= Date.now() - stripStart;
      s.classList.add('is-paused');
    });
    s.addEventListener('mouseleave', function () {
      s.classList.remove('is-paused');
      armStrip();
    });
    document.body.appendChild(s);
    strip = s;
    stripLeft = STRIP_MS;
    armStrip();
  }
  function stripFor(newItems) {
    var n = newItems.length, f = newItems[0];
    if (n === 1) { showStrip(f.kind, f.title, f.body); return; }
    var names = newItems.slice(0, 2).map(function (x) { return x.title; }).join(' · ');
    showStrip(f.kind, n + ' yeni bildirim', names);
  }

  /* ---- Panel ---- */
  function detailNode(n) {
    var box = el('div', 'lal-ndetail');
    var d = n.detail || {};
    var shown = 0;
    (d.orders || []).forEach(function (o) {
      var r = el('div', 'lal-nrow');
      r.appendChild(el('span', 'lal-nmp is-' + (o.marketplace || ''), MP[o.marketplace] || o.marketplace || ''));
      r.appendChild(el('span', 'lal-nrow-main', o.order_number || '-'));
      if (typeof o.net_amount === 'number') r.appendChild(el('span', 'lal-nrow-end', money.format(o.net_amount)));
      box.appendChild(r);
      shown++;
    });
    (d.reviews || []).forEach(function (v) {
      var r = el('div', 'lal-nrow lal-nrow-review');
      r.appendChild(el('span', 'lal-nmp is-' + (v.marketplace || ''), MP[v.marketplace] || v.marketplace || ''));
      r.appendChild(el('span', 'lal-nrow-main', '★'.repeat(Math.max(0, Math.min(5, v.star || 0))) + (v.sku ? ' · ' + v.sku : '')));
      if (v.content) r.appendChild(el('div', 'lal-nrow-text', v.content));
      box.appendChild(r);
      shown++;
    });
    if (d.total && d.total > shown) box.appendChild(el('div', 'lal-nrow-more', '+' + (d.total - shown) + ' daha'));
    box.addEventListener('click', function (e) { e.stopPropagation(); });
    return box;
  }

  function itemNode(n, d) {
    var open = expandedId === n.id;
    var li = el('div', 'lal-nitem' + (n.read ? '' : ' is-unread') + (open ? ' is-expanded' : ''));
    li.setAttribute('role', 'button');
    li.setAttribute('aria-expanded', open ? 'true' : 'false');
    li.tabIndex = 0;
    li.appendChild(icon(n.kind));
    var tx = el('div', 'lal-ntext');
    tx.appendChild(el('div', 'lal-ntitle', n.title));
    if (n.body) tx.appendChild(el('div', 'lal-nbody', n.body));
    tx.appendChild(el('div', 'lal-ntime', d ? ago(d) : ''));
    if (open) tx.appendChild(detailNode(n));
    li.appendChild(tx);
    var toggle = function () {
      expandedId = open ? null : n.id;
      if (!n.read) markRead([n.id]); else render();
    };
    li.addEventListener('click', toggle);
    li.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
    return li;
  }

  function render() {
    if (!list) return;
    var top = list.scrollTop;
    list.textContent = '';
    chips.forEach(function (c) { c.classList.toggle('is-active', c.dataset.f === filter); });
    markAll.disabled = unread === 0;
    var shown = items.filter(function (n) { return filter === 'all' || n.kind === filter; });
    if (!shown.length) {
      var em = el('div', 'lal-nempty');
      em.appendChild(el('div', 'lal-nempty-title', 'Henüz bildirim yok'));
      em.appendChild(el('div', 'lal-nempty-sub', 'Yeni sipariş, yorum ve kesinleşen net kâr burada görünecek.'));
      list.appendChild(em);
      return;
    }
    var lastDay = null;
    shown.forEach(function (n) {
      var d = parseTs(n.created_at);
      var label = d ? dayLabel(d) : '';
      if (label !== lastDay) { lastDay = label; list.appendChild(el('div', 'lal-nday', label)); }
      list.appendChild(itemNode(n, d));
    });
    list.scrollTop = top;
  }

  function markRead(ids) {
    var changed = 0;
    items.forEach(function (n) {
      if (ids.indexOf(n.id) > -1 && !n.read) { n.read = true; changed++; }
    });
    unread = Math.max(0, unread - changed);
    updateBell();
    render();
    api(API + '/read', { ids: ids }).catch(function () {});
  }
  function markAllRead() {
    items.forEach(function (n) { n.read = true; });
    unread = 0;
    updateBell();
    render();
    api(API + '/read', { all: true }).catch(function () {});
  }

  function loadAll() {
    api(API + '?limit=100').then(function (d) {
      items = d.items || [];
      unread = d.unread || 0;
      if (items.length && (lastId === null || items[0].id > lastId)) { lastId = items[0].id; setLast(lastId); }
      updateBell();
      render();
    }).catch(function () {});
  }

  function openDrawer() {
    hideStrip(false);
    backdrop.classList.add('is-open');
    drawer.classList.add('is-open');
    drawer.setAttribute('aria-hidden', 'false');
    bell.setAttribute('aria-expanded', 'true');
    loadAll();
    drawer.querySelector('.lal-ndrawer-close').focus();
  }
  function closeDrawer() {
    backdrop.classList.remove('is-open');
    drawer.classList.remove('is-open');
    drawer.setAttribute('aria-hidden', 'true');
    bell.setAttribute('aria-expanded', 'false');
  }

  /* ---- Sorgulama ---- */
  function poll() {
    if (document.hidden) return;
    var init = lastId === null;
    var url = init ? API + '?limit=1' : API + '?after_id=' + lastId + '&limit=30';
    api(url).then(function (d) {
      unread = d.unread || 0;
      var fresh = d.items || [];
      if (init) {
        lastId = fresh.length ? fresh[0].id : 0;
        setLast(lastId);
        updateBell();
        return;
      }
      updateBell();
      if (!fresh.length) return;
      lastId = fresh[0].id;
      setLast(lastId);
      var ids = fresh.map(function (n) { return n.id; });
      items = fresh.concat(items.filter(function (n) { return ids.indexOf(n.id) < 0; }));
      ring();
      stripFor(fresh);
      if (drawer.classList.contains('is-open')) render();
    }).catch(function () {});
  }

  function build() {
    var controls = document.querySelector('.lal-topbar-controls');
    if (!controls) return false;

    bell = el('button', 'lal-bell');
    bell.type = 'button';
    bell.setAttribute('aria-label', 'Bildirimler');
    bell.setAttribute('aria-haspopup', 'dialog');
    bell.setAttribute('aria-expanded', 'false');
    bell.innerHTML = SVG + BELL + '</svg>';
    badge = el('span', 'lal-bell-badge');
    badge.setAttribute('aria-hidden', 'true');
    bell.appendChild(badge);
    bell.addEventListener('click', function () {
      if (drawer.classList.contains('is-open')) closeDrawer(); else openDrawer();
    });
    controls.insertBefore(bell, controls.firstChild);

    backdrop = el('div', 'lal-ndrawer-backdrop');
    backdrop.addEventListener('click', closeDrawer);
    drawer = el('aside', 'lal-ndrawer');
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-label', 'Bildirimler');
    drawer.setAttribute('aria-hidden', 'true');

    var head = el('div', 'lal-ndrawer-head');
    head.appendChild(el('div', 'lal-ndrawer-title', 'Bildirimler'));
    markAll = el('button', 'lal-ndrawer-link', 'Tümünü okundu yap');
    markAll.type = 'button';
    markAll.addEventListener('click', markAllRead);
    var close = el('button', 'lal-ndrawer-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Kapat');
    close.addEventListener('click', closeDrawer);
    head.appendChild(markAll);
    head.appendChild(close);

    var bar = el('div', 'lal-nchips');
    FILTERS.forEach(function (f) {
      var c = el('button', 'lal-nchip', f[1]);
      c.type = 'button';
      c.dataset.f = f[0];
      c.addEventListener('click', function () { filter = f[0]; render(); });
      chips.push(c);
      bar.appendChild(c);
    });

    list = el('div', 'lal-nlist');
    drawer.appendChild(head);
    drawer.appendChild(bar);
    drawer.appendChild(list);
    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && drawer.classList.contains('is-open')) { closeDrawer(); bell.focus(); }
    });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
    return true;
  }

  function init() {
    lastId = getLast();
    if (!build()) return;
    updateBell();
    poll();
    setInterval(poll, POLL_MS);
  }

  window.lalNotifications = {
    open: function () { if (drawer) openDrawer(); },
    close: function () { if (drawer) closeDrawer(); },
    refresh: poll,
    preview: function () { showStrip('order', 'Yeni sipariş', 'Trendyol · 10123456789 · ' + money.format(1250)); }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
