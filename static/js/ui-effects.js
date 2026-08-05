// =====================================================================
// ARAYÜZ KATMANI — ambient su akışı, scroll reveal, tilt, value-pulse
// (Bu blok yalnızca görseldir; hiçbir veri/API mantığına dokunmaz.)
// =====================================================================
(function () {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- 1) Ambient "su akışı" arka planı ----------
  // Marka su filtrasyonu üzerine kurulu olduğu için, sayfanın en dip
  // katmanında çok düşük opasitede akan noktalar/çizgiler var —
  // sayfanın "canlı veri akıyor" hissi vermesi için.
  const canvas = document.getElementById('ambient-bg');
  if (canvas && !reduceMotion) {
    const ctx = canvas.getContext('2d');
    let w, h, dpr;
    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener('resize', resize);

    const COUNT = Math.min(46, Math.floor((window.innerWidth * window.innerHeight) / 34000));
    const particles = Array.from({ length: COUNT }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      r: 0.6 + Math.random() * 1.8,
      vy: 0.10 + Math.random() * 0.22,
      vx: (Math.random() - 0.5) * 0.12,
      phase: Math.random() * Math.PI * 2,
      amp: 8 + Math.random() * 22,
      alpha: 0.10 + Math.random() * 0.22,
    }));

    let t = 0;
    function tick() {
      t += 0.008;
      ctx.clearRect(0, 0, w, h);

      // birbirine yakın parçacıkları ince bir hatla bağla (nöral/veri ağı hissi)
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i], b = particles[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < 130 * 130) {
            const o = (1 - d2 / (130 * 130)) * 0.05;
            ctx.strokeStyle = `rgba(61,219,217,${o})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      for (const p of particles) {
        p.y -= p.vy;
        p.x += p.vx + Math.sin(t + p.phase) * 0.06;
        if (p.y < -10) { p.y = h + 10; p.x = Math.random() * w; }
        if (p.x < -10) p.x = w + 10;
        if (p.x > w + 10) p.x = -10;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(61,219,217,${p.alpha})`;
        ctx.fill();
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // ---------- 2) Sticky header: scroll ile daralma/cam efekti ----------
  const header = document.querySelector('header');
  if (header) {
    const onScroll = () => header.classList.toggle('is-scrolled', window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  // ---------- 3) Scroll-reveal: panel/kart/hero-stat elemanları görünürken yumuşak beliriyor ----------
  const revealTargets = document.querySelectorAll('.panel, .hero-stat, .card');
  if ('IntersectionObserver' in window && !reduceMotion) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry, idx) => {
        if (entry.isIntersecting) {
          const el = entry.target;
          const delay = Math.min(idx * 35, 220);
          setTimeout(() => el.classList.add('is-visible'), delay);
          io.unobserve(el);
        }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -6% 0px' });
    revealTargets.forEach(el => { el.classList.add('reveal'); io.observe(el); });
  }
  // Yeni DOM içeriği (tablolar, satırlar vs.) sonradan render edildiğinde
  // de reveal alsın diye periyodik olarak yeni panel/kart var mı bak.
  if ('IntersectionObserver' in window && !reduceMotion) {
    const lateIo = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) { entry.target.classList.add('is-visible'); lateIo.unobserve(entry.target); }
      });
    }, { threshold: 0.08 });
    const mo = new MutationObserver((mutations) => {
      mutations.forEach(m => {
        m.addedNodes.forEach(node => {
          if (node.nodeType !== 1) return;
          const scope = node.matches && node.matches('.panel, .card, .hero-stat') ? [node] : [];
          const nested = node.querySelectorAll ? node.querySelectorAll('.panel, .card, .hero-stat') : [];
          [...scope, ...nested].forEach(el => {
            if (!el.classList.contains('reveal')) {
              el.classList.add('reveal');
              lateIo.observe(el);
            }
          });
        });
      });
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  // ---------- 4) Kart/hero-stat üzerinde ince 3D tilt + ışık takibi ----------
  if (!reduceMotion && window.matchMedia('(pointer: fine)').matches) {
    document.addEventListener('pointermove', (e) => {
      const el = e.target.closest ? e.target.closest('.hero-stat, .card') : null;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const px = (e.clientX - rect.left) / rect.width;
      const py = (e.clientY - rect.top) / rect.height;
      const rx = (0.5 - py) * 5;
      const ry = (px - 0.5) * 6;
      el.style.transform = `perspective(700px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-2px)`;
    });
    document.addEventListener('pointerout', (e) => {
      const el = e.target.closest ? e.target.closest('.hero-stat, .card') : null;
      if (el) el.style.transform = '';
    });
  }

  // ---------- 5) Buton üzerinde imleci takip eden parlama ----------
  document.addEventListener('pointermove', (e) => {
    const btn = e.target.closest ? e.target.closest('.lal-btn-primary') : null;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    btn.style.setProperty('--mx', `${((e.clientX - rect.left) / rect.width) * 100}%`);
    btn.style.setProperty('--my', `${((e.clientY - rect.top) / rect.height) * 100}%`);
  });

  // ---------- 6) Değer güncellemelerinde ince "blur-in" flaşı ----------
  // hero rakamları / kart değerleri her fetch sonrası textContent ile
  // güncelleniyor; bunu izleyip her değişimde kısa bir canlanma efekti veriyoruz.
  if (!reduceMotion) {
    const valueSelectors = '.hero-stat-value, .card .value, .hf-value, #today-order-count';
    const watchTargets = new Set();
    document.querySelectorAll(valueSelectors).forEach(el => watchTargets.add(el));
    const valueMo = new MutationObserver((mutations) => {
      const touched = new Set();
      mutations.forEach(m => {
        const el = m.target.nodeType === 3 ? m.target.parentElement : m.target;
        if (el && el.matches && el.matches(valueSelectors)) touched.add(el);
      });
      touched.forEach(el => {
        el.classList.remove('value-pulse');
        void el.offsetWidth; // reflow — animasyonu yeniden tetikler
        el.classList.add('value-pulse');
      });
    });
    watchTargets.forEach(el => valueMo.observe(el, { characterData: true, childList: true, subtree: true }));
    // Sonradan eklenen değer elemanlarını da izlemeye al
    new MutationObserver((mutations) => {
      mutations.forEach(m => m.addedNodes.forEach(node => {
        if (node.nodeType !== 1) return;
        const found = node.matches && node.matches(valueSelectors) ? [node] : (node.querySelectorAll ? [...node.querySelectorAll(valueSelectors)] : []);
        found.forEach(el => { if (!watchTargets.has(el)) { watchTargets.add(el); valueMo.observe(el, { characterData: true, childList: true, subtree: true }); } });
      }));
    }).observe(document.body, { childList: true, subtree: true });
  }

  // ---------- 7) Chart.js: daha yumuşak, "premium" hissiyatlı varsayılanlar ----------
  if (window.Chart) {
    Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
    Chart.defaults.animation = { duration: reduceMotion ? 0 : 700, easing: 'easeOutQuint' };
    if (Chart.defaults.transitions && Chart.defaults.transitions.active) {
      Chart.defaults.transitions.active.animation.duration = reduceMotion ? 0 : 250;
    }
    if (Chart.defaults.elements && Chart.defaults.elements.point) {
      Chart.defaults.elements.point.hoverBorderWidth = 2.5;
    }
  }
})();
