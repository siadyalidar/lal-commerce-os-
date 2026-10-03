(function () {
  'use strict';
  var host = null;

  function ensureHost() {
    if (host) return host;
    host = document.createElement('div');
    host.className = 'lal-toasts';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
    return host;
  }

  function dismiss(t) {
    if (!t || t.classList.contains('is-out')) return;
    t.classList.add('is-out');
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 320);
  }

  function toast(msg, type, ms) {
    var text = String(msg == null ? '' : msg);
    var h = ensureHost();
    var dup = Array.prototype.some.call(h.children, function (c) {
      return c.textContent === text && !c.classList.contains('is-out');
    });
    if (dup) return;
    var t = document.createElement('div');
    t.className = 'lal-toast is-' + (type || 'info');
    t.textContent = text;
    t.addEventListener('click', function () { dismiss(t); });
    h.appendChild(t);
    while (h.children.length > 4) h.removeChild(h.firstChild);
    setTimeout(function () { dismiss(t); }, ms || 4500);
  }

  window.lalToast = toast;
  window.lalToast.success = function (m) { toast(m, 'success', 4000); };
  window.lalToast.error = function (m) { toast(m, 'error', 8000); };
  window.lalToast.info = function (m) { toast(m, 'info', 4500); };

  if (typeof window.showError === 'function') {
    window.showError = function (msg) { toast(msg, 'error', 8000); };
  }
})();
