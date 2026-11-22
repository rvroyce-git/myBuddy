/* MyBuddy site: renders data/reports.json as widgets and runs the small interactive touches.
   All text from JSON goes into the DOM via textContent; links are limited to http(s). */
(() => {
  "use strict";

  const root = document.documentElement;
  root.classList.add("js");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const $ = (sel, ctx = document) => ctx.querySelector(sel);

  const DATA_URL = "data/reports.json";
  const SAMPLE_URL = "data/reports.sample.json";
  const IST_OFFSET_MIN = 330;

  const ACCENTS = { ai_news: "teal", security: "blue", github_trending: "sage", deep_dive: "lav", research_papers: "blue", startups: "sage", ai_models: "lav" };
  const ACCENT_CYCLE = ["teal", "blue", "sage", "lav"];
  const BUBBLES = { ai_news: "Fresh AI news!", security: "Patch time!", github_trending: "Rising stars!", deep_dive: "Let's go deep!", research_papers: "New papers!", startups: "Shiny launches!", ai_models: "New models!" };
  const VISIBLE_ITEMS = 3; // compact cards show the top three; the dialog shows all
  const EMPTY_MSG = "No topics are set up yet. Please check back in a little while.";
  const ERROR_MSG = "We couldn't load the reports right now. Please check back in a little while.";

  // Schedule shown in the "morning" timeline (IST). Mirrors the topics in config.
  const DEEP_DIVE = { 1: "RAG", 2: "Agents", 3: "Training & fine-tuning", 4: "Audio & video", 5: "Evaluation" };
  const SLOTS = [
    { id: "ai_news", time: "06:30", name: "AI news", emoji: "🧠" },
    { id: "security", time: "07:00", name: "Cybersecurity", emoji: "🛡️" },
    { id: "github_trending", time: "07:15", name: "Trending repos", emoji: "⭐" },
    { id: "deep_dive", time: "07:30", name: "Deep dive", emoji: "🔬", weekdays: true },
    { id: "research_papers", time: "07:45", name: "Research papers", emoji: "📄" },
    { id: "startups", time: "08:00", name: "Startups & launches", emoji: "🚀" },
    { id: "ai_models", time: "08:15", name: "Open models", emoji: "🤖" },
  ];

  /* ---------- tiny DOM helper (text only, never HTML) ---------- */
  function el(tag, opts = {}, children = []) {
    const node = document.createElement(tag);
    if (opts.cls) node.className = opts.cls;
    if (opts.text != null) node.textContent = String(opts.text);
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) if (v != null) node.setAttribute(k, String(v));
    for (const c of children) if (c) node.append(c);
    return node;
  }
  const str = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v).trim());
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const sr = (text) => el("span", { cls: "sr-only", text });
  const byTopic = (id) => widgetsBox.querySelector(`.widget[data-topic="${CSS.escape(id)}"]`);
  // Short message for screen readers, only when the reports are (re)rendered.
  const say = (msg) => { const s = $("#srStatus"); if (s) s.textContent = msg; };

  function safeUrl(u) {
    try {
      const url = new URL(str(u));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }
  function parseDate(s) {
    const d = new Date(str(s));
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const fmtFull = (d) => d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  function relTime(d) {
    const diff = Date.now() - d.getTime();
    if (diff < -5 * 60000) return fmtFull(d); // clearly in the future: show the date, not "just now"
    const mins = Math.round(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} h ago`;
    const days = Math.round(hrs / 24);
    return days === 1 ? "yesterday" : `${days} days ago`;
  }
  function istNow() {
    const shifted = new Date(Date.now() + IST_OFFSET_MIN * 60000);
    return { minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(), day: shifted.getUTCDay(), shifted };
  }
  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };

  /* ---------- bear artwork ---------- */
  function drawBears() {
    const tpl = $("#peekBear");
    const face = $('[data-bear="face"]');
    if (tpl && face) face.append(tpl.content.querySelector(".peek-head").cloneNode(true));
  }

  /* ---------- theme toggle ---------- */
  function initTheme() {
    const btn = $("#themeBtn");
    if (!btn) return;
    let saved = null;
    try { saved = localStorage.getItem("mybuddy-theme"); } catch { /* storage blocked */ }
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
    const osDark = window.matchMedia("(prefers-color-scheme: dark)");
    const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : osDark.matches;
    const label = () => {
      const text = isDark() ? "Switch to light theme" : "Switch to dark theme";
      btn.setAttribute("aria-label", text);
      btn.title = text;
    };
    label();
    btn.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("mybuddy-theme", next); } catch { /* ignore */ }
      label();
    });
    if (osDark.addEventListener) osDark.addEventListener("change", label);
    else if (osDark.addListener) osDark.addListener(label);
  }

  /* ---------- data loading ---------- */
  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.topics)) throw new Error("Unexpected data shape");
    return data;
  }

  // Only the first load may fall back to the sample file; later refreshes just keep what's shown.
  async function loadReports(allowSample) {
    try {
      return { data: await getJSON(DATA_URL), fallback: false };
    } catch (err) {
      if (!allowSample) return { data: null, fallback: true };
      console.warn("MyBuddy: reports.json unavailable, using sample data.", err);
      try {
        return { data: await getJSON(SAMPLE_URL), fallback: true };
      } catch (err2) {
        console.warn("MyBuddy: sample data unavailable too.", err2);
        return { data: null, fallback: true };
      }
    }
  }

  // One normalised record per topic, so chips, widgets and the dialog agree on id and title.
  const isItem = (it) => isObj(it) && !!(str(it.title) || str(it.url));
  function normTopics(list) {
    const topics = list.filter(isObj);
    // Real ids are claimed first, so a generated or de-duplicated id can't take one that comes later.
    const seen = new Set(topics.map((t) => str(t.id)).filter(Boolean));
    const claimed = new Set();
    return topics.map((t, idx) => {
      const base = str(t.id) || `topic-${idx}`;
      let id = base;
      if (claimed.has(id) || (!str(t.id) && seen.has(id))) {
        for (let n = idx; seen.has(id); n++) id = `${base}-${n}`;
      }
      seen.add(id);
      claimed.add(id);
      const report = isObj(t.report) ? t.report : null;
      const items = report && Array.isArray(report.items) ? report.items.filter(isItem) : [];
      return {
        id, idx, report, items,
        title: str(t.title) || "Untitled topic",
        emoji: str(t.emoji),
        schedule: str(t.schedule),
        description: str(t.description),
        accent: ACCENTS[id] || ACCENT_CYCLE[idx % ACCENT_CYCLE.length],
      };
    });
  }

  /* ---------- widgets ---------- */
  const widgetsBox = $("#widgets");
  const chipsBox = $("#chips");
  const topicsById = new Map();
  let generatedAt = null;
  let currentStamp = null;

  function extIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("class", "ext");
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", "M6 3H3v10h10v-3M9 2h5v5M14 2 7.5 8.5");
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", "1.8");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    svg.append(p);
    return svg;
  }

  // Keep the external-link icon glued to the last word so it never wraps onto its own line.
  function titleWithIcon(title) {
    const cut = title.lastIndexOf(" ");
    const head = cut > 0 ? title.slice(0, cut + 1) : "";
    const tail = cut > 0 ? title.slice(cut + 1) : title;
    if (tail.length > 24) return [document.createTextNode(title), extIcon()]; // long unbroken word: let it wrap
    return [document.createTextNode(head), el("span", { cls: "nowrap" }, [document.createTextNode(tail), extIcon()])];
  }
  const newTab = () => sr(" (opens in a new tab)");
  const linkAttrs = (url, extra = {}) => ({ href: url, target: "_blank", rel: "noopener noreferrer", ...extra });

  // One-line item for the compact card: title (ellipsis) + small source chip.
  function renderMiniItem(item) {
    const title = str(item.title) || "Untitled";
    const url = safeUrl(item.url);
    const head = url
      ? el("a", { cls: "mini-title", attrs: linkAttrs(url, { title }) }, [document.createTextNode(title), newTab()])
      : el("span", { cls: "mini-title", text: title, attrs: { title } });
    const li = el("li", { cls: "mini" }, [head]);
    const source = str(item.source);
    if (source) li.append(el("span", { cls: "src", text: source, attrs: { title: source } }));
    return li;
  }

  // Full item for the report dialog: title link, source, time and blurb.
  function renderFullItem(item) {
    const title = str(item.title) || "Untitled";
    const url = safeUrl(item.url);
    const head = url
      ? el("a", { cls: "item-title", attrs: linkAttrs(url) }, [...titleWithIcon(title), newTab()])
      : el("span", { cls: "item-title", text: title });
    const li = el("li", { cls: "item" }, [el("h4", { cls: "item-h" }, [head])]);
    const meta = el("p", { cls: "item-meta" });
    const source = str(item.source);
    if (source) meta.append(el("span", { cls: "src", text: source }));
    const pub = parseDate(item.published);
    if (pub) meta.append(el("time", { cls: "pub", text: relTime(pub), attrs: { datetime: pub.toISOString(), title: fmtFull(pub) } }));
    if (meta.childNodes.length) li.append(meta);
    const blurb = str(item.blurb);
    if (blurb) li.append(el("p", { cls: "blurb", text: blurb }));
    return li;
  }

  // When the first report lands. Weekday-only topics skip to Monday over the weekend.
  function firstTime(schedule, id) {
    const m = /(\d{1,2}:\d{2})/.exec(schedule);
    if (!m) return null;
    const when = { time: `${m[1]} IST`, day: "" };
    if (id === "deep_dive" || /mon\s*[–-]\s*fri|weekdays/i.test(schedule)) {
      const { minutes, day } = istNow();
      if (day === 0 || day === 6 || (day === 5 && minutes >= toMin(m[1]))) when.day = "Monday";
    }
    return when;
  }

  function schedLine(schedule, report) {
    const sched = el("p", { cls: "sched" }, [el("span", { text: schedule || "Schedule not set" })]);
    const created = report ? parseDate(report.created_at) : null;
    if (created) {
      sched.append(el("time", { cls: "ago", text: relTime(created), attrs: { datetime: created.toISOString(), title: fmtFull(created), "data-rel": "" } }));
    }
    return sched;
  }

  function addPeek(host, bubbleText) {
    const peek = $("#peekBear").content.firstElementChild.cloneNode(true);
    $(".peek-bubble", peek).textContent = bubbleText;
    const back = $(".peek-back", peek), front = $(".peek-front", peek);
    back.setAttribute("aria-hidden", "true"); // they leave the aria-hidden wrapper, so hide each part
    front.setAttribute("aria-hidden", "true");
    host.prepend(back);
    host.append(front);
  }

  function renderWidget(t) {
    const { id, idx, title, schedule, report, items, accent } = t;
    const titleId = `w-${idx}-title`;

    const widget = el("div", { cls: `widget acc-${accent}`, attrs: { "data-topic": id } });
    widget.style.animationDelay = reduceMotion.matches ? "0s" : `${Math.min(idx, 8) * 70}ms`;

    // Focusable from script (timeline, focus restore) but not a tab stop: its link and button are.
    const card = el("article", { cls: `card${report ? " has-report" : ""}`, attrs: { tabindex: "-1", "aria-labelledby": titleId } });
    card.append(el("div", { cls: "card-top" }, [
      el("span", { cls: "emoji", text: t.emoji || "📰", attrs: { "aria-hidden": "true" } }),
      el("div", { cls: "card-id" }, [el("h3", { cls: "card-title", text: title, attrs: { id: titleId } }), schedLine(schedule, report)]),
    ]));

    if (!report) {
      const when = firstTime(schedule, id);
      const p = el("p", { cls: "empty-when" });
      if (when) p.append(`First report arrives ${when.day ? when.day + " " : ""}at `, el("strong", { text: when.time }), ".");
      else p.append("The first report is on its way.");
      const box = el("div", { cls: "empty" }, [el("span", { cls: "zz", text: "💤", attrs: { "aria-hidden": "true" } }), p]);
      if (t.description) box.append(el("p", { cls: "empty-desc", text: t.description }));
      card.append(box);
    } else {
      const headline = str(report.headline);
      const summary = str(report.summary);
      if (headline) card.append(el("p", { cls: "headline clamp", text: headline }));
      if (summary) card.append(el("p", { cls: "summary clamp", text: summary }));
      if (items.length) {
        card.append(el("ul", { cls: "mini-list", attrs: { "aria-label": `Top ${Math.min(items.length, VISIBLE_ITEMS)} of ${items.length} items` } }, items.slice(0, VISIBLE_ITEMS).map(renderMiniItem)));
      } else {
        card.append(el("p", { cls: "summary", text: "Nothing new made the cut this time." }));
      }
      const count = items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : "";
      // Accessible name keeps the visible words and adds the topic: "Open full report, AI news, 6 items".
      const btn = el("button", { cls: "open-btn", attrs: { type: "button", "aria-haspopup": "dialog" } }, [
        el("span", { text: "Open full report" }),
        sr(`, ${title}${count ? "," : ""} `),
        el("span", { cls: "count", text: count }),
        el("span", { cls: "arrow", text: "→", attrs: { "aria-hidden": "true" } }),
      ]);
      btn.addEventListener("click", () => openReport(id, btn));
      card.append(el("div", { cls: "card-foot" }, [btn]));
      // The whole card is a shortcut to the full report (links and buttons keep their own job,
      // and selecting text inside the card doesn't open it).
      card.addEventListener("click", (e) => {
        if (e.target.closest("a, button")) return;
        const sel = window.getSelection();
        if (sel && !sel.isCollapsed && sel.containsNode(card, true)) return;
        openReport(id, card);
      });
      card.addEventListener("keydown", (e) => {
        if (e.target === card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openReport(id, card); }
      });
    }

    widget.append(card);
    addPeek(widget, report ? (BUBBLES[id] || "For you!") : "Coming soon!");
    bindHold(widget);
    return widget;
  }

  function renderChips(topics) {
    chipsBox.replaceChildren();
    if (!topics.length) return;
    const make = (id, text, emoji) => {
      const b = el("button", { cls: "chip", attrs: { type: "button", "aria-pressed": id === "all" ? "true" : "false", "data-filter": id } }, [
        emoji ? el("span", { cls: "chip-emoji", text: emoji, attrs: { "aria-hidden": "true" } }) : null, document.createTextNode(text),
      ]);
      b.addEventListener("click", () => applyFilter(id));
      return b;
    };
    chipsBox.append(make("all", "All topics"));
    topics.forEach((t) => chipsBox.append(make(t.id, t.title, t.emoji)));
  }

  function applyFilter(id) {
    if (held) release(held);
    chipsBox.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.filter === id)));
    widgetsBox.querySelectorAll(".widget").forEach((w) => { w.hidden = !(id === "all" || w.dataset.topic === id); });
  }

  function renderReports(data, fallback, initial) {
    const notice = $("#notice");
    const hint = $("#hint") || $(".hint");
    if (!data) {
      chipsBox.replaceChildren();
      widgetsBox.replaceChildren(el("p", { cls: "empty", text: ERROR_MSG }));
      $("#updatedText").textContent = "Reports unavailable";
      if (hint) hint.hidden = true;
      say(ERROR_MSG);
      return;
    }
    const noticeText = fallback ? "We couldn't reach the latest reports just now, so here's sample data to show how it looks."
      : data.sample === true ? "These are sample reports. Real ones appear here once MyBuddy runs on its server." : "";
    if (notice) {
      notice.hidden = !noticeText; // unhide first so the status region announces the new text
      notice.textContent = noticeText;
    }
    const topics = normTopics(data.topics);
    generatedAt = parseDate(data.generated_at);
    currentStamp = str(data.generated_at);
    held = null;
    topicsById.clear();
    topics.forEach((t) => topicsById.set(t.id, t));
    renderChips(topics);
    if (topics.length) widgetsBox.replaceChildren(...topics.map(renderWidget));
    else widgetsBox.replaceChildren(el("p", { cls: "empty", text: EMPTY_MSG }));
    if (hint) hint.hidden = !topics.length;
    updateTimes();
    say(!topics.length ? EMPTY_MSG : initial ? `${topics.length} report${topics.length === 1 ? "" : "s"} loaded.` : "Reports updated.");
  }

  // Re-render after a refresh without losing the reader's place: wait for the dialog to close,
  // keep the active filter and put focus back on the same control in the new widget.
  let pendingData = null;
  let quietFocus = false; // focus moved by script, not the reader: don't scroll for it
  function rerender(data) {
    if (dlg && dlg.open) { pendingData = data; return; }
    const pressed = chipsBox.querySelector('.chip[aria-pressed="true"]');
    const filter = pressed ? pressed.dataset.filter : "all";
    const a = document.activeElement;
    const w = a && widgetsBox.contains(a) ? a.closest(".widget") : null;
    const snap = w ? { topic: w.dataset.topic, btn: a.matches(".open-btn"), link: [...w.querySelectorAll("a")].indexOf(a) }
      : a && chipsBox.contains(a) ? { chip: a.dataset.filter } : null;
    renderReports(data, false, false);
    applyFilter(filter === "all" || topicsById.has(filter) ? filter : "all");
    if (!snap) return;
    let target = null;
    if (snap.chip) target = chipsBox.querySelector(`.chip[data-filter="${CSS.escape(snap.chip)}"]`) || $(".chip", chipsBox);
    else {
      const nw = byTopic(snap.topic);
      if (nw) target = (snap.btn && $(".open-btn", nw)) || (snap.link >= 0 && nw.querySelectorAll("a")[snap.link]) || $(".card", nw);
      else target = $(".chip", chipsBox); // that topic is gone: land on "All topics"
    }
    if (target && !target.closest("[hidden]")) {
      quietFocus = true;
      target.focus({ preventScroll: true });
      quietFocus = false;
    }
  }

  function updateTimes() {
    const txt = $("#updatedText");
    if (generatedAt) {
      txt.textContent = `Updated ${relTime(generatedAt)}`;
      txt.parentElement.title = fmtFull(generatedAt);
    } else if (currentStamp !== null) {
      txt.textContent = "Update time unknown";
    }
    document.querySelectorAll("time[data-rel]").forEach((t) => {
      const d = parseDate(t.getAttribute("datetime"));
      if (d) t.textContent = relTime(d);
    });
  }

  /* ---------- the bear "holding" a widget ----------
     The bear peeks over the card's top edge. If there isn't room between the sticky header and the
     card (or the card's top is under the header), it holds the card up from the bottom edge instead. */
  const topbar = $(".topbar");
  const TUCK = 10; // px of the bear's head hidden behind the card edge (matches --tuck in CSS)
  let held = null;
  let placeFrame = 0;

  const headerBottom = () => Math.max(0, topbar.getBoundingClientRect().bottom);
  function bearRoom(host) {
    const back = $(".peek-back", host);
    return (back ? back.offsetHeight : 100) - TUCK + 10;
  }

  function placeBear(w) {
    const card = $(".card", w);
    if (!card) return;
    const r = card.getBoundingClientRect();
    const hb = headerBottom();
    const need = bearRoom(w);
    const lift = w.classList.contains("is-held") ? 0 : 8; // the held card is already lifted
    const topRoom = r.top - lift - hb;
    const topOK = topRoom - need >= 4;
    const bottomOK = r.bottom - lift + need <= window.innerHeight - 4;
    let below;
    if (w.classList.contains("below") && (bottomOK || !topOK)) below = true; // stay put unless the top now fits better
    else if (topOK) below = false;
    else if (bottomOK) below = true;
    else below = topRoom < need || (r.top - hb) < (window.innerHeight - r.bottom); // neither fits: never tuck the head under the header
    w.classList.toggle("below", below);
  }

  // If the card's top is hidden under the sticky header, nudge the page so the whole card shows.
  // The bear holds it from below, so it stays visible right under the header. Keyboard focus only.
  function revealCard(w) {
    if (held !== w) return;
    const r = $(".card", w).getBoundingClientRect();
    const hb = headerBottom();
    if (r.top < hb + 8) {
      w.classList.add("below");
      window.scrollBy({ top: r.top - hb - 16, behavior: reduceMotion.matches ? "auto" : "smooth" });
    }
  }
  const keyboardFocus = (t) => { try { return t.matches(":focus-visible"); } catch { return true; } };

  // `reveal`: only keyboard focus may scroll the page; hover and touch never move it.
  function hold(w, reveal = false) {
    if (held !== w) {
      if (held) release(held);
      held = w;
      w.classList.remove("below");
      placeBear(w);
      w.classList.add("is-held");
    }
    if (reveal) revealCard(w);
  }
  function release(w) {
    w.classList.remove("is-held");
    w.style.removeProperty("--rx");
    w.style.removeProperty("--ry");
    if (held === w) held = null;
  }
  function bindHold(w) {
    w.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") hold(w); });
    w.addEventListener("pointerleave", (e) => {
      if (e.pointerType !== "mouse") return;
      if (!w.contains(document.activeElement)) release(w);
      else { w.style.removeProperty("--rx"); w.style.removeProperty("--ry"); }
    });
    w.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse" || reduceMotion.matches || held !== w) return;
      const r = w.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      w.style.setProperty("--ry", `${(x * 4).toFixed(2)}deg`);
      w.style.setProperty("--rx", `${(-y * 3).toFixed(2)}deg`);
    });
    w.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") hold(w); });
    w.addEventListener("focusin", (e) => hold(w, !quietFocus && keyboardFocus(e.target)));
    w.addEventListener("focusout", (e) => {
      if (!w.contains(e.relatedTarget) && !w.matches(":hover")) release(w);
    });
  }
  document.addEventListener("pointerdown", (e) => {
    if (held && e.pointerType !== "mouse" && !held.contains(e.target)) release(held);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && held && !dlg.open) release(held); });
  const schedulePlace = () => {
    if (!held || placeFrame) return;
    placeFrame = requestAnimationFrame(() => { placeFrame = 0; if (held) placeBear(held); });
  };
  window.addEventListener("scroll", schedulePlace, { passive: true });
  window.addEventListener("resize", schedulePlace);

  /* ---------- full report dialog ---------- */
  const dlg = $("#reportDlg");
  let dlgTrigger = null;
  let dlgTopic = null;
  let dlgClosing = false;
  let closeTimer = 0, closeEnd = null, heldTimer = 0, downOnBackdrop = false, upOnBackdrop = false;
  addPeek(dlg, "Here's everything!");

  // Drop any close animation still pending, so it can't close the next opening.
  function clearClose() {
    clearTimeout(closeTimer);
    if (closeEnd) dlg.removeEventListener("animationend", closeEnd);
    closeTimer = 0;
    closeEnd = null;
  }

  function openReport(id, trigger) {
    const entry = topicsById.get(id);
    if (!entry || !dlg || !entry.report) return;
    const { report, items } = entry;
    clearClose();
    clearTimeout(heldTimer);
    dlgTrigger = trigger || document.activeElement;
    dlgTopic = id;
    if (held) release(held);

    dlg.className = `dlg acc-${entry.accent}`;
    $("#dlgEmoji").textContent = entry.emoji || "📰";
    $("#dlgTitle").textContent = entry.title;
    $("#dlgSched").replaceWith(Object.assign(schedLine(entry.schedule, report), { id: "dlgSched" }));
    const body = $("#dlgBody");
    const parts = [];
    if (str(report.headline)) parts.push(el("p", { cls: "dlg-headline", text: str(report.headline) }));
    if (str(report.summary)) parts.push(el("p", { cls: "dlg-summary", text: str(report.summary) }));
    parts.push(el("h3", { cls: "dlg-sub", text: items.length ? `All ${items.length} item${items.length === 1 ? "" : "s"}` : "No items this time" }));
    if (items.length) parts.push(el("ol", { cls: "items" }, items.map(renderFullItem)));
    parts.push(el("p", { cls: "dlg-note", text: "Every link opens the original source in a new tab. Summaries are written by a free AI model from these items only." }));
    body.replaceChildren(...parts);
    body.scrollTop = 0;

    dlgClosing = false;
    if (!dlg.open) dlg.showModal();
    document.body.classList.add("dlg-open");
    $(".dlg-close", dlg).focus();
    heldTimer = setTimeout(() => { if (dlg.open && !dlgClosing) dlg.classList.add("is-held"); }, reduceMotion.matches ? 0 : 260);
  }

  function finishClose() {
    clearClose();
    if (dlg.open) dlg.close();
  }
  function closeReport() {
    if (!dlg.open || dlgClosing) return;
    dlgClosing = true;
    dlg.classList.remove("is-held");
    if (reduceMotion.matches) { finishClose(); return; }
    dlg.classList.add("closing");
    closeTimer = setTimeout(finishClose, 400);
    closeEnd = (e) => { if (e.target === dlg && e.animationName === "dlgOut") finishClose(); };
    dlg.addEventListener("animationend", closeEnd);
  }
  if (dlg) {
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeReport(); });
    // Backdrop: only when the press starts and ends there, so a drag into or out of the panel doesn't close it.
    dlg.addEventListener("pointerdown", (e) => { downOnBackdrop = e.target === dlg; });
    dlg.addEventListener("pointerup", (e) => { upOnBackdrop = e.target === dlg; });
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg && downOnBackdrop && upOnBackdrop) closeReport();
      downOnBackdrop = upOnBackdrop = false;
    });
    $(".dlg-close", dlg).addEventListener("click", closeReport);
    dlg.addEventListener("close", () => {
      clearClose();
      clearTimeout(heldTimer);
      dlgClosing = false;
      dlg.classList.remove("closing", "is-held");
      document.body.classList.remove("dlg-open");
      let t = dlgTrigger;
      dlgTrigger = null;
      if (!t || !document.contains(t)) { const w = dlgTopic && byTopic(dlgTopic); t = w && $(".open-btn", w); } // trigger re-rendered away
      if (t) t.focus({ preventScroll: true });
      if (pendingData) { const d = pendingData; pendingData = null; rerender(d); }
    });
  }

  /* ---------- morning timeline ---------- */
  const runsOn = (slot, day) => !slot.weekdays || (day >= 1 && day <= 5);
  function inText(mins) {
    if (mins < 60) return `in ${mins} min`;
    const h = Math.floor(mins / 60), m = mins % 60;
    return m ? `in ${h} h ${m} min` : `in ${h} h`;
  }

  // Built once, then updated in place each tick so keyboard focus on a button survives.
  let tlRows = null;
  function buildTimeline(list) {
    tlRows = SLOTS.map((s) => {
      const row = { name: document.createTextNode(""), sep: sr(""), state: document.createTextNode("") };
      row.btn = el("button", { cls: "tl-btn", attrs: { type: "button" } }, [
        el("span", { cls: "tl-dot", text: s.emoji, attrs: { "aria-hidden": "true" } }),
        el("span", { cls: "tl-time" }, [document.createTextNode(s.time), sr(" IST,")]),
        el("span", { cls: "tl-name" }, [row.name]),
        el("span", { cls: "tl-state" }, [row.sep, row.state]),
      ]);
      row.btn.addEventListener("click", () => showWidget(s.id));
      row.li = el("li", { cls: "tl" }, [row.btn]);
      list.append(row.li);
      return row;
    });
  }

  function renderTimeline() {
    const list = $("#timeline");
    if (!list) return;
    const { minutes, day, shifted } = istNow();
    const hh = String(shifted.getUTCHours()).padStart(2, "0");
    const mm = String(shifted.getUTCMinutes()).padStart(2, "0");
    const dayName = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][day];
    const clock = $("#clock");
    if (clock) clock.textContent = `It's ${hh}:${mm} in India · ${dayName}`;

    const anyLeft = SLOTS.some((s) => runsOn(s, day) && toMin(s.time) > minutes);
    const viewDay = anyLeft ? day : (day + 1) % 7;
    const nowMin = anyLeft ? minutes : -1;
    const next = SLOTS.find((s) => runsOn(s, viewDay) && toMin(s.time) > nowMin);

    if (!tlRows) buildTimeline(list);
    SLOTS.forEach((s, i) => {
      const row = tlRows[i];
      const runs = runsOn(s, viewDay);
      const done = runs && toMin(s.time) <= nowMin;
      const isNext = s === next;
      let state = "";
      if (!runs) state = "Weekend off";
      else if (done) state = "Done";
      else if (isNext) {
        const wait = anyLeft ? toMin(s.time) - minutes : 1440 - minutes + toMin(s.time);
        state = `${anyLeft ? "Next" : "Tomorrow"} · ${inText(wait)}`;
      }
      row.name.data = s.id === "deep_dive" && DEEP_DIVE[viewDay] ? `Deep dive: ${DEEP_DIVE[viewDay]}` : s.name;
      row.state.data = state;
      row.sep.textContent = state ? ", " : "";
      row.li.className = `tl${done ? " done" : ""}${isNext ? " next" : ""}${!runs ? " rest" : ""}`;
      if (isNext) row.btn.setAttribute("aria-current", "time");
      else row.btn.removeAttribute("aria-current");
    });
  }

  function showWidget(id) {
    const w = byTopic(id);
    if (!w) return;
    if (w.hidden) applyFilter("all");
    w.scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "center" });
    const target = $(".open-btn", w) || $(".card", w);
    setTimeout(() => target.focus({ preventScroll: true }), reduceMotion.matches ? 0 : 450);
  }

  /* ---------- scroll reveal, header & print ---------- */
  function initReveal() {
    const targets = document.querySelectorAll(".reveal, .flow");
    if (!("IntersectionObserver" in window)) {
      targets.forEach((t) => t.classList.add("is-visible", "in-view"));
      return;
    }
    // Threshold 0: a section taller than a short viewport could never reach a percentage threshold.
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.target.classList.contains("flow")) { en.target.classList.toggle("in-view", en.isIntersecting); return; }
        if (en.isIntersecting) { en.target.classList.add("is-visible"); io.unobserve(en.target); }
      });
    }, { threshold: 0, rootMargin: "0px 0px -24px 0px" });
    targets.forEach((t) => io.observe(t));
  }
  function initHeader() {
    const bar = $(".topbar");
    const onScroll = () => bar.classList.toggle("scrolled", window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
  // Print everything: reveal all sections and open the FAQ, then put the FAQ back as it was.
  function initPrint() {
    let faqState = null;
    window.addEventListener("beforeprint", () => {
      document.querySelectorAll(".reveal").forEach((t) => t.classList.add("is-visible"));
      faqState = [...document.querySelectorAll("#faq details")].map((d) => [d, d.open]);
      faqState.forEach(([d]) => { d.open = true; });
    });
    window.addEventListener("afterprint", () => {
      if (faqState) faqState.forEach(([d, open]) => { d.open = open; });
      faqState = null;
    });
  }

  /* ---------- boot ---------- */
  // The reports render above most sections and push them down, so re-land a #link from the URL
  // unless the reader has already started moving around.
  let moved = false;
  ["wheel", "touchstart", "keydown", "pointerdown"].forEach((t) => window.addEventListener(t, () => { moved = true; }, { once: true, passive: true }));
  function keepAnchor() {
    if (moved || !location.hash) return;
    let target = null;
    try { target = document.querySelector(decodeURIComponent(location.hash)); } catch { /* not a valid selector */ }
    if (target) requestAnimationFrame(() => target.scrollIntoView({ block: "start", behavior: "auto" }));
  }

  let lastFetch = 0;
  let fetching = false;
  async function refresh(initial) {
    if (fetching) return;
    fetching = true;
    lastFetch = Date.now();
    try {
      const { data, fallback } = await loadReports(initial);
      if (initial) { renderReports(data, fallback, true); keepAnchor(); }
      else if (data && !fallback && str(data.generated_at) !== currentStamp) rerender(data); // a failed refresh keeps what's shown
    } finally { fetching = false; }
  }

  drawBears();
  initTheme();
  initHeader();
  initReveal();
  initPrint();
  renderTimeline();
  refresh(true);
  setInterval(() => { renderTimeline(); updateTimes(); }, 30000);
  setInterval(() => { if (document.visibilityState === "visible") refresh(false); }, 10 * 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    renderTimeline();
    updateTimes();
    if (Date.now() - lastFetch >= 60000) refresh(false);
  });
})();
