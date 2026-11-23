/* MyBuddy · Concept B "News stories"
   Vanilla JS. No dependencies. Data is rendered with textContent only. */
(() => {
  'use strict';

  /* Colour palette: change the default here (and in the inline <head> script). Preview any
     palette with ?palette=NAME. Each name is a data-palette block in styles.css. */
  const DEFAULT_PALETTE = 'periwinkle';
  const PALETTES = ['honey', 'mint', 'periwinkle'];

  const doc = document;
  const root = doc.documentElement;
  const $ = (s, r = doc) => r.querySelector(s);
  const SVGNS = 'http://www.w3.org/2000/svg';
  const STORY_MS = 12000;

  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  let RM = motionQuery.matches;
  motionQuery.addEventListener?.('change', (e) => { RM = e.matches; });

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
    remove(k) { try { localStorage.removeItem(k); } catch (e) { /* storage blocked */ } },
  };

  /* ---------- tiny DOM helpers ---------- */
  function el(tag, cls, text) {
    const n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function svgEl(paths, cls) {
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

  /* ---------- topic look ---------- */
  const P = (d) => ['path', { d }];
  const ICONS = {
    ai_news: [P('M11 3l1.8 4.7 4.7 1.8-4.7 1.8L11 16l-1.8-4.7L4.5 9.5l4.7-1.8z'), P('M18.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z')],
    security: [P('M12 3l7 3v5.2c0 4.4-3 8.2-7 9.8-4-1.6-7-5.4-7-9.8V6z'), P('M9 12l2.2 2.2L15.5 10')],
    github_trending: [P('M12 3.5l2.6 5.3 5.9.9-4.25 4.1 1 5.8L12 16.9l-5.25 2.7 1-5.8L3.5 9.7l5.9-.9z')],
    deep_dive: [['circle', { cx: '10.5', cy: '10.5', r: '6.5' }], P('M15.5 15.5L21 21'), P('M8 10.5a2.5 2.5 0 0 1 2.5-2.5')],
    research_papers: [P('M6 3h8l4 4v14H6z'), P('M14 3v4h4'), P('M9 12h6M9 16h6')],
    startups: [P('M12 2.5c3 2 4.5 5.5 4.5 9.5l-2 3h-5l-2-3c0-4 1.5-7.5 4.5-9.5z'), ['circle', { cx: '12', cy: '9.5', r: '1.7' }], P('M9.5 15l-3 2.5 1-4.5M14.5 15l3 2.5-1-4.5M10.5 18.5c.4 1.3.9 2.3 1.5 3 .6-.7 1.1-1.7 1.5-3')],
    ai_models: [P('M12 2.5l8 4.5v9l-8 4.5-8-4.5v-9z'), P('M4 7l8 4.5L20 7M12 11.5V21')],
  };
  const SHORT = {
    ai_news: 'AI news', security: 'Security', github_trending: 'GitHub', deep_dive: 'Deep dive',
    research_papers: 'Papers', startups: 'Launches', ai_models: 'Models',
  };
  function iconFor(t, cls) {
    if (ICONS[t.id]) return svgEl(ICONS[t.id], 'ico ' + (cls || ''));
    const s = el('span', 'emoji', t.emoji || '•');
    s.setAttribute('aria-hidden', 'true');
    return s;
  }
  /* Every topic shares the palette's card gradient (--card-1/--card-2); only the angle varies. */
  function paint(node, t) {
    node.style.setProperty('--ang', t.ang + 'deg');
  }

  /* ---------- time ----------
     The schedule is fixed in IST, but every time is shown in the visitor's own time zone,
     without a zone label. Words stay English (the page is English); only the 12/24-hour
     preference comes from the visitor's locale. IST is only used internally. */
  const IST = 'Asia/Kolkata';
  const H12 = (() => {
    try {
      const h = new Intl.DateTimeFormat(navigator.language, { hour: 'numeric' }).resolvedOptions();
      return h.hour12 !== undefined ? h.hour12 : !/^h2[34]$/.test(h.hourCycle || '');
    } catch (e) { return true; }
  })();
  const fmtTime = new Intl.DateTimeFormat('en', H12
    ? { hour: 'numeric', minute: '2-digit', hourCycle: 'h12' }
    : { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const fmtDate = new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short' });
  const fmtWeekday = new Intl.DateTimeFormat('en', { weekday: 'long' });
  const fmtLocalDay = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' });
  const fmtDay = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: IST });
  const dayNum = (key) => Date.UTC(+key.slice(0, 4), +key.slice(5, 7) - 1, +key.slice(8, 10)) / 864e5;
  const dayKey = (n) => new Date(n * 864e5).toISOString().slice(0, 10);
  const localGap = (d) => dayNum(fmtLocalDay.format(d)) - dayNum(fmtLocalDay.format(new Date()));
  /* "5:00 PM" today, "Yesterday · 5:00 PM" or "Nov 22 · 5:00 PM" (visitor's zone).
     `mid` lowercases the word for use inside a phrase ("Updated yesterday · …"). */
  function localStamp(d, mid) {
    const time = fmtTime.format(d);
    const gap = localGap(d);
    if (gap === 0) return time;
    if (gap === -1) return (mid ? 'yesterday' : 'Yesterday') + ' · ' + time;
    return fmtDate.format(d) + ' · ' + time;
  }
  const IST_AT = (key, h, m) => new Date(`${key}T${String(h).padStart(2, '0')}:${m}:00+05:30`);
  /* An "HH:MM" IST time on today's IST date, as a local clock time. */
  function istClock(hm) {
    const m = /^(\d{1,2}):(\d{2})$/.exec(hm || '');
    if (!m) return '';
    const d = IST_AT(fmtDay.format(new Date()), m[1], m[2]);
    return isNaN(d) ? '' : fmtTime.format(d);
  }
  /* Schedule label from the data ("Daily · 06:30 IST", "Mon–Fri · 07:30 IST", "Sat, Sun · …")
     -> the next run, as "Due 9:00 PM", "Due tomorrow · 9:00 PM" or "Back Monday". */
  const WD = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
  function scheduleDays(label) {
    const l = String(label || '').toLowerCase();
    if (/daily/.test(l)) return [0, 1, 2, 3, 4, 5, 6];
    if (/weekend/.test(l)) return [6, 0];
    const r = /\b(sun|mon|tue|wed|thu|fri|sat)\w*\s*[–-]\s*(sun|mon|tue|wed|thu|fri|sat)/.exec(l);
    if (r) { const out = []; for (let k = WD[r[1]]; ; k = (k + 1) % 7) { out.push(k); if (k === WD[r[2]]) break; } return out; }
    const list = (l.match(/\b(sun|mon|tue|wed|thu|fri|sat)/g) || []).map((x) => WD[x]);
    return list.length ? list : [0, 1, 2, 3, 4, 5, 6];
  }
  const LATE_GRACE_MS = 60 * 60 * 1000; // a run that is a little late is still "due" today
  function dueText(schedule) {
    const m = /(\d{1,2}):(\d{2})/.exec(String(schedule || ''));
    if (!m) return '';
    const days = scheduleDays(String(schedule).split('·')[0]);
    const now = Date.now(), base = dayNum(fmtDay.format(new Date()));
    for (let k = 0; k < 8; k++) {
      const key = dayKey(base + k);
      if (!days.includes(new Date((base + k) * 864e5).getUTCDay())) continue;
      const at = IST_AT(key, m[1], m[2]);
      if (isNaN(at) || at.getTime() + LATE_GRACE_MS <= now) continue;
      const gap = localGap(at);
      if (gap <= 0) return 'Due ' + fmtTime.format(at);
      if (gap === 1) return 'Due tomorrow · ' + fmtTime.format(at);
      return 'Back ' + fmtWeekday.format(at);
    }
    return '';
  }
  function localizeStatic() {
    const ready = istClock('08:15');
    if (ready) $('#readyBy').textContent = `Ready by ${ready}.`;
    doc.querySelectorAll('[data-ist]').forEach((n) => { const t = istClock(n.dataset.ist); if (t) n.textContent = t; });
  }

  /* ---------- data helpers ---------- */
  function validDate(iso) { const d = new Date(iso); return isNaN(d) ? null : d; }
  /* Only absolute https: links are allowed (data contract). */
  function safeUrl(u) {
    if (typeof u !== 'string') return null;
    const s = u.trim();
    if (!/^https:\/\/[^/\\\s]/i.test(s)) return null;
    try {
      const x = new URL(s);
      return x.protocol === 'https:' && x.hostname ? x.href : null;
    } catch (e) { return null; }
  }
  function firstSentences(text, max) {
    const s = String(text || '').trim();
    if (!s) return '';
    const parts = s.match(/[^.!?]+[.!?]+["')\]]*(\s+|$)|[^.!?]+$/g) || [s];
    return parts.slice(0, max).join('').trim();
  }
  function normalize(raw) {
    return (Array.isArray(raw.topics) ? raw.topics : []).filter((t) => t && t.id).map((t, k) => {
      const id = String(t.id);
      const r = t.report && typeof t.report === 'object' ? t.report : null;
      const d = r ? validDate(r.created_at) : null;
      let when = '';
      if (d) {
        when = localStamp(d);
      } else {
        when = dueText(t.schedule);
      }
      const items = r && Array.isArray(r.items) ? r.items : [];
      return {
        id, ang: 135 + (k % 5) * 15,
        bit: t.bit, // stable subscription bit (may be missing in older data)
        title: String(t.title || id),
        short: SHORT[id] || String(t.title || id),
        emoji: t.emoji ? String(t.emoji) : '',
        ready: !!(r && r.headline),
        headline: r && r.headline ? String(r.headline) : 'Coming soon',
        summary: r ? firstSentences(r.summary, 2) : '',
        stamp: r ? String(r.created_at || 'x') : 'none',
        when,
        items: items.map((it) => ({
          url: safeUrl(it && it.url),
          title: String((it && it.title) || ''),
          source: String((it && it.source) || ''),
        })).filter((it) => it.url && it.title).slice(0, 3),
      };
    });
  }

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
  try {
    if (CSS.supports('transition-timing-function', 'linear(0, 0.5, 1)')) {
      const sp = springEasing(190, 17);
      root.style.setProperty('--spring', sp.css);
      root.style.setProperty('--spring-dur', sp.ms + 'ms');
    }
  } catch (e) { /* older browser: CSS fallback curve */ }

  /* ---------- palette ---------- */
  function pickPalette() {
    let q = '';
    try { q = new URLSearchParams(location.search).get('palette') || ''; } catch (e) { q = ''; }
    q = q.toLowerCase().replace(/[^a-z-]/g, '').slice(0, 32);
    return PALETTES.includes(q) ? q : DEFAULT_PALETTE;
  }
  root.setAttribute('data-palette', pickPalette());

  /* ---------- theme ----------
     Always opens dark. The toggle switches for this visit only (never saved). */
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

  /* ---------- navbar border on scroll ---------- */
  const nav = $('#nav');
  addEventListener('scroll', () => nav.classList.toggle('scrolled', scrollY > 8), { passive: true });

  /* ---------- "How it works" tooltips ----------
     Hover (mouse) or keyboard focus shows a tip; a click/tap pins it open or closes it.
     Esc, tapping elsewhere or leaving focus closes it. Positioned so it never leaves the viewport. */
  const Tips = (() => {
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
    return { hide };
  })();

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

  localizeStatic();

  /* ---------- seen state ---------- */
  /* Seen state resets every day: it is keyed to the day of generated_at (IST). */
  const SEEN_KEY = 'mybuddy.seen.v2';
  let seen = {};
  let seenDay = '';
  function loadSeen(day) {
    seenDay = day || 'unknown';
    let saved = null;
    try { saved = JSON.parse(store.get(SEEN_KEY) || 'null'); } catch (e) { saved = null; }
    seen = saved && saved.day === seenDay && saved.ids && typeof saved.ids === 'object' ? saved.ids : {};
  }
  const isSeen = (t) => seen[t.id] === t.stamp;
  function markSeen(t) {
    if (isSeen(t)) return;
    seen[t.id] = t.stamp;
    store.set(SEEN_KEY, JSON.stringify({ day: seenDay, ids: seen }));
    refreshBubbles();
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
  function clampAll() {
    doc.querySelectorAll('[data-clamp]').forEach((n) => clampWords(n, +n.dataset.clamp || 3));
  }
  let clampTimer = 0;
  addEventListener('resize', () => { clearTimeout(clampTimer); clampTimer = setTimeout(clampAll, 150); });

  /* ---------- state ---------- */
  let topics = [];
  const bubblesEl = $('#bubbles');
  const bearBubble = $('#bearBubble');
  let bubbleBtns = [];

  function refreshBubbles() {
    bubbleBtns.forEach((b, i) => {
      const s = isSeen(topics[i]);
      b.classList.toggle('seen', s);
      b.setAttribute('aria-label', `${topics[i].title} story${s ? ', seen' : ''}`);
    });
    bearBubble.classList.toggle('seen', topics.length > 0 && topics.every(isSeen));
  }

  function buildBubbles() {
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
  bearBubble.addEventListener('click', () => {
    const first = topics.findIndex((t) => !isSeen(t));
    Story.open(first < 0 ? 0 : first, bearBubble);
  });

  function decorateIcon(svg) {
    if (svg.tagName.toLowerCase() !== 'svg') return svg;
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.9');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    return svg;
  }
  function orb(t) {
    const wrap = el('div', 'art');
    const ow = el('div', 'orb-wrap');
    const o = el('div', 'orb');
    o.append(decorateIcon(iconFor(t)), el('i'), el('i'), el('i'));
    ow.appendChild(o);
    wrap.appendChild(ow);
    return wrap;
  }

  function buildList() {
    const list = $('#hlList');
    list.textContent = '';
    topics.forEach((t, i) => {
      const li = el('li');
      li.style.setProperty('--i', i);
      const b = el('button', 'hl-item');
      b.type = 'button';
      paint(b, t);
      b.setAttribute('aria-label', `${t.title}: ${t.headline}. Open story`);
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

  /* =====================================================================
     DECK
     ===================================================================== */
  const Deck = (() => {
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
      deckEl.querySelectorAll('.card').forEach((c) => c.remove());
      dotsEl.textContent = '';
      cards = topics.map((t, i) => {
        const c = el('div', 'card');
        paint(c, t);
        c.setAttribute('role', 'button');
        c.setAttribute('aria-label', `${t.title}: ${t.headline}. Open story`);
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

  /* =====================================================================
     STORY VIEWER
     ===================================================================== */
  const Story = (() => {
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
      if (e.key === ' ' && !e.target.closest('a, button')) { e.preventDefault(); pauseBtn.click(); return; }
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

  /* global arrow keys for the deck */
  doc.addEventListener('keydown', (e) => {
    if (Story.isOpen || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (e.key === 'ArrowRight') { e.preventDefault(); Deck.next(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); Deck.prev(); }
  });

  /* =====================================================================
     QR CODE (tiny self-contained encoder)
     Byte mode, error correction level M, versions 1–6 (up to 106 bytes) — plenty for the
     start link. Follows ISO/IEC 18004; the tests decode it and check the Reed–Solomon blocks.
     ===================================================================== */
  const QR = (() => {
    const TOTAL = [0, 26, 44, 70, 100, 134, 172]; // codewords per version
    const ECC = [0, 10, 16, 26, 18, 24, 16];      // ECC codewords per block (level M)
    const BLOCKS = [0, 1, 1, 1, 2, 2, 4];         // blocks (level M)
    function gfMul(x, y) {
      let z = 0;
      for (let i = 7; i >= 0; i--) {
        z = (z << 1) ^ ((z >>> 7) * 0x11D);
        z ^= ((y >>> i) & 1) * x;
      }
      return z;
    }
    function rsDivisor(deg) {
      const r = new Array(deg).fill(0);
      r[deg - 1] = 1;
      let root = 1;
      for (let i = 0; i < deg; i++) {
        for (let j = 0; j < deg; j++) {
          r[j] = gfMul(r[j], root);
          if (j + 1 < deg) r[j] ^= r[j + 1];
        }
        root = gfMul(root, 2);
      }
      return r;
    }
    function rsRemainder(data, div) {
      const r = div.map(() => 0);
      data.forEach((b) => {
        const f = b ^ r.shift();
        r.push(0);
        div.forEach((c, i) => { r[i] ^= gfMul(c, f); });
      });
      return r;
    }
    function codewords(bytes, v) {
      const nb = BLOCKS[v], ecl = ECC[v], cap = (TOTAL[v] - ecl * nb) * 8;
      const bits = [];
      const put = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
      put(4, 4); put(bytes.length, 8); bytes.forEach((b) => put(b, 8));
      put(0, Math.min(4, cap - bits.length));
      put(0, (8 - (bits.length % 8)) % 8);
      for (let pad = 0xEC; bits.length < cap; pad ^= 0xEC ^ 0x11) put(pad, 8);
      const data = [];
      for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
      const short = Math.floor(TOTAL[v] / nb), nShort = nb - (TOTAL[v] % nb), div = rsDivisor(ecl);
      const blocks = [];
      for (let i = 0, k = 0; i < nb; i++) {
        const len = short - ecl + (i < nShort ? 0 : 1);
        const d = data.slice(k, k + len); k += len;
        blocks.push([d, rsRemainder(d, div)]);
      }
      const out = [];
      for (let i = 0; i <= short - ecl; i++) blocks.forEach(([d]) => { if (i < d.length) out.push(d[i]); });
      for (let i = 0; i < ecl; i++) blocks.forEach(([, e]) => out.push(e[i]));
      return out;
    }
    function maskHit(m, x, y) {
      switch (m) {
        case 0: return (x + y) % 2 === 0;
        case 1: return y % 2 === 0;
        case 2: return x % 3 === 0;
        case 3: return (x + y) % 3 === 0;
        case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
        case 5: return ((x * y) % 2) + ((x * y) % 3) === 0;
        case 6: return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
        default: return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
      }
    }
    function penalty(g) {
      const n = g.length;
      let p = 0, dark = 0;
      for (let pass = 0; pass < 2; pass++) {
        for (let a = 0; a < n; a++) {
          let run = 0, prev = -1, line = '';
          for (let b = 0; b < n; b++) {
            const c = pass ? g[b][a] : g[a][b];
            line += c;
            if (c === prev) run++; else { if (run >= 5) p += run - 2; run = 1; prev = c; }
          }
          if (run >= 5) p += run - 2;
          const s = '0000' + line + '0000';
          for (const pat of ['10111010000', '00001011101']) {
            for (let k = s.indexOf(pat); k >= 0; k = s.indexOf(pat, k + 1)) p += 40;
          }
        }
      }
      for (let y = 0; y < n; y++) {
        for (let x = 0; x < n; x++) {
          dark += g[y][x];
          if (x < n - 1 && y < n - 1) {
            const c = g[y][x];
            if (c === g[y][x + 1] && c === g[y + 1][x] && c === g[y + 1][x + 1]) p += 3;
          }
        }
      }
      return p + Math.floor(Math.abs(dark * 20 - n * n * 10) / (n * n)) * 10;
    }
    /* Returns a size×size grid of 0/1 (1 = dark), or null when the text is too long. */
    function matrix(text) {
      const bytes = Array.from(new TextEncoder().encode(text));
      let v = 1;
      while (v <= 6 && 12 + bytes.length * 8 > (TOTAL[v] - ECC[v] * BLOCKS[v]) * 8) v++;
      if (v > 6) return null;
      const n = 17 + 4 * v;
      const g = Array.from({ length: n }, () => new Array(n).fill(0));
      const fn = Array.from({ length: n }, () => new Array(n).fill(false));
      const set = (grid, x, y, d) => { grid[y][x] = d ? 1 : 0; fn[y][x] = true; };
      for (let i = 0; i < n; i++) { set(g, 6, i, i % 2 === 0); set(g, i, 6, i % 2 === 0); }
      [[3, 3], [n - 4, 3], [3, n - 4]].forEach(([cx, cy]) => {
        for (let dy = -4; dy <= 4; dy++) {
          for (let dx = -4; dx <= 4; dx++) {
            const x = cx + dx, y = cy + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
            if (x >= 0 && y >= 0 && x < n && y < n) set(g, x, y, d !== 2 && d !== 4);
          }
        }
      });
      if (v >= 2) {
        for (let dy = -2; dy <= 2; dy++) {
          for (let dx = -2; dx <= 2; dx++) set(g, n - 7 + dx, n - 7 + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
        }
      }
      const format = (grid, mask) => {
        const data = mask; // level M = 00
        let rem = data;
        for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
        const bits = ((data << 10) | rem) ^ 0x5412;
        const bit = (i) => (bits >>> i) & 1;
        for (let i = 0; i <= 5; i++) set(grid, 8, i, bit(i));
        set(grid, 8, 7, bit(6)); set(grid, 8, 8, bit(7)); set(grid, 7, 8, bit(8));
        for (let i = 9; i < 15; i++) set(grid, 14 - i, 8, bit(i));
        for (let i = 0; i < 8; i++) set(grid, n - 1 - i, 8, bit(i));
        for (let i = 8; i < 15; i++) set(grid, 8, n - 15 + i, bit(i));
        set(grid, 8, n - 8, 1); // dark module
      };
      format(g, 0); // reserve the format areas
      const cw = codewords(bytes, v);
      let i = 0;
      for (let right = n - 1; right >= 1; right -= 2) {
        if (right === 6) right = 5;
        for (let vert = 0; vert < n; vert++) {
          for (let j = 0; j < 2; j++) {
            const x = right - j, y = ((right + 1) & 2) === 0 ? n - 1 - vert : vert;
            if (fn[y][x]) continue;
            g[y][x] = i < cw.length * 8 ? (cw[i >>> 3] >>> (7 - (i & 7))) & 1 : 0;
            i++;
          }
        }
      }
      let best = null, bestP = Infinity;
      for (let m = 0; m < 8; m++) {
        const t = g.map((row, y) => row.map((c, x) => (!fn[y][x] && maskHit(m, x, y) ? c ^ 1 : c)));
        format(t, m);
        const p = penalty(t);
        if (p < bestP) { bestP = p; best = t; }
      }
      return best;
    }
    function draw(svg, text) {
      const m = matrix(text);
      if (!m) return false;
      const n = m.length, q = 4;
      let d = '';
      m.forEach((row, y) => row.forEach((c, x) => { if (c) d += `M${x + q} ${y + q}h1v1h-1z`; }));
      svg.setAttribute('viewBox', `0 0 ${n + 2 * q} ${n + 2 * q}`);
      svg.setAttribute('shape-rendering', 'crispEdges');
      svg.dataset.n = String(n);
      svg.dataset.text = text;
      svg.textContent = '';
      const bg = doc.createElementNS(SVGNS, 'rect');
      bg.setAttribute('width', '100%'); bg.setAttribute('height', '100%'); bg.setAttribute('class', 'qr-bg');
      const p = doc.createElementNS(SVGNS, 'path');
      p.setAttribute('d', d); p.setAttribute('class', 'qr-dots');
      svg.append(bg, p);
      return true;
    }
    return { matrix, draw };
  })();

  /* =====================================================================
     DAILY UPDATES
     Visitors pick topics, then open our chat bot with a start link that carries them
     (CONTRACT.md "Subscriptions contract", stable topic bits v2): payload = "t" + lowercase
     hex bitmask with each chosen topic's permanent `bit` set. The bit comes from reports.json;
     older data without one falls back to TOPIC_BITS by id. A topic whose bit is unknown,
     invalid or shared with another topic gets no chip. Chips follow the data's order, so new
     or removed topics just appear or disappear; saved picks are kept by topic id.
     ===================================================================== */
  const BOT_USERNAME = 'myBuddy_update_bot';
  const BOT_LINK = 'https://t.me/' + BOT_USERNAME;
  /* the chat app's own link: works where a network blocks the web link's domain */
  const BOT_APP = 'tg://resolve?domain=' + BOT_USERNAME;
  const APP_WAIT = 1200; // ms to wait for the app before falling back to the web link
  const SUBS_KEY = 'mybuddy.subs.v1';
  const MAX_BITS = 60; // payload is at most 15 hex digits: bits 0–59
  /* fallback when reports.json has no `bit` for a topic (same as config/topics.yaml) */
  const TOPIC_BITS = {
    ai_news: 0, security: 1, github_trending: 2, deep_dive: 3, research_papers: 4, startups: 5, ai_models: 6,
  };
  /* topics that also bring urgent alerts (security -> actively exploited bugs, hourly) */
  const URGENT = { security: true };
  const Subs = (() => {
    const sec = $('#subscribe');
    const card = $('#subsCard');
    const wrap = $('#subsChips');
    const btn = $('#subsBtn');
    const countEl = $('#subsN');
    const qr = $('#qrSvg');
    const note = $('#subsNote');
    const web = $('#subsWeb');
    let ids = [], bitOf = {}, chosen = new Set(), chips = [], allChip = null;

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
      let saved = null;
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
      note.classList.toggle('on', list.some((id) => URGENT[id]));
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
      bitOf = {};
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
        if (URGENT[t.id]) {
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
        if (!left && !document.hidden) window.open(link, '_blank', 'noopener,noreferrer');
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
      if (window.dispatchEvent(ev)) window.location.href = app;
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

  /* =====================================================================
     LOAD
     ===================================================================== */
  const errBox = $('#loadErr');
  /* data/reports.json, or the bundled sample when it is missing/unreachable (fresh install).
     A file that exists but is broken is an error, not a reason to show sample data. */
  async function fetchReports() {
    let res = null;
    try { res = await fetch('data/reports.json', { cache: 'no-cache' }); } catch (e) { res = null; }
    if (!res || !res.ok) res = await fetch('data/reports.sample.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }
  async function load() {
    errBox.hidden = true;
    try {
      const raw = await fetchReports();
      if (!raw || typeof raw !== 'object') throw new Error('bad data');
      const gen = validDate(raw.generated_at);
      topics = normalize(raw);
      if (!topics.length) throw new Error('no topics');
      if (gen) {
        $('#updatedText').textContent = 'Updated ' + localStamp(gen, true);
        $('#updated').hidden = false;
      }
      loadSeen(gen ? fmtDay.format(gen) : '');
      buildBubbles();
      Deck.build();
      buildList();
      Subs.build(topics);
      clampAll();
      if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(clampAll).catch(() => {});
    } catch (err) {
      errBox.hidden = false;
      doc.querySelectorAll('.card.skeleton .shimmer').forEach((s) => s.remove());
    }
  }
  $('#retryBtn').addEventListener('click', load);
  load();
})();
