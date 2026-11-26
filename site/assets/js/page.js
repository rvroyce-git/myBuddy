/* Static page behaviour: navbar border, "How it works" tooltips, scroll reveal. */

import { $, doc, RM } from './util.js';

export function initPage() {
  /* ---------- navbar border on scroll ---------- */
  const nav = $('#nav');
  addEventListener('scroll', () => nav.classList.toggle('scrolled', scrollY > 8), { passive: true });

  initTips();

  /* ---------- "What stays, what leaves": reveal on scroll ---------- */
  (() => {
    const flow = $('#flow');
    if (!flow) return;
    if (RM || !('IntersectionObserver' in window)) { flow.classList.add('in'); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((en) => en.isIntersecting)) { flow.classList.add('in'); io.disconnect(); }
    }, { threshold: 0.2 });
    io.observe(flow);
  })();
}

/* ---------- "How it works" tooltips ----------
   Hover (mouse) or keyboard focus shows a tip; a click/tap pins it open or closes it.
   Esc, tapping elsewhere or leaving focus closes it. Positioned so it never leaves the viewport. */
function initTips() {
  const steps = Array.from(doc.querySelectorAll('#steps .step'));
  const tips = steps.map((b) => doc.getElementById(b.getAttribute('aria-describedby')));
  let open = -1, pinned = false;
  const EDGE = 10, GAP = 12;

  function place(i) {
    const b = steps[i], tip = tips[i], li = b.parentNode;
    tip.style.left = '0px'; tip.style.top = '0px';
    const br = b.getBoundingClientRect(), lr = li.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const vw = doc.documentElement.clientWidth;
    const cx = br.left + br.width / 2;
    const left = Math.max(EDGE, Math.min(cx - tw / 2, vw - EDGE - tw));
    const below = br.top - th - GAP < EDGE + 62; // keep clear of the sticky nav
    tip.classList.toggle('below', below);
    tip.style.left = (left - lr.left) + 'px';
    tip.style.top = (below ? br.bottom - lr.top + GAP : br.top - lr.top - th - GAP) + 'px';
    tip.style.setProperty('--ax', Math.max(16, Math.min(tw - 16, cx - left)) + 'px');
  }
  function show(i, pin) {
    if (open !== -1 && open !== i) hide();
    open = i; pinned = !!pin;
    place(i);
    tips[i].classList.add('open');
    steps[i].setAttribute('data-open', '');
  }
  function hide() {
    if (open === -1) return;
    tips[open].classList.remove('open');
    steps[open].removeAttribute('data-open');
    open = -1; pinned = false;
  }
  steps.forEach((b, i) => {
    const li = b.parentNode;
    li.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') show(i, pinned && open === i); });
    li.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse' && open === i && !pinned) hide(); });
    b.addEventListener('focus', () => { if (b.matches(':focus-visible')) show(i, false); });
    b.addEventListener('blur', () => { if (open === i) hide(); });
    b.addEventListener('click', () => { if (open === i && pinned) hide(); else show(i, true); });
  });
  doc.addEventListener('pointerdown', (e) => {
    if (open !== -1 && !steps[open].parentNode.contains(e.target)) hide();
  }, true);
  doc.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open !== -1) { e.preventDefault(); hide(); }
  });
  addEventListener('resize', () => { if (open !== -1) place(open); });
}
