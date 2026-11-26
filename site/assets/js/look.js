/* Topic look: icons, short names, card gradient angle and the floating orb. */

import { el, own, svgEl, P } from './util.js';

const ICONS = {
  ai_news: [P('M11 3l1.8 4.7 4.7 1.8-4.7 1.8L11 16l-1.8-4.7L4.5 9.5l4.7-1.8z'), P('M18.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z')],
  security: [P('M12 3l7 3v5.2c0 4.4-3 8.2-7 9.8-4-1.6-7-5.4-7-9.8V6z'), P('M9 12l2.2 2.2L15.5 10')],
  github_trending: [P('M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.7 1-5.8L3.5 9.7l5.9-.9z')],
  deep_dive: [['circle', { cx: '10.5', cy: '10.5', r: '6.5' }], P('M15.5 15.5L21 21'), P('M8 10.5a2.5 2.5 0 0 1 2.5-2.5')],
  research_papers: [P('M6 3h8l4 4v14H6z'), P('M14 3v4h4'), P('M9 12h6M9 16h6')],
  startups: [P('M12 2.5c3 2 4.5 5.5 4.5 9.5l-2 3h-5l-2-3c0-4 1.5-7.5 4.5-9.5z'), ['circle', { cx: '12', cy: '9.5', r: '1.7' }], P('M9.5 15l-3 2.5 1-4.5M14.5 15l3 2.5-1-4.5M10.5 18.5c.4 1.3.9 2.3 1.5 3 .6-.7 1.1-1.7 1.5-3')],
  ai_models: [P('M12 2.5l8 4.5v9l-8 4.5-8-4.5v-9z'), P('M4 7l8 4.5L20 7M12 11.5V21')],
};
export const SHORT = {
  ai_news: 'AI news', security: 'Security', github_trending: 'GitHub', deep_dive: 'Deep dive',
  research_papers: 'Papers', startups: 'Launches', ai_models: 'Models',
};
export function iconFor(t, cls) {
  const icon = own(ICONS, t.id);
  if (icon) return svgEl(icon, 'ico ' + (cls || ''));
  const s = el('span', 'emoji', t.emoji || '•');
  s.setAttribute('aria-hidden', 'true');
  return s;
}
/* Every topic shares the palette's card gradient (--card-1/--card-2); only the angle varies. */
export function paint(node, t) {
  node.style.setProperty('--ang', t.ang + 'deg');
}

export function decorateIcon(svg) {
  if (svg.tagName.toLowerCase() !== 'svg') return svg;
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.9');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  return svg;
}
export function orb(t) {
  const wrap = el('div', 'art');
  const ow = el('div', 'orb-wrap');
  const o = el('div', 'orb');
  o.append(decorateIcon(iconFor(t)), el('i'), el('i'), el('i'));
  ow.appendChild(o);
  wrap.appendChild(ow);
  return wrap;
}
