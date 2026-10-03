(function () {
  'use strict';

  var sw = document.getElementById('mp-switch');
  if (sw) {
    var thumb = document.createElement('span');
    thumb.className = 'mp-thumb';
    thumb.setAttribute('aria-hidden', 'true');
    sw.insertBefore(thumb, sw.firstChild);

    var moveThumb = function (instant) {
      var a = sw.querySelector('.mp-switch-btn.active');
      if (!a) { thumb.classList.remove('is-ready'); return; }
      if (instant) thumb.style.transition = 'none';
      thumb.style.width = a.offsetWidth + 'px';
      thumb.style.transform = 'translateX(' + a.offsetLeft + 'px)';
      thumb.classList.add('is-ready');
      if (instant) { void thumb.offsetWidth; thumb.style.transition = ''; }
    };
    var mpObs = new MutationObserver(function () { moveThumb(false); });
    sw.querySelectorAll('.mp-switch-btn').forEach(function (b) {
      mpObs.observe(b, { attributes: true, attributeFilter: ['class'] });
    });
    window.addEventListener('resize', function () { moveThumb(true); });
    window.addEventListener('load', function () { moveThumb(true); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { moveThumb(true); });
    requestAnimationFrame(function () { moveThumb(true); });
  }

  var sel = document.getElementById('range-select');
  if (!sel) return;

  var wrap = document.createElement('div');
  wrap.className = 'lal-dd';
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'lal-dd-btn';
  btn.setAttribute('aria-haspopup', 'listbox');
  btn.setAttribute('aria-expanded', 'false');
  var lab = document.createElement('span');
  btn.appendChild(lab);
  btn.insertAdjacentHTML('beforeend', '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>');

  var menu = document.createElement('ul');
  menu.className = 'lal-dd-menu';
  menu.setAttribute('role', 'listbox');
  var opts = [];
  Array.prototype.forEach.call(sel.options, function (o) {
    var li = document.createElement('li');
    li.className = 'lal-dd-opt';
    li.setAttribute('role', 'option');
    li.dataset.value = o.value;
    li.textContent = o.textContent;
    menu.appendChild(li);
    opts.push(li);
  });

  sel.parentNode.insertBefore(wrap, sel);
  wrap.appendChild(sel);
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  sel.classList.add('lal-select-native');
  sel.tabIndex = -1;
  sel.setAttribute('aria-hidden', 'true');

  var fi = -1;

  function sync() {
    var cur = sel.options[sel.selectedIndex];
    lab.textContent = cur ? cur.textContent : '';
    opts.forEach(function (li) { li.setAttribute('aria-selected', String(li.dataset.value === sel.value)); });
  }

  function focusOpt(i) {
    fi = Math.max(0, Math.min(opts.length - 1, i));
    opts.forEach(function (li, k) { li.classList.toggle('is-focus', k === fi); });
  }

  function open() {
    wrap.classList.add('is-open');
    btn.setAttribute('aria-expanded', 'true');
    focusOpt(Math.max(0, sel.selectedIndex));
  }

  function close(refocus) {
    wrap.classList.remove('is-open');
    btn.setAttribute('aria-expanded', 'false');
    opts.forEach(function (li) { li.classList.remove('is-focus'); });
    if (refocus) btn.focus();
  }

  function choose(li) {
    if (sel.value !== li.dataset.value) {
      sel.value = li.dataset.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    sync();
    close(true);
  }

  btn.addEventListener('click', function () {
    if (wrap.classList.contains('is-open')) close(false); else open();
  });
  opts.forEach(function (li) { li.addEventListener('click', function () { choose(li); }); });

  btn.addEventListener('keydown', function (e) {
    var isOpen = wrap.classList.contains('is-open');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!isOpen) open();
      else focusOpt(fi + (e.key === 'ArrowDown' ? 1 : -1));
    } else if ((e.key === 'Enter' || e.key === ' ') && isOpen) {
      e.preventDefault();
      if (opts[fi]) choose(opts[fi]);
    } else if (e.key === 'Escape' && isOpen) {
      e.preventDefault();
      close(true);
    }
  });

  document.addEventListener('click', function (e) {
    if (!wrap.contains(e.target)) close(false);
  });

  sel.addEventListener('change', sync);
  sync();
  window.addEventListener('load', sync);
  setTimeout(sync, 600);
})();
