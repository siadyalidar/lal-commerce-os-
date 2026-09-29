(function () {
  'use strict';
  var band = document.getElementById('alerts-band');
  if (!band || !('MutationObserver' in window)) return;
  var KEY = 'lal-alerts-open';
  var mo;
  function isOpen() { try { return sessionStorage.getItem(KEY) === '1'; } catch (e) { return false; } }
  function setOpen(v) { try { sessionStorage.setItem(KEY, v ? '1' : '0'); } catch (e) {} }
  function apply() {
    mo.disconnect();
    var old = band.querySelector('.alerts-toggle');
    if (old) old.remove();
    var items = band.querySelectorAll(':scope > .alert');
    var open = isOpen();
    band.classList.toggle('is-compact', !open);
    if (items.length > 1) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'alerts-toggle';
      b.textContent = open ? 'Daha az göster' : '+' + (items.length - 1) + ' uyarı daha';
      b.setAttribute('aria-expanded', open ? 'true' : 'false');
      b.addEventListener('click', function () { setOpen(!isOpen()); apply(); });
      band.appendChild(b);
    }
    mo.observe(band, { childList: true });
  }
  mo = new MutationObserver(apply);
  apply();
})();
