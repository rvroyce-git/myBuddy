/* DAILY UPDATES
   Visitors pick topics, then open our chat bot with a start link that carries them
   (CONTRACT.md "Subscriptions contract", stable topic bits v2): payload = "t" + lowercase
   hex bitmask with each chosen topic's permanent `bit` set. The bit comes from reports.json;
   older data without one falls back to TOPIC_BITS by id. A topic whose bit is unknown,
   invalid or shared with another topic gets no chip. Chips follow the data's order, so new
   or removed topics just appear or disappear; saved picks are kept by topic id.
   The bot name and the fallback bit map are site settings in app.js. */

import { $, el, own, P, RM, store, svgEl } from './util.js';
import { decorateIcon, iconFor, paint } from './look.js';
import { QR } from './qr.js';

const APP_WAIT = 1200; // ms to wait for the app before falling back to the web link
const SUBS_KEY = 'mybuddy.subs.v1';
const MAX_BITS = 60; // payload is at most 15 hex digits: bits 0–59
/* topics that also bring urgent alerts (security -> actively exploited bugs, hourly) */
const URGENT = { security: true };

/* live binding: set by initSubscribe() */
export let Subs = null;

export function initSubscribe({ botUsername, topicBits }) {
  const BOT_LINK = 'https://t.me/' + botUsername;
  /* the chat app's own link: works where a network blocks the web link's domain */
  const BOT_APP = 'tg://resolve?domain=' + botUsername;
  const TOPIC_BITS = topicBits;
  Subs = (() => {
    const sec = $('#subscribe');
    const card = $('#subsCard');
    const wrap = $('#subsChips');
    const btn = $('#subsBtn');
    const countEl = $('#subsN');
    const qr = $('#qrSvg');
    const note = $('#subsNote');
    const web = $('#subsWeb');
    let ids = [], bitOf = Object.create(null), chosen = new Set(), chips = [], allChip = null;

    function payload(list) {
      const nib = [];
      list.forEach((id) => {
        const i = bitOf[id];
        if (Number.isInteger(i) && i >= 0 && i < MAX_BITS) nib[i >> 2] = (nib[i >> 2] || 0) | (1 << (i & 3));
      });
      let hex = '';
      for (let k = nib.length - 1; k >= 0; k--) hex += (nib[k] || 0).toString(16);
      return hex ? 't' + hex : '';
    }
    const ordered = () => ids.filter((id) => chosen.has(id));
    function linkFor(list) { const p = payload(list); return p ? `${BOT_LINK}?start=${p}` : BOT_LINK; }
    function appFor(list) { const p = payload(list); return p ? `${BOT_APP}&start=${p}` : BOT_APP; }

    function load() {
      let saved;
      try { saved = JSON.parse(store.get(SUBS_KEY) || 'null'); } catch (e) { saved = null; }
      chosen = new Set(Array.isArray(saved) ? saved.filter((id) => typeof id === 'string' && ids.includes(id)) : []);
    }
    function save() { store.set(SUBS_KEY, JSON.stringify(ordered())); }

    function pop(node, delay) {
      if (RM) return;
      node.classList.remove('pop');
      void node.offsetWidth;
      node.style.setProperty('--pd', (delay || 0) + 'ms');
      node.classList.add('pop');
    }
    wrap.parentNode.addEventListener('animationend', (e) => {
      if (e.animationName === 'chipPop') e.target.classList.remove('pop');
    });

    function sync() {
      const list = ordered(), n = list.length;
      chips.forEach((c) => c.setAttribute('aria-pressed', chosen.has(c.dataset.id) ? 'true' : 'false'));
      if (allChip) allChip.setAttribute('aria-pressed', n > 0 && n === ids.length ? 'true' : 'false');
      const was = btn.hasAttribute('href');
      if (n) {
        btn.href = linkFor(list);
        web.href = btn.href;
        web.hidden = false;
        btn.removeAttribute('aria-disabled');
        btn.setAttribute('aria-label', `Get daily updates for ${n} topic${n === 1 ? '' : 's'} (opens in a new tab)`);
        if (!was) pop(btn);
      } else {
        btn.removeAttribute('href');
        web.removeAttribute('href');
        web.hidden = true;
        btn.setAttribute('aria-disabled', 'true');
        btn.setAttribute('aria-label', 'Get daily updates (pick a topic first)');
      }
      if (countEl.textContent !== String(n)) { countEl.textContent = String(n); if (n) pop(countEl); }
      card.classList.toggle('has', n > 0);
      card.classList.toggle('full', n > 0 && n === ids.length);
      note.classList.toggle('on', list.some((id) => own(URGENT, id) === true));
      QR.draw(qr, linkFor(list));
    }

    function chip(cls, label, icon, i) {
      const b = el('button', 'tchip ' + cls);
      b.type = 'button';
      b.style.setProperty('--i', i);
      const ic = el('span', 'tc-ico'); ic.setAttribute('aria-hidden', 'true');
      const chk = svgEl([P('M6 12.5l4 4 8-9')], 'tc-chk');
      ic.append(icon, chk);
      b.append(ic, el('span', 'tc-txt', label));
      return b;
    }

    /* each topic's subscription bit, or null when unknown / invalid / shared */
    function topicBits(list) {
      const raw = list.map((t) => (t.bit === undefined || t.bit === null
        ? (Object.prototype.hasOwnProperty.call(TOPIC_BITS, t.id) ? TOPIC_BITS[t.id] : null)
        : t.bit));
      const ok = raw.map((b) => (Number.isInteger(b) && b >= 0 && b < MAX_BITS ? b : null));
      return ok.map((b) => (b !== null && ok.filter((x) => x === b).length === 1 ? b : null));
    }

    function build(all) {
      const bits = topicBits(all);
      const list = all.filter((t, k) => bits[k] !== null);
      bitOf = Object.create(null);
      all.forEach((t, k) => { if (bits[k] !== null) bitOf[t.id] = bits[k]; });
      ids = list.map((t) => t.id);
      wrap.textContent = '';
      allChip = chip('tc-all', 'All', decorateIcon(svgEl([
        ['rect', { x: '4', y: '4', width: '6.5', height: '6.5', rx: '2' }], ['rect', { x: '13.5', y: '4', width: '6.5', height: '6.5', rx: '2' }],
        ['rect', { x: '4', y: '13.5', width: '6.5', height: '6.5', rx: '2' }], ['rect', { x: '13.5', y: '13.5', width: '6.5', height: '6.5', rx: '2' }],
      ], 'ico')), 0);
      allChip.addEventListener('click', () => {
        const all = chosen.size === ids.length;
        chosen = new Set(all ? [] : ids);
        save();
        pop(allChip);
        chips.forEach((c, k) => pop(c, (k + 1) * 45));
        sync();
      });
      wrap.appendChild(allChip);
      chips = list.map((t, i) => {
        const c = chip('', t.short, decorateIcon(iconFor(t)), i + 1);
        paint(c.querySelector('.tc-ico'), t);
        c.dataset.id = t.id;
        c.dataset.bit = String(bitOf[t.id]);
        if (own(URGENT, t.id) === true) {
          const z = svgEl([P('M13 2.5L5.5 13.5h5.5l-1 8 7.5-11H12z')], 'tc-bolt');
          c.appendChild(z);
          c.title = '+ urgent alerts';
        }
        c.addEventListener('click', () => {
          if (chosen.has(t.id)) chosen.delete(t.id); else chosen.add(t.id);
          save();
          pop(c);
          sync();
        });
        wrap.appendChild(c);
        return c;
      });
      load();
      sync();
    }

    /* the button is a link only once a topic is picked; before that it nudges the chips */
    function nudge() {
      if (btn.hasAttribute('href')) return;
      chips.concat(allChip || []).forEach((c, k) => pop(c, k * 35));
    }
    /* A plain click/tap first tries the app link (some networks block the web link's domain).
       If the page is still in front after APP_WAIT, the app did not open: fall back to the web
       link in a new tab. Modified clicks (new tab, copy link…) keep the plain web link.
       The cancelable "mybuddy:open-app" event lets tests watch the app attempt. */
    let pending = null;
    function tryApp(app, link) {
      if (pending) pending();
      let left = false;
      const away = () => { left = true; };
      const onVis = () => { if (document.hidden) left = true; };
      const timer = setTimeout(() => {
        done();
        if (left || document.hidden) return;
        /* No "noopener" feature here: with it window.open always returns null, so a blocked
           pop-up (Safari blocks pop-ups from timers) could not be told apart. The opener is
           cut right away instead; if the pop-up was blocked, go to the web link in this tab. */
        let w;
        try { w = window.open(link, '_blank'); } catch (e) { w = null; }
        if (w) { try { w.opener = null; } catch (e) { /* cross-origin already */ } } else window.location.assign(link);
      }, APP_WAIT);
      function done() {
        clearTimeout(timer);
        pending = null;
        document.removeEventListener('visibilitychange', onVis);
        window.removeEventListener('blur', away);
        window.removeEventListener('pagehide', away);
      }
      pending = done;
      document.addEventListener('visibilitychange', onVis);
      window.addEventListener('blur', away);
      window.addEventListener('pagehide', away);
      const ev = new CustomEvent('mybuddy:open-app', { detail: { url: app }, cancelable: true });
      if (window.dispatchEvent(ev)) {
        try { window.location.href = app; } catch (e) { /* app scheme refused: the timer falls back */ }
      }
    }
    btn.addEventListener('click', (e) => {
      if (!btn.hasAttribute('href')) { e.preventDefault(); nudge(); return; }
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      const list = ordered();
      tryApp(appFor(list), linkFor(list));
    });
    btn.addEventListener('keydown', (e) => {
      if (!btn.hasAttribute('href') && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); nudge(); }
    });

    /* reveal on scroll */
    let revealed = false;
    function reveal() {
      if (revealed) return;
      revealed = true;
      sec.classList.add('in');
      setTimeout(() => sec.classList.add('done'), RM ? 0 : 1600); // chips have staggered in
    }
    if (RM || !('IntersectionObserver' in window)) reveal();
    else {
      const io = new IntersectionObserver((en) => { if (en.some((x) => x.isIntersecting)) { reveal(); io.disconnect(); } }, { threshold: 0.15 });
      io.observe(sec);
    }

    /* navbar bell: glide to the panel, focus it and make it glow */
    let flashTimer = 0;
    $('#bellBtn').addEventListener('click', (e) => {
      e.preventDefault();
      reveal();
      sec.scrollIntoView({ behavior: RM ? 'auto' : 'smooth', block: 'start' });
      sec.focus({ preventScroll: true });
      card.classList.remove('flash');
      void card.offsetWidth;
      card.classList.add('flash');
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => card.classList.remove('flash'), 2200);
    });

    return { build, payload, linkFor, appFor };
  })();
}
