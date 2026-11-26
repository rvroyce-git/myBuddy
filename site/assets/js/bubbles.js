/* Story bubbles (top row) and the headline list. */

import { $, doc, el, P, SVGNS, svgEl } from './util.js';
import { decorateIcon, iconFor, paint } from './look.js';
import { topics, withAction } from './data.js';
import { isSeen } from './prefs.js';
import { Story } from './story.js';

const bubblesEl = $('#bubbles');
const bearBubble = $('#bearBubble');
let bubbleBtns = [];

export function refreshBubbles() {
  bubbleBtns.forEach((b, i) => {
    const s = isSeen(topics[i]);
    b.classList.toggle('seen', s);
    b.setAttribute('aria-label', `${topics[i].title} story${s ? ', seen' : ''}`);
  });
  bearBubble.classList.toggle('seen', topics.length > 0 && topics.every(isSeen));
}

export function buildBubbles() {
  bubblesEl.querySelectorAll('li.topic').forEach((n) => n.remove());
  bubbleBtns = topics.map((t, i) => {
    const li = el('li', 'topic');
    li.style.setProperty('--i', i + 1);
    const b = el('button', 'bubble');
    b.type = 'button';
    const ring = el('span', 'ring'); ring.setAttribute('aria-hidden', 'true');
    const face = el('span', 'face'); paint(face, t);
    face.appendChild(decorateIcon(iconFor(t)));
    const tick = el('span', 'tick'); tick.setAttribute('aria-hidden', 'true');
    tick.appendChild(svgEl([P('M6 12.5l4 4 8-9')]));
    b.append(ring, face, tick, el('span', 'label', t.short));
    b.addEventListener('click', () => Story.open(i, b));
    li.appendChild(b);
    bubblesEl.appendChild(li);
    return b;
  });
  refreshBubbles();
}
export function initBubbles() {
  bearBubble.addEventListener('click', () => {
    const first = topics.findIndex((t) => !isSeen(t));
    Story.open(first < 0 ? 0 : first, bearBubble);
  });
}

export function buildList() {
  const list = $('#hlList');
  list.textContent = '';
  topics.forEach((t, i) => {
    const li = el('li');
    li.style.setProperty('--i', i);
    const b = el('button', 'hl-item');
    b.type = 'button';
    paint(b, t);
    b.setAttribute('aria-label', withAction(`${t.title}: ${t.headline}`, 'Open story'));
    const ic = el('span', 'hl-ico'); ic.appendChild(decorateIcon(iconFor(t)));
    const body = el('span', 'hl-body');
    const meta = el('span', 'hl-meta');
    meta.append(el('span', 'hl-topic', t.title));
    if (t.when) meta.append(el('span', 'hl-when', t.when));
    const txt = el('span', 'hl-text' + (t.ready ? '' : ' muted'), t.headline);
    txt.dataset.clamp = '3';
    body.append(meta, txt);
    b.append(ic, body);
    b.addEventListener('click', () => Story.open(i, b));
    li.appendChild(b);
    list.appendChild(li);
  });
  // "Play all" tile: opens the stories from the first unseen topic.
  const li = el('li', 'hl-all-li');
  li.style.setProperty('--i', topics.length);
  const b = el('button', 'hl-item hl-all');
  b.type = 'button';
  b.setAttribute('aria-label', 'Play all stories');
  const ic = el('span', 'hl-ico hl-bear');
  const tpl = doc.createElementNS(SVGNS, 'svg');
  tpl.setAttribute('viewBox', '0 0 120 112'); tpl.setAttribute('class', 'bear'); tpl.setAttribute('aria-hidden', 'true');
  const use = doc.createElementNS(SVGNS, 'use'); use.setAttribute('href', '#bear-head'); tpl.appendChild(use);
  ic.appendChild(tpl);
  const lab = el('span', 'hl-all-txt', 'Play all');
  const play = el('span', 'hl-play'); play.appendChild(svgEl([P('M8 5.5v13l10.5-6.5z')]));
  b.append(ic, lab, play);
  b.addEventListener('click', () => bearBubble.click());
  li.appendChild(b);
  list.appendChild(li);
}
