/* Shared DOM helpers, safe storage, reduced-motion flag and text clamping. */

export const doc = document;
export const root = doc.documentElement;
export const $ = (s, r = doc) => r.querySelector(s);
export const SVGNS = 'http://www.w3.org/2000/svg';

const motionQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
/* live binding: importers always read the current value */
export let RM = !!(motionQuery && motionQuery.matches);
motionQuery?.addEventListener?.('change', (e) => { RM = e.matches; });
/* own-property lookup: data ids like "constructor" or "__proto__" must not hit Object.prototype */
export const own = (obj, k) => (Object.prototype.hasOwnProperty.call(obj, k) ? obj[k] : undefined);

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
  remove(k) { try { localStorage.removeItem(k); } catch (e) { /* storage blocked */ } },
};

/* ---------- tiny DOM helpers ---------- */
export function el(tag, cls, text) {
  const n = doc.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
export function svgEl(paths, cls) {
  const s = doc.createElementNS(SVGNS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('class', cls || '');
  paths.forEach(([tag, attrs]) => {
    const p = doc.createElementNS(SVGNS, tag);
    Object.keys(attrs).forEach((k) => p.setAttribute(k, attrs[k]));
    s.appendChild(p);
  });
  return s;
}
export const P = (d) => ['path', { d }];

/* ---------- spring easing via CSS linear() ---------- */
function springEasing(stiffness, damping) {
  let x = 0, v = 0;
  const dt = 1 / 60, pts = [];
  for (let i = 0; i < 180; i++) {
    const a = -stiffness * (x - 1) - damping * v;
    v += a * dt; x += v * dt;
    pts.push(+x.toFixed(4));
    if (i > 20 && Math.abs(x - 1) < 0.002 && Math.abs(v) < 0.02) break;
  }
  return { css: `linear(0, ${pts.join(', ')})`, ms: Math.round(pts.length * 1000 / 60) };
}
export function applySpring() {
  try {
    if (CSS.supports('transition-timing-function', 'linear(0, 0.5, 1)')) {
      const sp = springEasing(190, 17);
      root.style.setProperty('--spring', sp.css);
      root.style.setProperty('--spring-dur', sp.ms + 'ms');
    }
  } catch (e) { /* older browser: CSS fallback curve */ }
}

/* Trim text to N lines at a word boundary, ending with an ellipsis. */
function clampWords(node, lines) {
  const full = node.dataset.full || node.textContent;
  node.dataset.full = full;
  node.textContent = full;
  const lh = parseFloat(getComputedStyle(node).lineHeight);
  if (!lh) return;
  const max = lh * lines + 2;
  if (node.scrollHeight <= max) return;
  const words = full.split(/\s+/);
  let lo = 1, hi = words.length - 1, best = 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    node.textContent = words.slice(0, mid).join(' ') + '…';
    if (node.scrollHeight <= max) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  node.textContent = words.slice(0, best).join(' ').replace(/[\s,;:.\-–—]+$/, '') + '…';
}
export function clampAll() {
  doc.querySelectorAll('[data-clamp]').forEach((n) => clampWords(n, +n.dataset.clamp || 3));
}
let clampTimer = 0;
export function initClamp() {
  addEventListener('resize', () => { clearTimeout(clampTimer); clampTimer = setTimeout(clampAll, 150); });
}
