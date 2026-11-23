/* MyBuddy · data + rendering.
   Data text only ever reaches the DOM through textContent / text nodes, and links must be http(s).
   Animation lives in motion.js; this file works on its own. */
(() => {
  "use strict";

  const root = document.documentElement;
  const $ = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));
  const RM = window.matchMedia("(prefers-reduced-motion: reduce)");
  const SVGNS = "http://www.w3.org/2000/svg";
  const DATA_URL = "data/reports.json";
  const SAMPLE_URL = "data/reports.sample.json";
  const IST_MIN = 330;
  const MB = (window.MyBuddy = window.MyBuddy || {});

  // Per-topic look. Tints are our own constants (never data).
  const META = {
    ai_news: { icon: "sparkles", tint: "#6EA8FE", deep: "#2563EB", short: "AI news" },
    security: { icon: "shield", tint: "#F87171", deep: "#DC2626", short: "Security" },
    github_trending: { icon: "star", tint: "#FBBF24", deep: "#B45309", short: "GitHub" },
    deep_dive: { icon: "layers", tint: "#A78BFA", deep: "#7C3AED", short: "Deep dive" },
    research_papers: { icon: "file", tint: "#2DD4BF", deep: "#0F766E", short: "Papers" },
    startups: { icon: "rocket", tint: "#F472B6", deep: "#DB2777", short: "Launches" },
    ai_models: { icon: "cpu", tint: "#4ADE80", deep: "#15803D", short: "Models" },
  };
  const CYCLE = [["#6EA8FE", "#2563EB"], ["#A78BFA", "#7C3AED"], ["#F472B6", "#DB2777"], ["#F87171", "#DC2626"], ["#FBBF24", "#B45309"], ["#4ADE80", "#15803D"], ["#2DD4BF", "#0F766E"]];
  const DEEP = { 1: "RAG", 2: "Agents", 3: "Training & fine-tuning", 4: "Audio & video", 5: "Evaluation" };
  const SLOTS = [
    { id: "ai_news", time: "06:30", name: "AI news" },
    { id: "security", time: "07:00", name: "Cybersecurity alerts" },
    { id: "github_trending", time: "07:15", name: "Trending GitHub repos" },
    { id: "deep_dive", time: "07:30", name: "Deep dive", weekdays: true },
    { id: "research_papers", time: "07:45", name: "AI research papers" },
    { id: "startups", time: "08:00", name: "Startup & product launches" },
    { id: "ai_models", time: "08:15", name: "Open-source AI models" },
  ];
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const DAYS_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /* ---------- helpers ---------- */
  function el(tag, opts = {}, kids = []) {
    const n = document.createElement(tag);
    if (opts.cls) n.className = opts.cls;
    if (opts.text != null) n.textContent = String(opts.text);
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) if (v != null) n.setAttribute(k, String(v));
    for (const k of kids) if (k) n.append(k);
    return n;
  }
  function icon(name, cls = "i") {
    const s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("class", cls);
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("focusable", "false");
    const u = document.createElementNS(SVGNS, "use");
    u.setAttribute("href", `#i-${name}`);
    s.append(u);
    return s;
  }
  const sr = (text) => el("span", { cls: "sr-only", text });
  const newTab = () => sr(" (opens in a new tab)");
  const str = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v).trim());
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const pad = (n) => String(n).padStart(2, "0");
  const say = (msg) => { const s = $("#srStatus"); if (s) s.textContent = msg; };
  const ext = (url) => ({ href: url, target: "_blank", rel: "noopener noreferrer" });

  function safeUrl(u) {
    try {
      const url = new URL(str(u));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }
  function parseDate(s) {
    if (!s) return null;
    const d = new Date(str(s));
    return Number.isNaN(d.getTime()) ? null : d;
  }
  function ist(d) {
    const x = new Date(d.getTime() + IST_MIN * 60000);
    return { h: x.getUTCHours(), m: x.getUTCMinutes(), day: x.getUTCDay(), date: x.getUTCDate(), mon: x.getUTCMonth(), y: x.getUTCFullYear() };
  }
  const hhmm = (p) => `${pad(p.h)}:${pad(p.m)}`;
  const sameDay = (a, b) => a.y === b.y && a.mon === b.mon && a.date === b.date;
  function istNow() { const p = ist(new Date()); p.min = p.h * 60 + p.m; return p; }
  const toMin = (s) => { const [h, m] = s.split(":").map(Number); return h * 60 + m; };
  const fmtFull = (d) => d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  function relTime(d) {
    const diff = Date.now() - d.getTime();
    if (diff < -5 * 60000) return fmtFull(d);
    const mins = Math.round(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} h ago`;
    const days = Math.round(hrs / 24);
    return days === 1 ? "yesterday" : `${days} days ago`;
  }
  function fmtWait(mins) {
    if (mins < 1) return "now";
    const h = Math.floor(mins / 60), m = mins % 60;
    return h ? `${h}h ${pad(m)}m` : `${m}m`;
  }
  const relEl = (d) => el("time", { text: relTime(d), attrs: { datetime: d.toISOString(), title: fmtFull(d), "data-rel": "" } });
  function inView(node) {
    if (!node || node.closest("[hidden]")) return false;
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  }
  function tintify(node, t) {
    node.classList.add("tinted");
    node.style.setProperty("--tint", t.tint);
    node.style.setProperty("--tint-d", t.deep);
  }

  /* ---------- data ---------- */
  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.topics)) throw new Error("Unexpected data shape");
    return data;
  }
  async function loadReports(allowSample) {
    try {
      return { data: await getJSON(DATA_URL), fallback: false };
    } catch (err) {
      if (!allowSample) return { data: null, fallback: true };
      console.warn("MyBuddy: reports.json unavailable, using sample data.", err);
      try { return { data: await getJSON(SAMPLE_URL), fallback: true }; } catch (err2) {
        console.warn("MyBuddy: sample data unavailable too.", err2);
        return { data: null, fallback: true };
      }
    }
  }

  const isItem = (it) => isObj(it) && !!(str(it.title) || str(it.url));
  function normTopics(list) {
    const topics = list.filter(isObj);
    const seen = new Set(topics.map((t) => str(t.id)).filter(Boolean));
    const claimed = new Set();
    return topics.map((t, idx) => {
      const base = str(t.id) || `topic-${idx}`;
      let id = base;
      if (claimed.has(id) || (!str(t.id) && seen.has(id))) for (let n = idx; seen.has(id); n++) id = `${base}-${n}`;
      seen.add(id);
      claimed.add(id);
      const report = isObj(t.report) ? t.report : null;
      const items = report && Array.isArray(report.items) ? report.items.filter(isItem) : [];
      const meta = META[id] || {};
      const [tint, deep] = meta.tint ? [meta.tint, meta.deep] : CYCLE[idx % CYCLE.length];
      const schedule = str(t.schedule);
      const tm = /(\d{1,2}):(\d{2})/.exec(schedule);
      const title = str(t.title) || "Untitled topic";
      return {
        id, idx, report, items, title, tint, deep, schedule,
        short: meta.short || title,
        icon: meta.icon || "news",
        time: tm ? `${pad(tm[1])}:${tm[2]}` : "",
        description: str(t.description),
        created: report ? parseDate(report.created_at) : null,
        weekdays: id === "deep_dive" || /mon\s*[–-]\s*fri|weekdays/i.test(schedule),
      };
    });
  }

  /* ---------- shared bits ---------- */
  function favLetter(s) {
    const m = /[A-Za-z0-9]/.exec(str(s).replace(/^the\s+/i, ""));
    return m ? m[0].toUpperCase() : "•";
  }
  // Cut at a word boundary so nothing ends mid-word ("Combatting adversarial…", never "di…").
  function wordCut(text, max) {
    if (text.length <= max) return text;
    const cut = text.slice(0, max + 1);
    const sp = cut.lastIndexOf(" ");
    return `${(sp > max * 0.6 ? cut.slice(0, sp) : text.slice(0, max)).replace(/[\s,;:.\-–—]+$/, "")}…`;
  }
  // Data hygiene: drop a trailing " - Publisher" that repeats the source; Google News items get the
  // publisher as their source instead of the generic feed name.
  function cleanItem(item) {
    let title = str(item.title) || "Untitled";
    let source = str(item.source);
    const m = /\s+[-–—|]\s+([^-–—|]{2,60})$/.exec(title);
    if (m) {
      const pub = m[1].trim(), p = pub.toLowerCase(), sl = source.toLowerCase();
      if (sl && (sl === p || sl.startsWith(p) || p.startsWith(sl))) title = title.slice(0, m.index);
      else if (/^google news/i.test(source)) { title = title.slice(0, m.index); source = pub; }
    }
    return { title, source, url: safeUrl(item.url), blurb: str(item.blurb), published: parseDate(item.published) };
  }
  const FRESH_DAYS = 14;
  const fresh = (d) => !!d && Date.now() - d.getTime() <= FRESH_DAYS * 86400000;
  const sharedSource = (items) => {
    const srcs = items.map((it) => cleanItem(it).source);
    return items.length > 1 && srcs[0] && srcs.every((s) => s === srcs[0]) ? srcs[0] : "";
  };
  function twoLengths(cls, text, shortMax, longMax) {
    const full = wordCut(text, longMax), short = wordCut(text, shortMax);
    if (full === short) return el("span", { cls, text: full });
    return el("span", { cls }, [el("span", { cls: "short", text: short }), el("span", { cls: "long", text: full })]);
  }
  function itemLink(item) {
    const it = cleanItem(item);
    const kids = [
      el("span", { cls: "fav", text: favLetter(it.source || it.title), attrs: { "aria-hidden": "true" } }),
      el("span", { cls: "it-body" }, [
        twoLengths("it-title", it.title, 44, 84),
        it.source ? el("span", { cls: "it-src", text: it.source }) : null,
        it.blurb ? el("span", { cls: "it-blurb", text: it.blurb }) : null,
      ]),
    ];
    if (!it.url) return el("div", { cls: "it" }, kids);
    kids.push(icon("ext", "i ext"), newTab());
    return el("a", { cls: "it", attrs: { ...ext(it.url), title: it.title } }, kids);
  }
  function cardHead(t, idPrefix, extra) {
    const when = t.time ? `${t.weekdays ? "Mon–Fri · " : ""}${t.time} IST` : t.schedule;
    const meta = el("p", { cls: "card-meta" }, [el("span", { text: when })]);
    if (t.created) meta.append(el("span", { cls: "sep", text: "·", attrs: { "aria-hidden": "true" } }), relEl(t.created));
    const head = el("header", { cls: "card-head" }, [
      el("span", { cls: "tile", attrs: { "aria-hidden": "true" } }, [icon(t.icon)]),
      el("div", { cls: "card-id" }, [el("h3", { cls: "card-title", text: t.title, attrs: { id: `${idPrefix}-${t.idx}` } }), meta]),
    ]);
    if (extra) head.append(extra);
    return head;
  }
  const liveBadge = (text) => el("span", { cls: "badge" }, [el("span", { cls: "live-dot", attrs: { "aria-hidden": "true" } }), document.createTextNode(text)]);
  const plainBadge = (text) => el("span", { cls: "badge" }, [document.createTextNode(text)]);
  // "Live" only while a brief is under 3 hours old; otherwise say when it is from.
  function freshBadge(d) {
    if (!d) return null;
    if (Date.now() - d.getTime() < 3 * 3600000) return liveBadge("Live");
    const p = ist(d);
    return plainBadge(sameDay(p, istNow()) ? `Today ${hhmm(p)}` : `${p.date} ${MONTHS[p.mon]}`);
  }
  const cloneTpl = (id) => { const tpl = $(id); return tpl ? tpl.content.firstElementChild.cloneNode(true) : null; };

  /* ---------- schedule (IST) ---------- */
  const runsOn = (slot, day) => !slot.weekdays || (day >= 1 && day <= 5);
  function schedule() {
    const now = istNow();
    const left = SLOTS.some((s) => runsOn(s, now.day) && toMin(s.time) > now.min);
    let next = null;
    for (let off = 0; off < 8 && !next; off++) {
      const day = (now.day + off) % 7;
      for (const s of SLOTS) {
        if (!runsOn(s, day)) continue;
        const tm = toMin(s.time);
        if (off === 0 && tm <= now.min) continue;
        next = { slot: s, off, day, wait: off * 1440 + tm - now.min };
        break;
      }
    }
    return { now, next, left, viewDay: left ? now.day : (now.day + 1) % 7 };
  }
  function whenText(n) {
    const day = n.off === 0 ? "" : n.off === 1 ? "Tomorrow " : `${DAYS_SHORT[n.day]} `;
    return `${n.slot.name} · ${day}${n.slot.time} IST`;
  }
  function emptyText(t) {
    const now = istNow();
    const time = t.time;
    if (t.weekdays) {
      const weekend = now.day === 0 || now.day === 6 || (now.day === 5 && time && now.min >= toMin(time));
      if (weekend) return { title: `Back Monday${time ? ` ${time}` : ""}`, sub: `Monday's deep dive: ${DEEP[1]}.` };
      const topic = DEEP[time && now.min >= toMin(time) ? (now.day % 7) + 1 : now.day];
      return { title: time ? `First brief at ${time} IST` : "First brief soon", sub: topic ? `Next up: ${topic}.` : t.description };
    }
    return { title: time ? `First brief at ${time} IST` : "First brief soon", sub: t.description };
  }

  /* ---------- state ---------- */
  const bento = $("#bento");
  const tabs = $("#tabs");
  const tabInd = $("#tabInd");
  const topicsById = new Map();
  let topics = [];
  let featured = null;
  let filter = "all";
  let currentStamp = null;

  /* ---------- hero ---------- */
  function renderLive(data, fallback) {
    const lt = $("#liveText");
    if (!lt) return;
    lt.replaceChildren();
    const g = data ? parseDate(data.generated_at) : null;
    if (!data) { lt.append("Brief unavailable right now"); return; }
    if (fallback || data.sample === true) { lt.append("Preview · ", el("b", { text: "sample data" })); return; }
    if (!g) { lt.append("Live"); return; }
    const p = ist(g);
    const when = sameDay(p, istNow()) ? hhmm(p) : `${p.date} ${MONTHS[p.mon]}, ${hhmm(p)}`;
    lt.append("Live · updated ", el("b", { text: `${when} IST` }));
  }

  function renderHero(list) {
    const main = $("#hvMain");
    const pick = list.find((t) => t.id === "ai_news" && t.report) || featured || list.find((t) => t.report);
    if (main) {
      const beam = $(".beam", main) || el("span", { cls: "beam", attrs: { "aria-hidden": "true" } });
      main.replaceChildren(beam);
      if (!pick) {
        main.append(el("p", { cls: "hm-hl", text: "Today's brief is on its way." }));
        main.removeAttribute("data-topic");
      } else {
        tintify(main, pick);
        main.dataset.topic = pick.id;
        main.setAttribute("aria-labelledby", `hv-${pick.idx}`);
        main.removeAttribute("aria-label");
        main.append(cardHead(pick, "hv", freshBadge(pick.created)));
        if (str(pick.report.headline)) main.append(el("p", { cls: "hm-hl", text: wordCut(str(pick.report.headline), 120) }));
        const shared = sharedSource(pick.items);
        if (pick.items.length) main.append(el("ul", { cls: `items${shared ? " one-src" : ""}` }, pick.items.slice(0, 2).map((it) => el("li", {}, [itemLink(it)]))));
        const open = el("button", { cls: "open-btn", attrs: { type: "button", "aria-haspopup": "dialog" } }, [
          el("span", { text: "Full report" }), sr(`, ${pick.title}`), icon("arrow"),
        ]);
        open.addEventListener("click", () => openReport(pick.id, open, main));
        main.append(el("div", { cls: "hm-foot" }, [el("span", { cls: "sent" }, [icon("check"), document.createTextNode("Sent to Telegram")]), open]));
        main.onclick = (e) => { if (!e.target.closest("a, button")) openReport(pick.id, open, main); };
      }
    }
    // Notification-style card: a different topic's headline.
    const note = list.find((t) => t.id === "security" && t.report && t !== pick) || list.find((t) => t.report && t !== pick);
    const nt = $("#hvNoteTitle"), nb = $("#hvNoteBody"), ntime = $("#hvNoteTime");
    if (nt && nb) {
      if (note) {
        const tile = el("span", { cls: "tile tinted", attrs: { "aria-hidden": "true" } }, [icon(note.icon)]);
        tintify(tile, note);
        nt.replaceChildren(tile, document.createTextNode(note.title));
        nb.textContent = str(note.report.headline) || str(note.report.summary);
        if (ntime && note.created) ntime.textContent = hhmm(ist(note.created));
      } else {
        nt.textContent = "MyBuddy";
        nb.textContent = "Your first brief arrives at 06:30 IST.";
      }
    }
    // Readiness card: how many of today's briefs are in.
    const dots = $("#hvDots");
    if (dots) {
      dots.replaceChildren(...list.map((t) => { const i = el("i", { cls: t.report ? "" : "off" }); tintify(i, t); return i; }));
      const ready = $("#hvReady");
      if (ready) ready.textContent = `${list.filter((t) => t.report).length}/${list.length}`;
    }
    // Hero bear peeks over the main card (and tucks away when the card slides under the nav).
    const hb = $("#hvBear");
    if (hb && !hb.firstChild) { const b = cloneTpl("#bearTpl"); if (b) hb.append(b); }
  }
  function initHeroBear() {
    const hb = $("#hvBear"), main = $("#hvMain"), pill = $(".nav-pill");
    if (!hb || !main || !pill) return;
    let raf = 0;
    const check = () => {
      raf = 0;
      const top = main.getBoundingClientRect().top;
      hb.classList.toggle("tuck", top - 56 < pill.getBoundingClientRect().bottom + 6);
    };
    addEventListener("scroll", () => { if (!raf) raf = requestAnimationFrame(check); }, { passive: true });
    addEventListener("resize", check);
    check();
  }

  function renderStats(list) {
    const n = list.reduce((a, t) => a + t.items.length, 0);
    const s = $("#statStories");
    if (s) { s.textContent = String(n); s.dataset.to = String(n); }
  }

  /* ---------- marquee ---------- */
  function renderMarquee(list) {
    const track = $("#mqTrack");
    if (!track) return;
    const withItems = list.filter((t) => t.items.length);
    const picks = [];
    for (let r = 0; r < 3; r++) for (const t of withItems) if (t.items[r] && picks.length < 18) picks.push([t, cleanItem(t.items[r])]);
    const band = track.closest(".marquee-band");
    if (!picks.length) { if (band) band.hidden = true; return; }
    if (band) band.hidden = false;
    const make = (dup) => {
      const ul = el("ul", { cls: "mq-list" });
      if (dup) ul.setAttribute("aria-hidden", "true");
      for (const [t, it] of picks) {
        const kids = [el("span", { cls: "mq-dot", attrs: { "aria-hidden": "true" } }), el("span", { cls: "mq-t", text: wordCut(it.title, 64) }), el("span", { cls: "mq-s", text: it.source || t.short })];
        const a = it.url ? el("a", { attrs: { ...ext(it.url), title: it.title } }, kids) : el("span", { cls: "mq-a" }, kids);
        if (it.url && dup) a.tabIndex = -1;
        if (it.url && !dup) a.append(newTab());
        const li = el("li", { cls: "mq-item" }, [a]);
        tintify(li, t);
        ul.append(li);
      }
      return ul;
    };
    track.replaceChildren(make(false), make(true));
    requestAnimationFrame(() => {
      const w = track.scrollWidth / 2;
      track.style.setProperty("--mq-dur", `${Math.max(40, Math.round(w / 42))}s`);
    });
  }

  /* ---------- bento ---------- */
  function renderCell(t, isFeatured) {
    const cell = el("div", { cls: "cell", attrs: { "data-topic": t.id } });
    tintify(cell, t);
    const card = el("article", { cls: "card", attrs: { "aria-labelledby": `t-${t.idx}` } });
    cell.append(card);
    if (!t.report) {
      // Slim strip: the bear naps until the topic's next run.
      cell.classList.add("strip");
      card.classList.add("is-empty", "is-strip");
      card.append(cardHead(t, "t"));
      const e = emptyText(t);
      const bear = el("div", { cls: "sleepy-wrap", attrs: { "aria-hidden": "true" } }, [cloneTpl("#sleepyTpl"), el("span", { cls: "zz", text: "z" }), el("span", { cls: "zz z2", text: "z" })]);
      card.append(el("div", { cls: "empty" }, [bear, el("div", {}, [el("p", { cls: "empty-title", text: e.title }), e.sub ? el("p", { cls: "empty-sub", text: e.sub }) : null])]));
      return cell;
    }
    card.classList.add("has-report");
    if (isFeatured) card.append(el("span", { cls: "beam", attrs: { "aria-hidden": "true" } }));
    card.append(cardHead(t, "t", isFeatured ? liveBadge("Latest") : null));
    const hl = str(t.report.headline), sum = str(t.report.summary);
    if (hl) card.append(el("p", { cls: "card-hl", text: wordCut(hl, 120) }));
    if (sum) card.append(twoLengths("card-sum", sum, 120, 400));
    const shared = sharedSource(t.items);
    if (shared) card.append(el("span", { cls: "it-src src-once", text: shared }));
    if (t.items.length) card.append(el("ul", { cls: `items${shared ? " one-src" : ""}`, attrs: { "aria-label": `Top stories, ${t.title}` } }, t.items.slice(0, 6).map((it) => el("li", {}, [itemLink(it)]))));
    else card.append(el("p", { cls: "empty-sub", text: "Nothing new made the cut this time." }));
    const open = el("button", { cls: "open-btn", attrs: { type: "button", "aria-haspopup": "dialog" } }, [
      el("span", { text: "Open full report" }), sr(`, ${t.title}`), icon("arrow"),
    ]);
    open.addEventListener("click", () => openReport(t.id, open, card));
    card.append(el("footer", { cls: "card-foot" }, [el("span", { cls: "more" }), open]));
    card.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && sel.containsNode(card, true)) return;
      openReport(t.id, open, card);
    });
    return cell;
  }

  // Card modes: "small" (headline + 2 stories), "big" (featured: + short summary, 3 stories),
  // "focus" (filtered to one topic: full summary, every story with its blurb).
  const PHONE = window.matchMedia("(max-width: 759px)");
  function setMode(cell, mode) {
    const card = $(".card", cell);
    if (!card || card.classList.contains("is-empty")) return;
    if (mode === "big" && PHONE.matches) mode = "small"; // phones keep the featured card compact
    card.classList.toggle("big", mode !== "small");
    card.classList.toggle("focus", mode === "focus");
    const t = topicsById.get(cell.dataset.topic);
    const more = $(".more", card);
    if (!t || !more) return;
    const small = cell.classList.contains("r1") ? 3 : 2; // cards beside the featured one have room for a third story
    const shown = Math.min(t.items.length, mode === "focus" ? 6 : mode === "big" ? 3 : small);
    const rest = t.items.length - shown;
    more.textContent = rest > 0 ? `+${rest} more` : `${t.items.length} ${t.items.length === 1 ? "story" : "stories"}`;
  }
  const modeFor = (cell) => (filter !== "all" ? (cell.hidden ? "small" : "focus") : cell.classList.contains("s-f") ? "big" : "small");

  // Rows of 3–4 cards that always fill the 12 columns (no ragged holes).
  function rowSizes(n) {
    const rows = [];
    while (n > 0) {
      const take = n === 5 ? 3 : n >= 4 ? 4 : n;
      rows.push(take);
      n -= take;
    }
    return rows;
  }
  function renderBento(list) {
    const reports = list.filter((t) => t.report);
    const empties = list.filter((t) => !t.report);
    const ordered = featured ? [featured, ...reports.filter((t) => t !== featured)] : reports;
    const cells = ordered.map((t) => renderCell(t, t === featured));
    const spanFor = { 1: "s-12", 2: "s-6", 3: "s-4", 4: "s-3" };
    let rest = cells;
    if (featured && cells.length >= 3) {
      cells[0].classList.add("s-f");
      cells[1].classList.add("s-3", "r1");
      cells[2].classList.add("s-3", "r1");
      rest = cells.slice(3);
    } else if (featured && cells.length) {
      cells.forEach((c) => c.classList.add(spanFor[cells.length]));
      rest = [];
    }
    let i = 0;
    for (const size of rowSizes(rest.length)) {
      rest.slice(i, i + size).forEach((c) => c.classList.add(spanFor[size]));
      i += size;
    }
    // Tablet (2 columns): the featured card is full width; an odd one out goes full width too.
    if ((cells.length - (featured ? 1 : 0)) % 2 === 1) cells[cells.length - 1].classList.add("wide-last");
    const strips = empties.map((t) => renderCell(t, false));
    bento.replaceChildren(...cells, ...strips);
    if (!cells.length && !strips.length) bento.replaceChildren(el("p", { cls: "notice", text: "No topics are set up yet." }));
  }

  /* ---------- tabs + filter ---------- */
  function renderTabs(list) {
    tabs.replaceChildren(tabInd);
    const make = (id, label, t) => {
      const b = el("button", { cls: "tab", attrs: { type: "button", "aria-pressed": String(id === filter), "data-filter": id } });
      if (t) { tintify(b, t); b.append(el("span", { cls: "tdot", attrs: { "aria-hidden": "true" } })); }
      b.append(document.createTextNode(label));
      b.addEventListener("click", () => applyFilter(id, true));
      return b;
    };
    tabs.append(make("all", "All"), ...list.map((t) => make(t.id, t.short, t)));
    tabInd.style.transition = "none";
    moveIndicator();
    requestAnimationFrame(() => requestAnimationFrame(() => { tabInd.style.transition = ""; }));
  }
  function moveIndicator() {
    const cur = $('.tab[aria-pressed="true"]', tabs);
    if (!cur) return;
    tabInd.style.setProperty("--x", `${cur.offsetLeft}px`);
    tabInd.style.setProperty("--w", `${cur.offsetWidth}px`);
  }
  // Layout changed: let the motion layer re-measure (ScrollTrigger pins etc.) once the DOM is final.
  const layoutChanged = () => document.dispatchEvent(new CustomEvent("mybuddy:layout"));
  function applyFilter(id, animate) {
    if (id !== "all" && !topicsById.has(id)) id = "all";
    const cells = $$(".cell", bento);
    const run = () => {
      filter = id;
      $$(".tab", tabs).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === id)));
      cells.forEach((c) => { c.hidden = !(id === "all" || c.dataset.topic === id); });
      bento.classList.toggle("is-filtered", id !== "all");
      cells.forEach((c) => setMode(c, modeFor(c)));
      moveIndicator();
    };
    if (held) release(held);
    if (animate && document.startViewTransition && !RM.matches) {
      cells.forEach((c, i) => { c.style.viewTransitionName = `cell-${i}`; });
      const vt = document.startViewTransition(run);
      vt.updateCallbackDone.then(layoutChanged, layoutChanged);
      vt.finished.finally(() => { cells.forEach((c) => { c.style.viewTransitionName = ""; }); layoutChanged(); });
    } else {
      run();
      layoutChanged();
    }
    const cur = $('.tab[aria-pressed="true"]', tabs);
    if (cur && tabs.scrollWidth > tabs.clientWidth) tabs.scrollTo({ left: cur.offsetLeft - 24, behavior: RM.matches ? "auto" : "smooth" });
  }

  /* ---------- the bear holding a card ----------
     The bear (56 × ~46 px beyond the edge) must stay fully on screen and never cover the nav or the tab bar.
     Try the top edge, then the bottom edge; otherwise it stays hidden. */
  const navPill = $(".nav-pill");
  let held = null;
  let placeFrame = 0;
  const overlaps = (a, b) => a.x1 < b.right && a.x2 > b.left && a.y1 < b.bottom && a.y2 > b.top;
  function placeBear(cell) {
    const card = $(".card", cell);
    if (!card) return;
    const r = card.getBoundingClientRect();
    const avoid = [navPill, tabs].filter((n) => n && n.offsetParent).map((n) => n.getBoundingClientRect());
    const x2 = r.right - 30 + 4, x1 = x2 - 56 - 8;
    const spots = {
      top: { x1, x2, y1: r.top - 48, y2: r.top + 6 },
      bottom: { x1, x2, y1: r.bottom - 6, y2: r.bottom + 48 },
    };
    const fits = (k) => { const s = spots[k]; return s.y1 >= 4 && s.y2 <= innerHeight - 4 && !avoid.some((a) => overlaps(s, a)); };
    const cur = cell.dataset.bear;
    let side = cur === "top" || cur === "bottom" ? (fits(cur) ? cur : null) : null;
    if (!side) side = fits("top") ? "top" : fits("bottom") ? "bottom" : "none";
    cell.dataset.bear = side;
  }
  function hold(cell) {
    if (held === cell) return;
    if (held) release(held);
    if (!cell || !$(".card.has-report", cell)) return;
    if (!$(".bear", cell)) { const b = cloneTpl("#bearTpl"); if (b) cell.append(b); }
    held = cell;
    cell.dataset.bear = "";
    placeBear(cell);
    requestAnimationFrame(() => { if (held === cell) cell.classList.add("is-held"); });
  }
  function release(cell) {
    cell.classList.remove("is-held");
    if (held === cell) held = null;
  }
  function initBear() {
    bento.addEventListener("pointerover", (e) => {
      if (e.pointerType !== "mouse") return;
      const cell = e.target.closest(".cell");
      if (cell && bento.contains(cell)) hold(cell);
    });
    bento.addEventListener("pointerout", (e) => {
      if (e.pointerType !== "mouse" || !held) return;
      if (e.relatedTarget && held.contains(e.relatedTarget)) return;
      if (!held.contains(document.activeElement)) release(held);
    });
    bento.addEventListener("focusin", (e) => { const cell = e.target.closest(".cell"); if (cell) hold(cell); });
    bento.addEventListener("focusout", (e) => {
      if (held && !held.contains(e.relatedTarget) && !held.matches(":hover")) release(held);
    });
    const schedulePlace = () => {
      if (!held || placeFrame) return;
      placeFrame = requestAnimationFrame(() => { placeFrame = 0; if (held) placeBear(held); });
    };
    addEventListener("scroll", schedulePlace, { passive: true });
    addEventListener("resize", schedulePlace);
  }

  /* ---------- full report sheet ---------- */
  const sheet = $("#sheet");
  const panel = $("#sheetPanel");
  const sheetBody = $("#sheetBody");
  let sheetTrigger = null, sheetSource = null, closing = false, downOnBackdrop = false, pendingData = null;

  function titleWithIcon(title) {
    const cut = title.lastIndexOf(" ");
    const head = cut > 0 ? title.slice(0, cut + 1) : "";
    const tail = cut > 0 ? title.slice(cut + 1) : title;
    const glued = el("span", { attrs: { style: "white-space:nowrap" } }, [document.createTextNode(tail), icon("ext")]);
    return tail.length > 24 ? [document.createTextNode(title), icon("ext")] : [document.createTextNode(head), glued];
  }
  function sheetItem(item) {
    const it = cleanItem(item);
    const head = it.url ? el("a", { cls: "s-title", attrs: ext(it.url) }, [...titleWithIcon(it.title), newTab()]) : el("span", { cls: "s-title", text: it.title });
    const li = el("li", { cls: "s-item" }, [el("h4", { cls: "s-h" }, [head])]);
    const meta = el("p", { cls: "s-meta" });
    if (it.source) meta.append(el("span", { cls: "it-src", text: it.source }));
    if (fresh(it.published)) meta.append(relEl(it.published)); // older dates are noise, not news
    if (meta.childNodes.length) li.append(meta);
    if (it.blurb) li.append(el("p", { cls: "s-blurb", text: it.blurb }));
    return li;
  }
  function fillSheet(t) {
    panel.className = "sheet-panel";
    tintify(panel, t);
    $("#sheetTile").replaceChildren(icon(t.icon));
    $("#sheetTitle").textContent = t.title;
    const meta = $("#sheetMeta");
    meta.replaceChildren(document.createTextNode(t.schedule || ""));
    if (t.created) meta.append(" · ", relEl(t.created));
    const r = t.report;
    const parts = [];
    if (str(r.headline)) parts.push(el("p", { cls: "sheet-hl", text: str(r.headline) }));
    if (str(r.summary)) parts.push(el("p", { cls: "sheet-sum", text: str(r.summary) }));
    parts.push(el("h3", { cls: "sheet-sub", text: t.items.length ? `Sources · ${t.items.length}` : "No sources this time" }));
    if (t.items.length) parts.push(el("ol", { cls: "s-items" }, t.items.map(sheetItem)));
    parts.push(el("p", { cls: "sheet-note" }, [icon("check"), el("span", { text: "Written by a free AI model from these sources only. Every link opens the original." })]));
    sheetBody.replaceChildren(...parts);
    sheetBody.scrollTop = 0;
  }
  const canVT = () => !!document.startViewTransition && !RM.matches;
  let openedByKey = false;
  function showSheet() {
    sheet.showModal();
    root.classList.add("sheet-open");
    if (MB.lenis) MB.lenis.stop();
    $("#sheetClose").focus({ preventScroll: true, focusVisible: openedByKey });
  }
  addEventListener("keydown", () => { openedByKey = true; }, true);
  addEventListener("pointerdown", () => { openedByKey = false; }, true);
  function openReport(id, trigger, source) {
    const t = topicsById.get(id);
    if (!t || !t.report || !sheet || sheet.open) return;
    sheetTrigger = trigger || document.activeElement;
    if (held) release(held);
    fillSheet(t);
    closing = false;
    sheet.classList.remove("pop", "closing");
    if (canVT() && inView(source)) {
      sheetSource = source;
      source.style.viewTransitionName = "report";
      root.classList.add("vt-report");
      const vt = document.startViewTransition(() => {
        source.style.viewTransitionName = "";
        panel.style.viewTransitionName = "report";
        showSheet();
      });
      vt.finished.finally(() => { panel.style.viewTransitionName = ""; root.classList.remove("vt-report"); });
    } else {
      sheetSource = null;
      sheet.classList.add("pop");
      showSheet();
    }
  }
  function closeReport() {
    if (!sheet.open || closing) return;
    closing = true;
    const src = sheetSource && document.contains(sheetSource) && inView(sheetSource) ? sheetSource : null;
    if (src && canVT()) {
      panel.style.viewTransitionName = "report";
      root.classList.add("vt-report");
      const vt = document.startViewTransition(() => {
        panel.style.viewTransitionName = "";
        src.style.viewTransitionName = "report";
        sheet.close();
      });
      vt.finished.finally(() => { src.style.viewTransitionName = ""; root.classList.remove("vt-report"); });
    } else if (!RM.matches) {
      sheet.classList.add("closing");
      setTimeout(() => { if (sheet.open) sheet.close(); }, 240);
    } else sheet.close();
  }
  function initSheet() {
    if (!sheet) return;
    sheet.addEventListener("cancel", (e) => { e.preventDefault(); closeReport(); });
    sheet.addEventListener("pointerdown", (e) => { downOnBackdrop = e.target === sheet; });
    sheet.addEventListener("click", (e) => {
      if (e.target === sheet && downOnBackdrop) closeReport();
      downOnBackdrop = false;
    });
    $("#sheetClose").addEventListener("click", closeReport);
    sheet.addEventListener("close", () => {
      closing = false;
      sheet.classList.remove("closing", "pop");
      root.classList.remove("sheet-open");
      if (MB.lenis) MB.lenis.start();
      let t = sheetTrigger;
      sheetTrigger = null;
      sheetSource = null;
      if (t && !document.contains(t)) t = null;
      if (t) t.focus({ preventScroll: true });
      if (pendingData) { const d = pendingData; pendingData = null; renderAll(d, false, false); }
    });
  }

  /* ---------- morning timeline + countdowns ---------- */
  let tlRows = null;
  let nowRow = null;
  function buildTimeline() {
    const list = $("#tlList");
    if (!list) return;
    tlRows = SLOTS.map((s) => {
      const row = { slot: s, name: el("span", { text: s.name }), sub: el("small"), state: el("span", { cls: "slot-state" }) };
      row.btn = el("button", { cls: "slot-btn", attrs: { type: "button" } }, [
        el("span", { cls: "slot-time" }, [document.createTextNode(s.time), sr(" IST,")]),
        el("span", { cls: "slot-dot", attrs: { "aria-hidden": "true" } }),
        el("span", { cls: "slot-name" }, [row.name, row.sub]),
        row.state,
      ]);
      row.btn.addEventListener("click", () => showCard(s.id));
      row.li = el("li", { cls: "slot", attrs: { "data-topic": s.id } }, [row.btn]);
      const meta = META[s.id];
      if (meta) tintify(row.li, meta);
      list.append(row.li);
      return row;
    });
    nowRow = el("li", { cls: "tl-now", attrs: { "aria-hidden": "true" } }, [el("span", { cls: "now-pill" })]);
  }
  function layoutRail() {
    const track = $(".tl-track");
    if (!track || !tlRows) return;
    const dots = [...tlRows.map((r) => $(".slot-dot", r.li)), nowRow.isConnected ? $(".now-pill", nowRow) : null].filter(Boolean);
    const railTop = $(".tl-rail").getBoundingClientRect().top;
    const ys = dots.map((d) => { const r = d.getBoundingClientRect(); return r.top + r.height / 2 - railTop; });
    const rail = $(".tl-rail");
    track.style.top = `${Math.min(...ys)}px`;
    track.style.bottom = `${rail.offsetHeight - Math.max(...ys)}px`;
  }
  function tick() {
    const { now, next, left, viewDay } = schedule();
    // Countdown panel + hero card
    const wait = next ? fmtWait(next.wait) : "--";
    const what = next ? whenText(next) : "";
    const set = (id, v) => { const n = $(id); if (n && n.textContent !== v) n.textContent = v; };
    set("#cdBig", wait);
    set("#cdWhat", what);
    set("#cdClock", `It's ${hhmm(now)} in India · ${DAYS[now.day]}`);
    if (!tlRows) buildTimeline();
    if (!tlRows) return;
    // The list shows today. "Now" sits where the clock is: above 06:30 at night, between rows during
    // the morning, below 08:15 afterwards. The next run is highlighted (tomorrow's 06:30 in the evening).
    let firstAfter = null;
    tlRows.forEach((row) => {
      const s = row.slot;
      const runs = runsOn(s, now.day);
      const passed = toMin(s.time) <= now.min;
      const isNext = !!next && s === next.slot && (next.off === 0 || (!left && next.off === 1));
      if (!passed && !firstAfter) firstAfter = row;
      let state = "";
      if (isNext) state = `${next.off === 0 ? "Next" : "Tomorrow"} · ${fmtWait(next.wait)}`;
      else if (!runs) state = "Weekend off";
      else if (passed) state = "Done";
      if (row.state.textContent !== state) row.state.textContent = state;
      const subDay = isNext && next.off ? next.day : now.day;
      const sub = s.id === "deep_dive" && runsOn(s, subDay) && DEEP[subDay] ? DEEP[subDay] : "";
      if (row.sub.textContent !== sub) row.sub.textContent = sub;
      row.li.classList.toggle("done", runs && passed);
      row.li.classList.toggle("next", isNext);
      row.li.classList.toggle("off", !runs && !isNext);
      if (isNext) row.btn.setAttribute("aria-current", "time"); else row.btn.removeAttribute("aria-current");
    });
    $(".now-pill", nowRow).textContent = `Now · ${hhmm(now)}`;
    const list = $("#tlList");
    if (firstAfter) { if (nowRow.nextSibling !== firstAfter.li) firstAfter.li.before(nowRow); }
    else if (list && list.lastElementChild !== nowRow) list.append(nowRow);
    layoutRail();
  }
  function showCard(id) {
    if (filter !== "all" && filter !== id) applyFilter("all", false);
    const cell = $(`.cell[data-topic="${CSS.escape(id)}"]`, bento);
    if (!cell) return;
    const card = $(".card", cell);
    const go = MB.scrollToEl || ((node) => node.scrollIntoView({ behavior: RM.matches ? "auto" : "smooth", block: "center" }));
    go(cell);
    const focusTarget = $(".open-btn", cell);
    setTimeout(() => {
      card.classList.remove("flash");
      void card.offsetWidth;
      card.classList.add("flash");
      if (focusTarget) focusTarget.focus({ preventScroll: true });
    }, RM.matches ? 0 : 700);
  }

  /* ---------- theme ---------- */
  function initTheme() {
    const btn = $("#themeBtn");
    if (!btn) return;
    const os = window.matchMedia("(prefers-color-scheme: dark)");
    const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : os.matches || !window.matchMedia("(prefers-color-scheme: light)").matches);
    const label = () => {
      const text = isDark() ? "Switch to light theme" : "Switch to dark theme";
      btn.setAttribute("aria-label", text);
      btn.title = text;
    };
    label();
    btn.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      const apply = () => {
        root.dataset.theme = next;
        try { localStorage.setItem("mybuddy-theme", next); } catch { /* storage blocked */ }
        label();
      };
      if (!canVT()) { apply(); return; }
      const r = btn.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      root.classList.add("vt-theme");
      const vt = document.startViewTransition(apply);
      vt.ready.then(() => {
        root.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${end}px at ${x}px ${y}px)`] },
          { duration: 700, easing: "cubic-bezier(.77,0,.175,1)", pseudoElement: "::view-transition-new(root)" });
      }).catch(() => {});
      vt.finished.finally(() => root.classList.remove("vt-theme"));
    });
    if (os.addEventListener) os.addEventListener("change", label);
  }

  /* ---------- nav ---------- */
  function initNav() {
    const nav = $("#nav"), btn = $("#menuBtn"), links = $("#navLinks");
    if (!nav) return;
    const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 30);
    addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    if (!btn || !links) return;
    const setOpen = (o) => {
      nav.classList.toggle("open", o);
      btn.setAttribute("aria-expanded", String(o));
      btn.setAttribute("aria-label", o ? "Close menu" : "Open menu");
    };
    btn.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
    links.addEventListener("click", (e) => { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && nav.classList.contains("open")) { setOpen(false); btn.focus(); } });
    document.addEventListener("pointerdown", (e) => { if (nav.classList.contains("open") && !nav.contains(e.target)) setOpen(false); });
  }

  /* ---------- render pipeline ---------- */
  function renderError() {
    renderLive(null, true);
    bento.replaceChildren(el("div", { cls: "cell s-12" }, [el("div", { cls: "card is-empty" }, [
      el("div", { cls: "empty" }, [el("div", { cls: "sleepy-wrap", attrs: { "aria-hidden": "true" } }, [cloneTpl("#sleepyTpl")]),
        el("p", { cls: "empty-title", text: "Couldn't load today's brief" }), el("p", { cls: "empty-sub", text: "Please check back in a little while." })]),
    ])]));
    tabs.replaceChildren(tabInd);
    const band = $(".marquee-band");
    if (band) band.hidden = true;
    renderHero([]);
    bento.setAttribute("aria-busy", "false");
    say("Couldn't load today's brief.");
  }

  function renderAll(data, fallback, initial) {
    if (!data) { renderError(); return; }
    const notice = $("#notice");
    const text = fallback ? "Couldn't reach the latest brief, so this is sample data." : data.sample === true ? "These are sample reports. Real ones appear once MyBuddy runs." : "";
    if (notice) { notice.hidden = !text; notice.textContent = text; }
    topics = normTopics(data.topics);
    topicsById.clear();
    topics.forEach((t) => topicsById.set(t.id, t));
    featured = null;
    for (const t of topics) if (t.report && t.created && (!featured || t.created > featured.created)) featured = t;
    if (!featured) featured = topics.find((t) => t.report) || null;
    currentStamp = str(data.generated_at);
    if (held) release(held);
    renderLive(data, fallback);
    renderHero(topics);
    renderStats(topics);
    renderMarquee(topics);
    renderTabs(topics);
    renderBento(topics);
    applyFilter(filter, false);
    bento.setAttribute("aria-busy", "false");
    tick();
    say(initial ? `${topics.filter((t) => t.report).length} of ${topics.length} reports loaded.` : "Reports updated.");
    MB.rendered = true;
    document.dispatchEvent(new CustomEvent("mybuddy:render", { detail: { initial } }));
  }

  function updateTimes() {
    $$("time[data-rel]").forEach((t) => { const d = parseDate(t.getAttribute("datetime")); if (d) t.textContent = relTime(d); });
  }

  let lastFetch = 0, fetching = false;
  async function refresh(initial) {
    if (fetching) return;
    fetching = true;
    lastFetch = Date.now();
    try {
      const { data, fallback } = await loadReports(initial);
      if (initial) renderAll(data, fallback, true);
      else if (data && !fallback && str(data.generated_at) !== currentStamp) {
        if (sheet && sheet.open) pendingData = data;
        else renderAll(data, false, false);
      }
    } finally { fetching = false; }
  }

  // Re-land a #hash after content above it has rendered (unless the reader already moved).
  let moved = false;
  ["wheel", "touchstart", "keydown", "pointerdown"].forEach((t) => addEventListener(t, () => { moved = true; }, { once: true, passive: true }));
  document.addEventListener("mybuddy:render", (e) => {
    if (!e.detail.initial || moved || !location.hash) return;
    let target = null;
    try { target = document.querySelector(decodeURIComponent(location.hash)); } catch { /* not a selector */ }
    if (target) requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
  });

  if (PHONE.addEventListener) PHONE.addEventListener("change", () => { if (topics.length) $$(".cell", bento).forEach((c) => setMode(c, modeFor(c))); });
  MB.openReport = openReport;
  MB.showCard = showCard;
  MB.relayout = () => { moveIndicator(); layoutRail(); };

  initTheme();
  initNav();
  initSheet();
  initBear();
  initHeroBear();
  tick();
  refresh(true);
  addEventListener("resize", () => { moveIndicator(); layoutRail(); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { moveIndicator(); layoutRail(); });
  setInterval(() => { tick(); updateTimes(); }, 15000);
  setInterval(() => { if (document.visibilityState === "visible") refresh(false); }, 10 * 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    tick();
    updateTimes();
    if (Date.now() - lastFetch >= 60000) refresh(false);
  });
})();
