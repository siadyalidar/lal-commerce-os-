// =====================================================================
// ARAYÜZ KATMANI — kontrollü reveal, veri güncellemesi ve grafik hareketi
// (Bu blok yalnızca görseldir; hiçbir veri/API mantığına dokunmaz.)
// =====================================================================
(function () {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---------- 1) Sticky header ----------
  const header = document.querySelector('header');
  if (header) {
    const onScroll = () => header.classList.toggle('is-scrolled', window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  // ---------- 2) Scroll-reveal: panel/kart/hero-stat elemanları görünürken yumuşak beliriyor ----------
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

  // ---------- 3) Değer güncellemelerinde ince "blur-in" flaşı ----------
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

  // ---------- 4) Chart.js varsayılanları ----------
  if (window.Chart) {
    Chart.defaults.font.family = "'Instrument Sans', 'Inter', -apple-system, sans-serif";
    Chart.defaults.color = '#a0a5a8';
    Chart.defaults.borderColor = 'rgba(241,242,239,0.12)';
    Chart.defaults.animation = { duration: reduceMotion ? 0 : 520, easing: 'easeOutQuart' };
    if (Chart.defaults.transitions && Chart.defaults.transitions.active) {
      Chart.defaults.transitions.active.animation.duration = reduceMotion ? 0 : 250;
    }
    if (Chart.defaults.elements && Chart.defaults.elements.point) {
      Chart.defaults.elements.point.hoverBorderWidth = 2.5;
    }
  }
})();
