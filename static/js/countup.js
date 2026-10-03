(function () {
  'use strict';
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var IDS = ['stat-net-profit', 'stat-gross-revenue', 'stat-revenue', 'stat-gross-profit',
    'today-order-count', 'stat-orders', 'stat-avg-margin', 'stat-commission', 'stat-service-fee',
    'stat-stoppage', 'stat-platform-fee', 'stat-cash-advance', 'stat-return', 'stat-payment-order'];

  function parse(t) {
    var m = String(t).trim().match(/^(-?)(₺?)\s*(-?)([\d.,]+)\s*(%?)$/);
    if (!m) return null;
    var pct = m[5] === '%', raw = m[4], num, dec = 0;
    if (pct) {
      num = parseFloat(raw);
      var i = raw.indexOf('.');
      dec = i < 0 ? 0 : raw.length - i - 1;
    } else {
      var c = raw.indexOf(',');
      dec = c < 0 ? 0 : raw.length - c - 1;
      num = parseFloat(raw.replace(/\./g, '').replace(',', '.'));
    }
    if (isNaN(num)) return null;
    var neg = m[1] === '-' || m[3] === '-';
    return { n: neg ? -num : num, cur: m[2] === '₺', pct: pct, dec: dec, post: m[3] === '-' };
  }

  function fmt(o, v) {
    var a = Math.abs(v), body;
    if (o.pct) body = a.toFixed(o.dec) + '%';
    else body = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: o.dec, maximumFractionDigits: o.dec }).format(a);
    var neg = v < 0;
    if (o.cur) return (neg && !o.post ? '-' : '') + '₺' + (neg && o.post ? '-' : '') + body;
    return (neg ? '-' : '') + body;
  }

  function attach(el) {
    var prev = 0, last = el.textContent, raf = null;
    var obs = new MutationObserver(function () {
      var txt = el.textContent;
      if (txt === last) return;
      last = txt;
      var o = parse(txt);
      if (!o) { prev = 0; return; }
      var from = prev, t0 = performance.now(), D = 800;
      prev = o.n;
      if (raf) cancelAnimationFrame(raf);
      (function step(now) {
        var p = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - p, 4);
        el.textContent = p === 1 ? txt : fmt(o, from + (o.n - from) * e);
        obs.takeRecords();
        if (p < 1) raf = requestAnimationFrame(step);
      })(t0);
    });
    obs.observe(el, { childList: true, characterData: true, subtree: true });
  }

  IDS.forEach(function (id) {
    var el = document.getElementById(id);
    if (el) attach(el);
  });
})();
