/* The full-screen story viewer: slides, progress bars, gestures, keyboard and focus trap. */

import { $, doc, el, P, RM, root, svgEl } from './util.js';
import { decorateIcon, iconFor, orb, paint } from './look.js';
import { topics } from './data.js';
import { markSeen } from './prefs.js';
import { Deck } from './deck.js';

const STORY_MS = 12000;

/* live binding: set by initStory() */
export let Story = null;

export function initStory() {
  Story = (() => {
    const box = $('#story');
    const frame = $('#storyFrame');
    const stage = $('#storyStage');
    const barsEl = $('#storyBars');
    const backdrop = $('#storyBackdrop');
    const pauseBtn = $('#storyPause');
    const closeBtn = $('#storyClose');
    const sticker = $('#sticker');
    const live = $('#storyLive');
    const outside = [$('#nav'), $('#main'), doc.querySelector('.foot'), doc.querySelector('.skip')];
    let isOpen = false, i = 0, slide = null, bars = [], anims = [], lastFocus = null, closing = null;
    const pausers = new Set();

    function buildBars() {
      barsEl.textContent = '';
      bars = topics.map(() => { const b = el('span', 'bar'); b.appendChild(el('i')); barsEl.appendChild(b); return b; });
    }

    function makeSlide(k) {
      const t = topics[k];
      const s = el('article', 'slide');
      paint(s, t);
      s.setAttribute('aria-roledescription', 'story');
      s.setAttribute('aria-label', `${k + 1} of ${topics.length}`);
      s.append(el('span', 'slide-blob a'), el('span', 'slide-blob b'));
      const meta = el('div', 'slide-meta');
      const iw = el('span', 'ico-wrap'); iw.appendChild(decorateIcon(iconFor(t)));
      const mt = el('div');
      mt.append(el('div', 'slide-name', t.title));
      if (t.when) mt.append(el('div', 'slide-when', t.when));
      meta.append(iw, mt);
      const body = el('div', 'slide-body');
      body.appendChild(el('h2', 'slide-title', t.headline));
      if (t.summary) body.appendChild(el('p', 'slide-sum', t.summary));
      if (t.items.length) {
        const ul = el('ul', 'chips');
        t.items.forEach((it, j) => {
          const li = el('li'); li.style.setProperty('--i', j);
          const a = el('a', 'chip');
          a.href = it.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
          a.setAttribute('aria-label', `${it.title}${it.source ? ', ' + it.source : ''} (opens in a new tab)`);
          const tx = el('span', 'chip-txt');
          if (it.source) tx.appendChild(el('span', 'chip-src', it.source));
          tx.appendChild(el('span', 'chip-title', it.title));
          const ar = el('span', 'chip-arrow'); ar.appendChild(svgEl([P('M7 17L17 7M9 7h8v8')]));
          a.append(tx, ar);
          li.appendChild(a); ul.appendChild(li);
        });
        body.appendChild(ul);
      }
      body.addEventListener('pointerenter', (e) => { if (e.pointerType === 'mouse') pause('hover'); });
      body.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') resume('hover'); });
      s.append(meta, orb(t), body, el('span', 'slide-dim'));
      return s;
    }

    function setPaused() {
      frame.classList.toggle('paused', pausers.size > 0);
    }
    function pause(r) { pausers.add(r); setPaused(); }
    function resume(r) { pausers.delete(r); setPaused(); }

    function runBar() {
      bars.forEach((b, k) => {
        b.classList.remove('run');
        b.classList.toggle('done', k < i || (RM && k === i));
      });
      if (!RM) {
        void bars[i].offsetWidth;
        bars[i].style.setProperty('--dur', STORY_MS + 'ms');
        bars[i].classList.add('run');
      }
    }
    barsEl.addEventListener('animationend', (e) => {
      if (!isOpen || !bars[i] || e.target.parentNode !== bars[i]) return;
      go(i + 1, 1);
    });

    function announce() {
      const t = topics[i];
      live.textContent = `Story ${i + 1} of ${topics.length}. ${t.title}. ${t.headline}`;
    }

    function finishAnims() { anims.forEach((a) => { try { a.finish(); } catch (e) { /* */ } }); anims = []; }

    function show(k, dir) {
      finishAnims();
      const old = slide;
      i = k;
      slide = makeSlide(k);
      stage.appendChild(slide);
      slide.classList.add('enter');
      markSeen(topics[k]);
      runBar();
      announce();
      // bear sticker: wave on last story
      sticker.classList.toggle('wave', k === topics.length - 1);
      if (old) {
        if (RM || !dir) {
          old.remove();
        } else {
          const w = stage.offsetWidth;
          const origin = `50% 50% -${w / 2}px`;
          old.style.transformOrigin = origin; slide.style.transformOrigin = origin;
          const opts = { duration: 620, easing: 'cubic-bezier(.65,0,.35,1)', fill: 'both' };
          const a1 = old.animate([{ transform: 'rotateY(0deg)' }, { transform: `rotateY(${-dir * 90}deg)` }], opts);
          const a2 = slide.animate([{ transform: `rotateY(${dir * 90}deg)` }, { transform: 'rotateY(0deg)' }], opts);
          const d1 = old.querySelector('.slide-dim').animate([{ opacity: 0 }, { opacity: 0.55 }], opts);
          const d2 = slide.querySelector('.slide-dim').animate([{ opacity: 0.55 }, { opacity: 0 }], opts);
          const fr = frame.animate([{ transform: 'scale(1)', transformOrigin: '50% 50%' }, { transform: 'scale(.96)', transformOrigin: '50% 50%' }, { transform: 'scale(1)', transformOrigin: '50% 50%' }], { duration: 620, easing: 'ease-in-out' });
          anims = [a1, a2, d1, d2, fr];
          const cur = slide;
          a2.onfinish = () => {
            old.remove();
            [a2, d2].forEach((a) => a.cancel());
            cur.style.transformOrigin = '';
          };
        }
      }
    }

    function go(k, dir) {
      if (!isOpen) return;
      if (k < 0) { show(0, 0); return; }
      if (k >= topics.length) { close(); return; }
      show(k, dir);
    }

    function open(k, src) {
      if (!topics.length) return;
      if (closing) { closing.finish(); }
      if (isOpen) { go(k, k > i ? 1 : -1); return; }
      isOpen = true;
      lastFocus = doc.activeElement;
      box.hidden = false;
      root.classList.add('locked');
      outside.forEach((n) => { if (n) n.inert = true; });
      pausers.clear();
      pauseBtn.setAttribute('aria-pressed', 'false');
      pauseBtn.setAttribute('aria-label', 'Pause stories');
      buildBars();
      stage.textContent = '';
      slide = null;
      show(k, 0);
      frame.style.transform = '';
      if (!RM) {
        sticker.classList.add('reset');
        sticker.classList.remove('peek');
        void sticker.offsetWidth;
        sticker.classList.remove('reset');
        sticker.classList.add('peek');
        backdrop.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, easing: 'ease-out' });
        const f = frame.getBoundingClientRect();
        const s = src && src.getBoundingClientRect ? src.getBoundingClientRect() : null;
        if (s && s.width) {
          const sx = s.width / f.width, sy = s.height / f.height;
          frame.animate([
            { transform: `translate(${s.left - f.left}px, ${s.top - f.top}px) scale(${sx}, ${sy})`, transformOrigin: '0 0', opacity: 0.4 },
            { transform: 'none', transformOrigin: '0 0', opacity: 1 },
          ], { duration: 560, easing: 'cubic-bezier(.2,.85,.25,1)' });
        } else {
          frame.animate([{ transform: 'scale(.9)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 420, easing: 'cubic-bezier(.2,.85,.25,1)' });
        }
      } else {
        sticker.classList.add('peek');
      }
      closeBtn.focus({ preventScroll: true });
    }

    function close(fromY = 0) {
      if (!isOpen) return;
      isOpen = false;
      finishAnims();
      bars.forEach((b) => b.classList.remove('run'));
      const lastIndex = i;
      const done = () => {
        closing = null;
        box.hidden = true;
        frame.style.transform = '';
        backdrop.style.opacity = '';
        stage.textContent = '';
        slide = null;
        sticker.classList.remove('peek', 'wave');
        root.classList.remove('locked');
        outside.forEach((n) => { if (n) n.inert = false; });
        Deck.setSilently(lastIndex);
        if (lastFocus && lastFocus.classList && lastFocus.classList.contains('card')) Deck.focusTop();
        else if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
      };
      if (RM) { done(); return; }
      const a = frame.animate([
        { transform: `translateY(${fromY}px) scale(${1 - fromY / 2000})`, opacity: 1 },
        { transform: `translateY(${fromY + 90}px) scale(.88)`, opacity: 0 },
      ], { duration: 320, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' });
      backdrop.animate([{ opacity: getComputedStyle(backdrop).opacity }, { opacity: 0 }], { duration: 320, fill: 'forwards' });
      closing = a;
      a.onfinish = () => {
        frame.getAnimations().forEach((x) => x.cancel());
        backdrop.getAnimations().forEach((x) => x.cancel());
        done();
      };
    }

    /* controls */
    closeBtn.addEventListener('click', () => close());
    $('#storyPrev').addEventListener('click', () => go(i - 1, -1));
    $('#storyNext').addEventListener('click', () => go(i + 1, 1));
    pauseBtn.addEventListener('click', () => {
      const on = !pausers.has('user');
      if (on) pause('user'); else resume('user');
      pauseBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      pauseBtn.setAttribute('aria-label', on ? 'Play stories' : 'Pause stories');
    });
    backdrop.addEventListener('click', () => close());
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) pause('hidden'); else resume('hidden'); });

    /* gestures: tap zones, hold to pause, swipe sideways, swipe down to close */
    let g = null;
    frame.addEventListener('pointerdown', (e) => {
      if (!isOpen || e.button > 0 || e.target.closest('a, button')) return;
      g = { id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: e.timeStamp, dx: 0, dy: 0, mode: null, s: [[e.timeStamp, e.clientY]] };
      try { frame.setPointerCapture(e.pointerId); } catch (err) { /* */ }
      pause('hold');
    });
    frame.addEventListener('pointermove', (e) => {
      if (!g || e.pointerId !== g.id) return;
      g.dx = e.clientX - g.x0; g.dy = e.clientY - g.y0;
      g.s.push([e.timeStamp, e.clientY]); if (g.s.length > 6) g.s.shift();
      if (!g.mode && Math.hypot(g.dx, g.dy) > 10) g.mode = Math.abs(g.dy) > Math.abs(g.dx) ? 'y' : 'x';
      if (g.mode === 'y' && g.dy > 0 && !RM) {
        frame.style.transform = `translateY(${g.dy}px) scale(${1 - g.dy / 2000})`;
        backdrop.style.opacity = String(Math.max(0.2, 1 - g.dy / 500));
      }
    });
    function endG(e) {
      if (!g || e.pointerId !== g.id) return;
      const d = g; g = null;
      resume('hold');
      const dur = e.timeStamp - d.t0;
      if (d.mode === 'y') {
        const f = d.s[0], l = d.s[d.s.length - 1];
        const vy = (l[1] - f[1]) / Math.max(1, l[0] - f[0]);
        if (d.dy > 110 || (vy > 0.6 && d.dy > 30)) { close(RM ? 0 : d.dy); return; }
        frame.animate([{ transform: frame.style.transform || 'none' }, { transform: 'none' }], { duration: 380, easing: 'cubic-bezier(.3,1.4,.5,1)' });
        frame.style.transform = ''; backdrop.style.opacity = '';
        return;
      }
      if (e.type !== 'pointerup') return;
      if (d.mode === 'x' && Math.abs(d.dx) > 50) { go(d.dx < 0 ? i + 1 : i - 1, d.dx < 0 ? 1 : -1); return; }
      if (!d.mode && dur < 350) {
        const r = frame.getBoundingClientRect();
        if (e.clientX - r.left < r.width * 0.33) go(i - 1, -1); else go(i + 1, 1);
      }
    }
    frame.addEventListener('pointerup', endG);
    frame.addEventListener('pointercancel', endG);

    /* keyboard + focus trap */
    doc.addEventListener('keydown', (e) => {
      if (!isOpen) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(i + 1, 1); return; }
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(i - 1, -1); return; }
      const onControl = e.target instanceof Element && e.target.closest('a, button');
      if (e.key === ' ' && !onControl) { e.preventDefault(); pauseBtn.click(); return; }
      if (e.key === 'Tab') {
        const f = Array.from(box.querySelectorAll('button, a[href]')).filter((n) => n.offsetParent !== null && !n.closest('[inert]'));
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        const inside = box.contains(doc.activeElement);
        if (!inside) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
        else if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });

    return { open, close, get isOpen() { return isOpen; } };
  })();
}
