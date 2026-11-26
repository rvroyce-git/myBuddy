/* The card deck: deal, swipe/fly-out, hover tilt, dots and arrow keys. */

import { $, doc, el, P, RM, svgEl } from './util.js';
import { decorateIcon, iconFor, orb, paint } from './look.js';
import { topics, withAction } from './data.js';
import { Story } from './story.js';

/* live binding: set by initDeck() */
export let Deck = null;

export function initDeck() {
  Deck = (() => {
    const deckEl = $('#deck');
    const dotsEl = $('#dots');
    const live = $('#deckLive');
    const bear = $('#deckBear');
    let cards = [], dots = [], cur = 0, fly = null, drag = null, dealt = false;
    const n = () => cards.length;

    const FAN = [0, -5, 4, -2.5];
    function pose(p) {
      const k = Math.min(p, 3);
      return { x: 0, y: k * 16, r: FAN[k], s: 1 - k * 0.06, o: p > 3 ? 0 : 1, sh: p > 3 ? 0.5 : k * 0.16 };
    }
    const tf = (q) => `translate3d(${q.x}px, ${q.y}px, 0) rotate(${q.r}deg) scale(${q.s})`;
    const mix = (a, b, t) => a + (b - a) * t;

    function build() {
      settleFly();
      deckEl.querySelectorAll('.card').forEach((c) => c.remove());
      dotsEl.textContent = '';
      dots = [];
      cur = 0;
      cards = topics.map((t, i) => {
        const c = el('div', 'card');
        paint(c, t);
        c.setAttribute('role', 'button');
        c.setAttribute('aria-label', withAction(`${t.title}: ${t.headline}`, 'Open story'));
        c.dataset.i = i;
        const inner = el('div', 'card-in');
        const top = el('div', 'card-top');
        const tag = el('span', 'tag');
        const iw = el('span', 'ico-wrap'); iw.appendChild(decorateIcon(iconFor(t)));
        tag.append(iw, el('span', 'tag-name', t.title));
        top.append(tag, el('span', 'num', String(i + 1).padStart(2, '0')));
        const bottom = el('div', 'card-bottom');
        const txt = el('div');
        if (t.when) txt.appendChild(el('span', 'when', t.when));
        const hl = el('h3', 'card-hl' + (t.ready ? '' : ' muted'), t.headline);
        hl.dataset.clamp = '3';
        txt.appendChild(hl);
        const go = el('span', 'go');
        go.appendChild(svgEl([P('M8 5.5v13l10.5-6.5z')]));
        bottom.append(txt, go);
        inner.append(top, orb(t), bottom, el('span', 'card-sheen'), el('span', 'card-shade'));
        c.appendChild(inner);
        deckEl.appendChild(c);

        const d = el('button', 'dot');
        d.type = 'button';
        d.setAttribute('aria-label', `Show ${t.title}`);
        d.appendChild(el('span'));
        d.addEventListener('click', () => goTo(i));
        dotsEl.appendChild(d);
        dots.push(d);
        return c;
      });
      deal();
    }

    function deal() {
      if (RM) { render(); dealt = true; return; }
      cards.forEach((c, i) => {
        c.classList.add('notrans');
        c.style.transform = `translate3d(0, 120px, 0) rotate(${(i % 2 ? 1 : -1) * 14}deg) scale(.8)`;
        c.style.opacity = '0';
      });
      void deckEl.offsetWidth;
      cards.forEach((c, i) => {
        c.classList.remove('notrans');
        const p = (i - cur + n()) % n();
        c.style.transitionDelay = `${(n() - 1 - p) * 70 + 250}ms`;
      });
      render();
      setTimeout(() => { cards.forEach((c) => { c.style.transitionDelay = ''; }); dealt = true; }, 1400);
    }

    function render(skip) {
      const N = n();
      cards.forEach((c, i) => {
        const p = (i - cur + N) % N;
        c._p = p;
        const top = p === 0;
        c.classList.toggle('is-top', top);
        c.tabIndex = top ? 0 : -1;
        c.setAttribute('aria-hidden', top ? 'false' : 'true');
        if (c === skip) return;
        const q = pose(p);
        c.style.transform = tf(q);
        c.style.opacity = String(q.o);
        c.style.zIndex = String(N - p + 1);
        c.style.setProperty('--sh', q.sh);
      });
      dots.forEach((d, i) => d.setAttribute('aria-current', i === cur ? 'true' : 'false'));
      bear.classList.toggle('wave', N > 0 && cur === N - 1);
      if (topics[cur]) live.textContent = `${cur + 1} of ${N}: ${topics[cur].title}`;
    }

    function settleFly() { if (fly) fly.finish(); }

    function flyOut(dir, vx = 0, vy = 0) {
      if (!n()) return;
      settleFly();
      const c = cards[cur];
      const from = getComputedStyle(c).transform;
      c.classList.remove('dragging');
      c.style.zIndex = String(n() + 3);
      cur = (cur + 1) % n();
      render(c);
      if (RM) { c.classList.add('notrans'); render(); void c.offsetWidth; c.classList.remove('notrans'); return; }
      const dist = dir * (innerWidth * 0.5 + c.offsetWidth * 1.1);
      const endY = (drag ? drag.dy * 0.35 : -20) + vy * 140;
      const dur = Math.max(260, Math.min(560, 560 - Math.abs(vx) * 160));
      const a = c.animate([
        { transform: from === 'none' ? 'none' : from, opacity: 1 },
        { transform: `translate3d(${dist}px, ${endY}px, 0) rotate(${dir * 30}deg)`, opacity: 0 },
      ], { duration: dur, easing: 'cubic-bezier(.2,.65,.35,1)', fill: 'forwards' });
      fly = a;
      a.onfinish = () => {
        c.classList.add('notrans');
        const q = pose(c._p);
        c.style.transform = tf(q); c.style.opacity = String(q.o); c.style.zIndex = String(n() - c._p + 1);
        c.style.setProperty('--sh', q.sh);
        a.cancel();
        void c.offsetWidth;
        c.classList.remove('notrans');
        if (fly === a) fly = null;
      };
    }

    function next() { flyOut(-1); }
    function prev() {
      if (!n()) return;
      settleFly();
      cur = (cur - 1 + n()) % n();
      const c = cards[cur];
      if (!RM) {
        c.classList.add('notrans');
        c.style.transform = `translate3d(${-(innerWidth * 0.5 + c.offsetWidth)}px, -30px, 0) rotate(-28deg)`;
        c.style.opacity = '0';
        c.style.zIndex = String(n() + 3);
        void c.offsetWidth;
        c.classList.remove('notrans');
      }
      render();
    }
    function goTo(i) {
      if (!n() || i === cur) return;
      if (i === (cur + 1) % n()) return next();
      if (i === (cur - 1 + n()) % n()) return prev();
      settleFly();
      cur = i;
      render();
    }
    function setSilently(i) { settleFly(); cur = i; render(); }

    /* drag / swipe */
    deckEl.addEventListener('pointerdown', (e) => {
      const c = e.target.closest('.card');
      if (!c || !c.classList.contains('is-top') || e.button > 0 || !dealt) return;
      if (fly) settleFly();
      drag = { c, id: e.pointerId, x0: e.clientX, y0: e.clientY, dx: 0, dy: 0, moved: false, s: [[e.timeStamp, e.clientX, e.clientY]] };
      try { c.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    });
    deckEl.addEventListener('pointermove', (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      drag.dx = e.clientX - drag.x0;
      drag.dy = e.clientY - drag.y0;
      if (!drag.moved && Math.hypot(drag.dx, drag.dy) > 6) {
        drag.moved = true;
        drag.c.classList.add('dragging');
        drag.c.style.setProperty('--rx', '0deg'); drag.c.style.setProperty('--ry', '0deg');
      }
      drag.s.push([e.timeStamp, e.clientX, e.clientY]);
      if (drag.s.length > 6) drag.s.shift();
      if (!drag.moved) return;
      const { c, dx, dy } = drag;
      c.style.transform = `translate3d(${dx}px, ${dy * 0.35}px, 0) rotate(${dx * 0.06}deg)`;
      const prog = Math.min(Math.abs(dx) / (c.offsetWidth * 0.6), 1);
      cards.forEach((o) => {
        if (o === c || o._p > 4) return;
        const a = pose(o._p), b = pose(o._p - 1);
        o.classList.add('follow');
        o.style.transform = tf({ x: 0, y: mix(a.y, b.y, prog), r: mix(a.r, b.r, prog), s: mix(a.s, b.s, prog) });
        o.style.opacity = String(mix(a.o, b.o, prog));
        o.style.setProperty('--sh', mix(a.sh, b.sh, prog));
      });
      bear.classList.toggle('peek', prog > 0.15);
    });
    function endDrag(e) {
      if (!drag || e.pointerId !== drag.id) return;
      const d = drag;
      cards.forEach((o) => o.classList.remove('follow'));
      bear.classList.remove('peek');
      if (!d.moved) {
        drag = null;
        if (e.type === 'pointerup') Story.open(cur, d.c);
        return;
      }
      const first = d.s[0], last = d.s[d.s.length - 1];
      const dt = Math.max(1, last[0] - first[0]);
      const vx = (last[1] - first[1]) / dt, vy = (last[2] - first[2]) / dt;
      const far = Math.abs(d.dx) > d.c.offsetWidth * 0.3;
      const flick = Math.abs(vx) > 0.45 && Math.abs(d.dx) > 30 && Math.sign(vx) === Math.sign(d.dx);
      if (far || flick) {
        flyOut(Math.sign(d.dx) || 1, vx, vy);
      } else {
        d.c.classList.remove('dragging');
        render();
      }
      drag = null;
    }
    deckEl.addEventListener('pointerup', endDrag);
    deckEl.addEventListener('pointercancel', endDrag);
    deckEl.addEventListener('lostpointercapture', (e) => { if (drag && e.pointerId === drag.id) endDrag({ pointerId: e.pointerId, type: 'pointercancel' }); });

    /* hover tilt (mouse only) */
    deckEl.addEventListener('pointermove', (e) => {
      if (RM || e.pointerType !== 'mouse' || drag) return;
      const c = e.target.closest('.card.is-top');
      if (!c) return;
      const r = c.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5, py = (e.clientY - r.top) / r.height - 0.5;
      c.style.setProperty('--ry', (px * 12).toFixed(2) + 'deg');
      c.style.setProperty('--rx', (-py * 10).toFixed(2) + 'deg');
      c.style.setProperty('--mx', (px * 60).toFixed(1) + '%');
      c.style.setProperty('--my', (py * 60).toFixed(1) + '%');
    });
    deckEl.addEventListener('pointerout', (e) => {
      const c = e.target.closest('.card');
      if (c && !c.contains(e.relatedTarget)) { c.style.setProperty('--rx', '0deg'); c.style.setProperty('--ry', '0deg'); }
    });

    deckEl.addEventListener('keydown', (e) => {
      const c = e.target.closest('.card.is-top');
      if (c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); Story.open(cur, c); }
    });

    $('#prevBtn').addEventListener('click', prev);
    $('#nextBtn').addEventListener('click', next);

    return {
      build, next, prev, setSilently,
      get cur() { return cur; },
      topCard: () => cards[cur],
      focusTop() { focusCard(); },
    };
    function focusCard() { const c = cards[cur]; if (c) c.focus({ preventScroll: true }); }
  })();
}

/* Arrow keys move the deck (not while a story is open or the user is typing). */
export function bindDeckKeys() {
  doc.addEventListener('keydown', (e) => {
    if (Story.isOpen || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (e.key === 'ArrowRight') { e.preventDefault(); Deck.next(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); Deck.prev(); }
  });
}
