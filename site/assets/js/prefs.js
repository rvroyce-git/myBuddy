/* Visitor preferences: colour palette, light/dark theme (never saved) and seen stories. */

import { $, doc, root, RM, store } from './util.js';
import { refreshBubbles } from './bubbles.js';

function pickPalette(palettes, fallback) {
  let q;
  try { q = new URLSearchParams(location.search).get('palette') || ''; } catch (e) { q = ''; }
  q = q.toLowerCase().replace(/[^a-z-]/g, '').slice(0, 32);
  return palettes.includes(q) ? q : fallback;
}
export function initPalette(palettes, fallback) {
  root.setAttribute('data-palette', pickPalette(palettes, fallback));
}

/* ---------- theme ----------
   Always opens dark. The toggle switches for this visit only (never saved). */
export function initTheme() {
  const themeBtn = $('#themeBtn');
  const metaTheme = doc.querySelector('meta[name="theme-color"]');
  store.remove('mybuddy.theme'); // old saved choice from earlier versions
  root.setAttribute('data-theme', 'dark');
  const effectiveDark = () => root.getAttribute('data-theme') !== 'light';
  function syncTheme() {
    const dark = effectiveDark();
    root.setAttribute('data-mode', dark ? 'dark' : 'light');
    themeBtn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    const bg = getComputedStyle(root).getPropertyValue('--bg').trim();
    if (metaTheme && bg) metaTheme.setAttribute('content', bg);
  }
  syncTheme();
  themeBtn.addEventListener('click', () => {
    const next = effectiveDark() ? 'light' : 'dark';
    const apply = () => { root.setAttribute('data-theme', next); syncTheme(); };
    if (!RM && doc.startViewTransition) {
      const r = themeBtn.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      const vt = doc.startViewTransition(apply);
      vt.ready.then(() => {
        root.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
          { duration: 650, easing: 'cubic-bezier(.4,0,.2,1)', pseudoElement: '::view-transition-new(root)' });
      }).catch(() => {});
    } else {
      apply();
    }
  });
}

/* ---------- seen state ---------- */
/* Seen state resets every day: it is keyed to the day of generated_at (IST). */
const SEEN_KEY = 'mybuddy.seen.v2';
let seen = Object.create(null);
let seenDay = '';
export function loadSeen(day) {
  seenDay = day || 'unknown';
  let saved;
  try { saved = JSON.parse(store.get(SEEN_KEY) || 'null'); } catch (e) { saved = null; }
  seen = Object.create(null);
  const ids = saved && saved.day === seenDay ? saved.ids : null;
  if (ids && typeof ids === 'object' && !Array.isArray(ids)) {
    Object.keys(ids).forEach((k) => { if (typeof ids[k] === 'string') seen[k] = ids[k]; });
  }
}
export const isSeen = (t) => seen[t.id] === t.stamp;
export function markSeen(t) {
  if (isSeen(t)) return;
  seen[t.id] = t.stamp;
  store.set(SEEN_KEY, JSON.stringify({ day: seenDay, ids: seen }));
  refreshBubbles();
}
