(function () {
  'use strict';
  var KEY = 'lal-sidebar-collapsed';
  var sb = document.getElementById('lal-sidebar');
  if (!sb) return;
  var btn = document.getElementById('sidebar-collapse-btn');
  var ind = sb.querySelector('.lal-nav-indicator');
  var items = Array.prototype.slice.call(sb.querySelectorAll('a.lal-nav-item'));
  var active = sb.querySelector('a.lal-nav-item.is-active');
  var root = document.documentElement;
  var timer = null;

  var tip = document.createElement('div');
  tip.className = 'lal-nav-tip';
  document.body.appendChild(tip);

  if (getComputedStyle(sb).position === 'static') sb.style.position = 'relative';
  if (ind) sb.classList.add('has-indicator');
  if (active) active.setAttribute('aria-current', 'page');

  function place(el, hover, instant) {
    if (!ind || !el) return;
    if (instant) ind.style.transition = 'none';
    ind.style.width = el.offsetWidth + 'px';
    ind.style.height = el.offsetHeight + 'px';
    ind.style.transform = 'translate(' + el.offsetLeft + 'px,' + el.offsetTop + 'px)';
    ind.classList.toggle('is-hover', !!hover);
    ind.classList.add('is-ready');
    if (instant) { void ind.offsetWidth; ind.style.transition = ''; }
  }

  function rest(instant) {
    if (active) place(active, false, instant);
    else if (ind) ind.classList.remove('is-ready');
  }

  function showTip(el) {
    if (!sb.classList.contains('is-collapsed')) return;
    var label = el.querySelector('.lal-nav-label');
    if (!label) return;
    tip.textContent = label.textContent.trim();
    var r = el.getBoundingClientRect();
    tip.style.left = (r.right + 10) + 'px';
    tip.style.top = (r.top + r.height / 2 - tip.offsetHeight / 2) + 'px';
    tip.classList.add('is-on');
  }

  function hideTip() { tip.classList.remove('is-on'); }

  function setCollapsed(c, persist) {
    sb.classList.toggle('is-collapsed', c);
    var w = getComputedStyle(root).getPropertyValue('--lal-sidebar-w-collapsed').trim();
    if (c && w) root.style.setProperty('--lal-sidebar-w', w);
    else root.style.removeProperty('--lal-sidebar-w');
    if (btn) {
      btn.setAttribute('aria-expanded', String(!c));
      btn.setAttribute('aria-label', c ? 'Kenar çubuğunu genişlet' : 'Kenar çubuğunu daralt');
    }
    if (persist) { try { localStorage.setItem(KEY, c ? '1' : '0'); } catch (e) {} }
    hideTip();
    if (ind) ind.classList.remove('is-ready');
    clearTimeout(timer);
    timer = setTimeout(function () { rest(true); }, 430);
  }

  items.forEach(function (it) {
    it.addEventListener('mouseenter', function () { place(it, it !== active); showTip(it); });
    it.addEventListener('focus', function () { place(it, it !== active); showTip(it); });
    it.addEventListener('mouseleave', hideTip);
    it.addEventListener('blur', hideTip);
  });
  sb.addEventListener('mouseleave', function () { rest(false); hideTip(); });

  if (btn) {
    btn.addEventListener('click', function () {
      setCollapsed(!sb.classList.contains('is-collapsed'), true);
    });
    var c = sb.classList.contains('is-collapsed');
    btn.setAttribute('aria-expanded', String(!c));
    btn.setAttribute('aria-label', c ? 'Kenar çubuğunu genişlet' : 'Kenar çubuğunu daralt');
  }

  window.addEventListener('resize', function () { rest(true); });
  window.addEventListener('load', function () { rest(true); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { rest(true); });
  requestAnimationFrame(function () { rest(true); });
})();
