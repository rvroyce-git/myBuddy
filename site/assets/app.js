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
  const VISIBLE_ITEMS = 2;

  // Schedule shown in the "morning" timeline (IST). Mirrors the topics in config.
  const DEEP_DIVE = { 1: "RAG", 2: "Agents", 3: "Training & fine-tuning", 4: "Audio & video", 5: "Evaluation" };
  const SLOTS = [
    { id: "ai_news", time: "06:30", name: "AI news", emoji: "🧠" },
    { id: "security", time: "07:00", name: "Cybersecurity", emoji: "🛡️" },
    { id: "github_trending", time: "07:15", name: "Trending repos", emoji: "⭐" },
    { id: "deep_dive", time: "07:30", name: "Deep dive", emoji: "🔭", weekdays: true },
    { id: "research_papers", time: "07:45", name: "Research papers", emoji: "📄" },
    { id: "startups", time: "08:00", name: "Startups & launches", emoji: "🚀" },
    { id: "ai_models", time: "08:15", name: "Open models", emoji: "🧩" },
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
  function relTime(d) {
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} h ago`;
    const days = Math.round(hrs / 24);
    return days === 1 ? "yesterday" : `${days} days ago`;
  }
  const fmtFull = (d) => d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

  /* ---------- bear artwork (static, trusted markup) ---------- */
  const FULL_BEAR = `
<svg viewBox="0 0 240 262" xmlns="http://www.w3.org/2000/svg">
  <g class="wave-arm">
    <ellipse cx="192" cy="118" rx="19" ry="36" transform="rotate(28 192 118)" class="fur"/>
    <circle cx="208" cy="86" r="19" class="fur"/>
    <ellipse cx="208" cy="88" rx="10" ry="8" class="inner"/>
  </g>
  <ellipse cx="78" cy="244" rx="30" ry="17" class="fur"/>
  <ellipse cx="162" cy="244" rx="30" ry="17" class="fur"/>
  <ellipse cx="78" cy="245" rx="15" ry="9" class="inner"/>
  <ellipse cx="162" cy="245" rx="15" ry="9" class="inner"/>
  <ellipse cx="120" cy="190" rx="72" ry="62" class="fur"/>
  <ellipse cx="120" cy="202" rx="44" ry="38" class="muzzle"/>
  <g transform="rotate(-9 74 178)">
    <rect x="34" y="150" width="78" height="58" rx="8" class="paper"/>
    <line x1="46" y1="166" x2="88" y2="166" class="paper-line t"/>
    <line x1="46" y1="180" x2="100" y2="180" class="paper-line"/>
    <line x1="46" y1="192" x2="94" y2="192" class="paper-line"/>
  </g>
  <ellipse cx="66" cy="196" rx="21" ry="16" class="fur"/>
  <ellipse cx="64" cy="198" rx="10" ry="7" class="inner"/>
  <g>
    <circle cx="66" cy="46" r="25" class="fur"/><circle cx="66" cy="46" r="13" class="inner"/>
    <circle cx="174" cy="46" r="25" class="fur"/><circle cx="174" cy="46" r="13" class="inner"/>
    <ellipse cx="120" cy="94" rx="66" ry="58" class="fur"/>
    <ellipse cx="120" cy="114" rx="28" ry="21" class="muzzle"/>
    <ellipse cx="80" cy="108" rx="10" ry="6.5" class="cheek"/>
    <ellipse cx="160" cy="108" rx="10" ry="6.5" class="cheek"/>
    <g class="eyes">
      <circle cx="96" cy="88" r="7.5" class="eye"/><circle cx="144" cy="88" r="7.5" class="eye"/>
      <circle cx="98.5" cy="85.5" r="2.5" class="shine"/><circle cx="146.5" cy="85.5" r="2.5" class="shine"/>
    </g>
    <ellipse cx="120" cy="105" rx="9.5" ry="7" class="nose"/>
    <path d="M120 112v6M109 120c5 5 17 5 22 0" class="mouth"/>
  </g>
</svg>`;

  function drawBears() {
    const full = $('[data-bear="full"]');
    if (full) full.innerHTML = FULL_BEAR; // static markup above, no external data
    const tpl = $("#peekBear");
    const face = $('[data-bear="face"]');
    if (tpl && face) face.append(tpl.content.querySelector(".peek-head").cloneNode(true));
  }

  /* ---------- theme toggle ---------- */
  function initTheme() {
    const btn = $("#themeBtn");
    let saved = null;
    try { saved = localStorage.getItem("mybuddy-theme"); } catch { /* storage blocked */ }
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
    const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const label = () => btn.setAttribute("aria-label", isDark() ? "Switch to light theme" : "Switch to dark theme");
    label();
    btn.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("mybuddy-theme", next); } catch { /* ignore */ }
      label();
    });
  }

  /* ---------- data loading ---------- */
  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.topics)) throw new Error("Unexpected data shape");
    return data;
  }

  async function loadReports() {
    try {
      return { data: await getJSON(DATA_URL), fallback: false };
    } catch (err) {
      console.warn("MyBuddy: reports.json unavailable, using sample data.", err);
      try {
        return { data: await getJSON(SAMPLE_URL), fallback: true };
      } catch (err2) {
        console.warn("MyBuddy: sample data unavailable too.", err2);
        return { data: null, fallback: true };
      }
    }
  }

  /* ---------- widgets ---------- */
  const widgetsBox = $("#widgets");
  const chipsBox = $("#chips");
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
    return [document.createTextNode(head), el("span", { cls: "nowrap" }, [document.createTextNode(tail), extIcon()])];
  }

  function renderItem(item) {
    const title = str(item && item.title) || "Untitled";
    const url = safeUrl(item && item.url);
    let head;
    if (url) {
      head = el("a", { attrs: { href: url, target: "_blank", rel: "noopener noreferrer" } }, [
        ...titleWithIcon(title), el("span", { cls: "sr-only", text: " (opens in a new tab)" }),
      ]);
    } else {
      head = el("span", { cls: "item-title", text: title });
    }
    const li = el("li", { cls: "item" }, [head]);
    const source = str(item && item.source);
    if (source) li.append(el("div", {}, [el("span", { cls: "src", text: source })]));
    const blurb = str(item && item.blurb);
    if (blurb) li.append(el("p", { cls: "blurb", text: blurb }));
    return li;
  }

  function firstTime(schedule) {
    const m = /(\d{1,2}:\d{2})/.exec(schedule);
    return m ? `${m[1]} IST` : null;
  }

  function renderWidget(topic, idx) {
    const id = str(topic.id) || `topic-${idx}`;
    const title = str(topic.title) || "Untitled topic";
    const schedule = str(topic.schedule);
    const report = topic.report && typeof topic.report === "object" ? topic.report : null;
    const accent = ACCENTS[id] || ACCENT_CYCLE[idx % ACCENT_CYCLE.length];
    const titleId = `w-${idx}-title`;

    const widget = el("div", { cls: `widget acc-${accent}`, attrs: { "data-topic": id } });
    widget.style.animationDelay = reduceMotion.matches ? "0s" : `${Math.min(idx, 8) * 70}ms`;

    const peek = $("#peekBear").content.firstElementChild.cloneNode(true);
    $(".peek-bubble", peek).textContent = report ? (BUBBLES[id] || "For you!") : "Coming soon!";

    const card = el("article", { cls: "card", attrs: { tabindex: "0", "aria-labelledby": titleId } });
    const sched = el("p", { cls: "sched" }, [el("span", { text: schedule || "Schedule not set" })]);
    const created = report ? parseDate(report.created_at) : null;
    if (created) {
      sched.append(el("time", { cls: "ago", text: relTime(created), attrs: { datetime: created.toISOString(), title: fmtFull(created), "data-rel": "" } }));
    }
    card.append(el("div", { cls: "card-top" }, [
      el("span", { cls: "emoji", text: str(topic.emoji) || "📰", attrs: { "aria-hidden": "true" } }),
      el("div", {}, [el("h3", { cls: "card-title", text: title, attrs: { id: titleId } }), sched]),
    ]));

    if (!report) {
      const when = firstTime(schedule);
      const p = el("p");
      if (when) p.append("First report arrives at ", el("strong", { text: when }), ".");
      else p.append("The first report is on its way.");
      const box = el("div", { cls: "empty" }, [el("span", { cls: "zz", text: "💤", attrs: { "aria-hidden": "true" } }), p]);
      const desc = str(topic.description);
      if (desc) box.append(el("p", { cls: "summary", text: desc }));
      card.append(box);
    } else {
      const headline = str(report.headline);
      const summary = str(report.summary);
      if (headline) card.append(el("p", { cls: "headline", text: headline }));
      if (summary) card.append(el("p", { cls: "summary", text: summary }));
      const items = Array.isArray(report.items) ? report.items : [];
      if (items.length === 0) {
        card.append(el("p", { cls: "summary", text: "Nothing new made the cut this time." }));
      } else {
        card.append(el("ul", { cls: "items", attrs: { "aria-label": `${title} sources` } }, items.slice(0, VISIBLE_ITEMS).map(renderItem)));
        const rest = items.slice(VISIBLE_ITEMS);
        if (rest.length) {
          const moreId = `w-${idx}-more`;
          const more = el("div", { cls: "more", attrs: { id: moreId } }, [el("ul", { cls: "items" }, rest.map(renderItem))]);
          more.inert = true;
          more.setAttribute("aria-hidden", "true");
          const label = el("span", { text: `Show ${rest.length} more` });
          const btn = el("button", { cls: "more-btn", attrs: { type: "button", "aria-expanded": "false", "aria-controls": moreId } }, [
            label, el("span", { cls: "chev", text: "⌄", attrs: { "aria-hidden": "true" } }),
          ]);
          btn.addEventListener("click", () => {
            const open = btn.getAttribute("aria-expanded") !== "true";
            btn.setAttribute("aria-expanded", String(open));
            more.classList.toggle("open", open);
            more.inert = !open;
            more.setAttribute("aria-hidden", String(!open));
            label.textContent = open ? "Show fewer" : `Show ${rest.length} more`;
          });
          card.append(more, btn);
        }
      }
    }

    // Layering: bear head (behind card) -> card -> paws (in front of the card's top edge).
    widget.append($(".peek-back", peek), card, $(".peek-front", peek));
    bindHold(widget);
    return widget;
  }

  function renderChips(topics) {
    chipsBox.replaceChildren();
    const make = (id, text) => {
      const b = el("button", { cls: "chip", text, attrs: { type: "button", "aria-pressed": id === "all" ? "true" : "false", "data-filter": id } });
      b.addEventListener("click", () => applyFilter(id));
      return b;
    };
    chipsBox.append(make("all", "All topics"));
    topics.forEach((t) => chipsBox.append(make(str(t.id), `${str(t.emoji)} ${str(t.title)}`.trim())));
  }

  function applyFilter(id) {
    chipsBox.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.filter === id)));
    widgetsBox.querySelectorAll(".widget").forEach((w) => { w.hidden = !(id === "all" || w.dataset.topic === id); });
  }

  function renderReports(data, fallback) {
    const notice = $("#notice");
    if (!data) {
      widgetsBox.replaceChildren(el("p", { cls: "empty", text: "We couldn't load the reports right now. Please check back in a little while." }));
      $("#updatedText").textContent = "Reports unavailable";
      return;
    }
    if (fallback) {
      notice.textContent = "We couldn't reach the latest reports just now, so here's sample data to show how it looks.";
      notice.hidden = false;
    } else if (data.sample) {
      notice.textContent = "These are sample reports. Real ones appear here once MyBuddy runs on its server.";
      notice.hidden = false;
    } else {
      notice.hidden = true;
    }
    const topics = data.topics.filter((t) => t && typeof t === "object");
    generatedAt = parseDate(data.generated_at);
    currentStamp = str(data.generated_at);
    renderChips(topics);
    widgetsBox.replaceChildren(...topics.map(renderWidget));
    updateTimes();
  }

  function updateTimes() {
    const txt = $("#updatedText");
    if (generatedAt) {
      txt.textContent = `Updated ${relTime(generatedAt)}`;
      txt.parentElement.title = fmtFull(generatedAt);
    } else if (currentStamp !== null) {
      txt.textContent = "Update time unknown";
    }
    widgetsBox.querySelectorAll("time[data-rel]").forEach((t) => {
      const d = parseDate(t.getAttribute("datetime"));
      if (d) t.textContent = relTime(d);
    });
  }

  /* ---------- bear "holding" a widget ---------- */
  let held = null;
  function hold(w) {
    if (held === w) return;
    if (held) release(held);
    held = w;
    w.classList.add("is-held");
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
      if (e.pointerType === "mouse" && !w.contains(document.activeElement)) release(w);
      else if (e.pointerType === "mouse") { w.style.removeProperty("--rx"); w.style.removeProperty("--ry"); }
    });
    w.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse" || reduceMotion.matches || held !== w) return;
      const r = w.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      w.style.setProperty("--ry", `${(x * 5).toFixed(2)}deg`);
      w.style.setProperty("--rx", `${(-y * 4).toFixed(2)}deg`);
    });
    w.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") hold(w); });
    w.addEventListener("focusin", () => hold(w));
    w.addEventListener("focusout", (e) => {
      if (!w.contains(e.relatedTarget) && !w.matches(":hover")) release(w);
    });
  }
  document.addEventListener("pointerdown", (e) => {
    if (held && e.pointerType !== "mouse" && !held.contains(e.target)) release(held);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && held) release(held); });

  /* ---------- morning timeline ---------- */
  function istNow() {
    const shifted = new Date(Date.now() + IST_OFFSET_MIN * 60000);
    return { minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(), day: shifted.getUTCDay(), shifted };
  }
  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  const runsOn = (slot, day) => !slot.weekdays || (day >= 1 && day <= 5);
  function inText(mins) {
    if (mins < 60) return `in ${mins} min`;
    const h = Math.floor(mins / 60), m = mins % 60;
    return m ? `in ${h} h ${m} min` : `in ${h} h`;
  }

  function renderTimeline() {
    const list = $("#timeline");
    const { minutes, day, shifted } = istNow();
    const hh = String(shifted.getUTCHours()).padStart(2, "0");
    const mm = String(shifted.getUTCMinutes()).padStart(2, "0");
    const dayName = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][day];
    $("#clock").textContent = `It's ${hh}:${mm} in India · ${dayName}`;

    const anyLeft = SLOTS.some((s) => runsOn(s, day) && toMin(s.time) > minutes);
    const viewDay = anyLeft ? day : (day + 1) % 7;
    const nowMin = anyLeft ? minutes : -1;
    const next = SLOTS.find((s) => runsOn(s, viewDay) && toMin(s.time) > nowMin);

    list.replaceChildren(...SLOTS.map((s) => {
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
      const name = s.id === "deep_dive" && DEEP_DIVE[viewDay] ? `Deep dive: ${DEEP_DIVE[viewDay]}` : s.name;
      const btn = el("button", { cls: "tl-btn", attrs: { type: "button", "aria-label": `${s.time} IST, ${name}${state ? ", " + state : ""}. Show this report.` } }, [
        el("span", { cls: "tl-dot", text: s.emoji, attrs: { "aria-hidden": "true" } }),
        el("span", { cls: "tl-time", text: s.time }),
        el("span", { cls: "tl-name", text: name }),
        el("span", { cls: "tl-state", text: state }),
      ]);
      btn.addEventListener("click", () => showWidget(s.id));
      const li = el("li", { cls: `tl${done ? " done" : ""}${isNext ? " next" : ""}${!runs ? " rest" : ""}` }, [btn]);
      if (isNext) li.setAttribute("aria-current", "time");
      return li;
    }));
  }

  function showWidget(id) {
    const w = widgetsBox.querySelector(`.widget[data-topic="${CSS.escape(id)}"]`);
    if (!w) return;
    if (w.hidden) applyFilter("all");
    w.scrollIntoView({ behavior: reduceMotion.matches ? "auto" : "smooth", block: "center" });
    setTimeout(() => $(".card", w).focus({ preventScroll: true }), reduceMotion.matches ? 0 : 450);
  }

  /* ---------- scroll reveal & header ---------- */
  function initReveal() {
    const targets = document.querySelectorAll(".reveal, .flow");
    if (!("IntersectionObserver" in window)) {
      targets.forEach((t) => t.classList.add("is-visible", "in-view"));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.target.classList.contains("flow")) { en.target.classList.toggle("in-view", en.isIntersecting); return; }
        if (en.isIntersecting) { en.target.classList.add("is-visible"); io.unobserve(en.target); }
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });
    targets.forEach((t) => io.observe(t));
  }
  function initHeader() {
    const bar = $(".topbar");
    const onScroll = () => bar.classList.toggle("scrolled", window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ---------- boot ---------- */
  async function refresh(initial) {
    const { data, fallback } = await loadReports();
    if (initial || (data && str(data.generated_at) !== currentStamp)) renderReports(data, fallback);
  }

  drawBears();
  initTheme();
  initHeader();
  initReveal();
  renderTimeline();
  refresh(true);
  setInterval(() => { renderTimeline(); updateTimes(); }, 30000);
  setInterval(() => { if (document.visibilityState === "visible") refresh(false); }, 10 * 60000);
})();
