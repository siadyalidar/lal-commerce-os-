(function () {
  'use strict';
  if (!('IntersectionObserver' in window)) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var root = document.querySelector('.lal-page-content');
  if (!root) return;

  var SEL = [
    '.lal-command-greeting',
    '.lal-panel',
    '.panel',
    '.lal-status-card',
    '.lal-grid-4 > *',
    '.summary-grid > *'
  ].join(',');

  var els = Array.prototype.filter.call(root.querySelectorAll(SEL), function (el) {
    return !(el.parentElement && el.parentElement.closest(SEL));
  });
  if (!els.length) return;

  document.documentElement.classList.add('rv-on');
  els.forEach(function (el) { el.classList.add('rv'); });

  var io = new IntersectionObserver(function (entries) {
    var visible = entries.filter(function (e) { return e.isIntersecting; });
    visible.forEach(function (e, i) {
      var el = e.target;
      el.style.transitionDelay = Math.min(i * 70, 350) + 'ms';
      el.classList.add('rv-in');
      io.unobserve(el);
      setTimeout(function () {
        el.classList.remove('rv', 'rv-in');
        el.style.transitionDelay = '';
      }, 1500);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0 });

  els.forEach(function (el) { io.observe(el); });
})();
