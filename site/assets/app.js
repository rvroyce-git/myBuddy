/* MyBuddy: renders data/reports.json as calm news cards, a report popup, and the small motion touches.
   All data goes into the DOM via textContent; only http(s) links are used. No external scripts. */
(() => {
  "use strict";

  const root = document.documentElement;
  root.classList.add("js");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const $ = (sel, ctx = document) => ctx.querySelector(sel);

  const DATA_URL = "data/reports.json";
  const SAMPLE_URL = "data/reports.sample.json";
  const IST_MIN = 330;
  const ACCENTS = { ai_news: "teal", security: "blue", github_trending: "sage", deep_dive: "lav", research_papers: "blue", startups: "sage", ai_models: "lav" };
  const CYCLE = ["teal", "blue", "sage", "lav"];
  // Simple line icons per topic (24px grid); unknown topics fall back to the data emoji.
  const ICONS = {
    ai_news: "M5 5h11v14H7a2 2 0 0 1-2-2zM16 9h3v8a2 2 0 0 1-2 2M8.5 9h4.5M8.5 12.5h4.5M8.5 16h2.5",
    security: "M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6zM9 12l2 2 4-4",
    github_trending: "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z",
    deep_dive: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM15.5 15.5L20 20",
    research_papers: "M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 15.5h5M10 19h3",
    startups: "M12 15l-3-3c1.6-4.3 4.8-7.3 10-8-.6 5.2-3.7 8.4-7 11zM9 12H5.5L8 9h3.2M12 15v3.5l3-2.5v-3.2M6.5 17.5c-1.2.6-1.5 2.5-1.5 2.5s1.9-.3 2.5-1.5",
    ai_models: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9",
  };
  function topicIcon(t) {
    const d = ICONS[t.id];
    if (!d) return document.createTextNode(t.emoji);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
    return svg;
  }

  /* ---------- helpers ---------- */
  function el(tag, opts = {}, kids = []) {
    const n = document.createElement(tag);
    if (opts.cls) n.className = opts.cls;
    if (opts.text != null) n.textContent = String(opts.text);
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) if (v != null) n.setAttribute(k, String(v));
    for (const c of kids) if (c) n.append(c);
    return n;
  }
  const str = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v).trim());
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
  const say = (msg) => { const s = $("#srStatus"); if (s) s.textContent = msg; };
  function safeUrl(u) {
    try { const url = new URL(str(u)); return url.protocol === "https:" || url.protocol === "http:" ? url.href : null; }
    catch { return null; }
  }
  function parseDate(s) { const d = new Date(str(s)); return Number.isNaN(d.getTime()) ? null : d; }
  // Wall-clock parts in India (UTC+5:30, no DST).
  function ist(d) {
    const x = new Date(d.getTime() + IST_MIN * 60000);
    return { y: x.getUTCFullYear(), mo: x.getUTCMonth(), d: x.getUTCDate(), day: x.getUTCDay(), h: x.getUTCHours(), m: x.getUTCMinutes() };
  }
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (p) => `${pad(p.h)}:${pad(p.m)}`;
  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const schedTime = (s) => { const m = /(\d{1,2}):(\d{2})/.exec(s); return m ? `${pad(+m[1])}:${m[2]}` : ""; };
  const dayKey = (p) => Date.UTC(p.y, p.mo, p.d);
  const shortDate = (p) => new Date(dayKey(p)).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  // Days between a date and today, both in IST (0 = today, 1 = yesterday).
  const daysAgo = (d) => Math.round((dayKey(ist(new Date())) - dayKey(ist(d))) / 864e5);
  const dayLabel = (d) => { const n = daysAgo(d); return n <= 0 ? "" : n === 1 ? "Yesterday" : shortDate(ist(d)); };
  const weekdaysOnly = (id, s) => id === "deep_dive" || /mon\s*[–-]\s*fri|weekdays/i.test(s);

  const quietVT = (vt) => { vt.ready.catch(() => {}); vt.updateCallbackDone.catch(() => {}); return vt; };

  /* ---------- theme ---------- */
  function initTheme() {
    const btn = $("#themeBtn");
    const os = window.matchMedia("(prefers-color-scheme: dark)");
    const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : os.matches);
    const label = () => { const t = isDark() ? "Switch to light theme" : "Switch to dark theme"; btn.setAttribute("aria-label", t); btn.title = t; };
    label();
    btn.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      const apply = () => { root.dataset.theme = next; label(); };
      if (document.startViewTransition && !reduce.matches) quietVT(document.startViewTransition(apply));
      else apply();
      try { localStorage.setItem("mybuddy-theme", next); } catch { /* storage blocked */ }
    });
    if (os.addEventListener) os.addEventListener("change", label);
  }

  /* ---------- data ---------- */
  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.topics)) throw new Error("Unexpected data shape");
    return data;
  }
  async function load(allowSample) {
    try { return { data: await getJSON(DATA_URL), sample: false }; }
    catch (err) {
      if (!allowSample) return { data: null };
      console.warn("MyBuddy: reports.json unavailable, using sample data.", err);
      try { return { data: await getJSON(SAMPLE_URL), sample: true }; }
      catch (err2) { console.warn("MyBuddy: sample data unavailable too.", err2); return { data: null }; }
    }
  }
  const isItem = (it) => isObj(it) && !!(str(it.title) || safeUrl(it.url));
  function normTopics(list) {
    const used = new Set();
    return list.filter(isObj).slice(0, 12).map((t, idx) => {
      let id = str(t.id) || `topic-${idx}`;
      while (used.has(id)) id += "-x";
      used.add(id);
      const report = isObj(t.report) ? t.report : null;
      const schedule = str(t.schedule);
      return {
        id, idx, report, schedule,
        items: report && Array.isArray(report.items) ? report.items.filter(isItem).slice(0, 20) : [],
        title: str(t.title) || "Untitled",
        emoji: str(t.emoji) || "📰",
        time: schedTime(schedule),
        accent: ACCENTS[id] || CYCLE[idx % CYCLE.length],
      };
    });
  }

  /* ---------- cards ---------- */
  const grid = $("#grid");
  const tpl = $("#peekBear");
  const topics = new Map();
  let stamp = null;

  function addPeek(host, bubble) {
    const back = tpl.content.querySelector(".peek-back").cloneNode(true);
    const front = tpl.content.querySelector(".peek-front").cloneNode(true);
    $(".peek-bubble", back).textContent = bubble;
    host.prepend(back);
    host.append(front);
  }

  // Empty card text: weekday-only topics say "Back Monday" over the weekend.
  function waitText(t) {
    const now = ist(new Date());
    const nowMin = now.h * 60 + now.m;
    if (weekdaysOnly(t.id, t.schedule)) {
      const late = t.time && nowMin >= toMin(t.time);
      if (now.day === 0 || now.day === 6 || (now.day === 5 && late)) return "Back Monday";
    }
    return t.time ? `Coming at ${t.time}` : "Coming soon";
  }

  function renderCard(t) {
    const titleId = `t-${t.idx}`;
    // Time: today's report time, "Yesterday"/"22 Nov" for older ones, the schedule time while waiting.
    const created = t.report ? parseDate(t.report.created_at) : null;
    const old = created ? dayLabel(created) : "";
    const when = created ? old || hhmm(ist(created)) : t.time;
    const card = el("article", { cls: `card${old ? " stale" : ""}`, attrs: { "aria-labelledby": titleId } }, [
      el("span", { cls: "card-bg", attrs: { "aria-hidden": "true" } }),
      el("div", { cls: "card-top" }, [
        el("span", { cls: "emoji", attrs: { "aria-hidden": "true" } }, [topicIcon(t)]),
        el("div", { cls: "card-id" }, [
          el("h3", { cls: "topic", text: t.title, attrs: { id: titleId } }),
          when ? el("span", { cls: "time", text: when, attrs: { "aria-label": old || `${when} IST` } }) : null,
        ]),
      ]),
    ]);
    const headline = t.report ? str(t.report.headline) || (t.items[0] && str(t.items[0].title)) || "Fresh items are in." : "";
    if (t.report) {
      card.append(el("p", { cls: "headline", text: headline }));
      const btn = el("button", { cls: "read", attrs: { type: "button", "aria-haspopup": "dialog", "aria-label": `Read: ${t.title}` } }, [
        el("span", { text: "Read" }), el("span", { cls: "arrow", text: "→", attrs: { "aria-hidden": "true" } }),
      ]);
      btn.addEventListener("click", () => openReport(t.id, btn));
      card.append(el("div", { cls: "card-foot" }, [btn]));
    } else {
      card.append(el("p", { cls: "waiting", attrs: { "data-loop": "" } }, [tpl.content.querySelector(".sleepy").cloneNode(true), el("span", { text: waitText(t) })]));
    }
    const widget = el("div", { cls: `widget acc-${t.accent}`, attrs: { "data-topic": t.id } }, [card]);
    addPeek(widget, t.report ? "Yours!" : "Zzz…");
    bindHold(widget);
    return el("li", { cls: "cell" }, [widget]);
  }

  function renderUpdated(data, sample, list) {
    const box = $("#updated");
    if (!data) { box.hidden = true; return; }
    // Newest report time; the export time only if no report has one.
    const times = list.map((t) => t.report && parseDate(t.report.created_at)).filter(Boolean);
    const d = times.length ? new Date(Math.max(...times)) : parseDate(data.generated_at);
    let text = "";
    if (sample || data.sample === true) text = "Sample data";
    else if (d) {
      const old = dayLabel(d);
      text = `Updated ${old ? `${old}, ` : ""}${hhmm(ist(d))} IST`;
    }
    $("#updatedText").textContent = text;
    box.hidden = !text;
  }

  function render(data, sample, animate) {
    held = null;
    topics.clear();
    if (!data) {
      renderUpdated(null);
      grid.replaceChildren(el("li", { cls: "grid-msg", text: "News unavailable. Please try later." }));
      say("News unavailable.");
      return;
    }
    stamp = str(data.generated_at);
    const list = normTopics(data.topics);
    renderUpdated(data, sample, list);
    list.forEach((t) => topics.set(t.id, t));
    if (!list.length) { grid.replaceChildren(el("li", { cls: "grid-msg", text: "No news yet." })); return; }
    const cells = list.map(renderCard);
    grid.replaceChildren(...cells);
    if (animate) observeCells(cells); else cells.forEach((c) => c.classList.add("in"));
    grid.querySelectorAll("[data-loop]").forEach(watchLoop);
    say(`${list.length} topics loaded.`);
  }

  /* ---------- fly-in on scroll ---------- */
  let cellIO = null;
  function observeCells(cells) {
    if (reduce.matches || !("IntersectionObserver" in window)) { cells.forEach((c) => c.classList.add("in")); return; }
    if (!cellIO) {
      cellIO = new IntersectionObserver((entries) => {
        entries.filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top || a.boundingClientRect.left - b.boundingClientRect.left)
          .forEach((e, i) => {
            e.target.style.setProperty("--d", `${i * 90}ms`);
            e.target.classList.add("in");
            cellIO.unobserve(e.target);
            setTimeout(() => e.target.style.removeProperty("--d"), 1200 + i * 90); // hover lift stays snappy afterwards
          });
      }, { threshold: 0.12, rootMargin: "0px 0px -30px 0px" });
    }
    cells.forEach((c) => cellIO.observe(c));
  }
  const loopIO = "IntersectionObserver" in window
    ? new IntersectionObserver((es) => es.forEach((e) => e.target.classList.toggle("run", e.isIntersecting)))
    : null;
  function watchLoop(n) { if (loopIO) loopIO.observe(n); else n.classList.add("run"); }
  const onVisibility = () => root.classList.toggle("tab-hidden", document.visibilityState === "hidden");
  document.addEventListener("visibilitychange", onVisibility);
  onVisibility();

  function initFlow() {
    const flow = $("#flow");
    if (reduce.matches || !("IntersectionObserver" in window)) { flow.classList.add("on"); return; }
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { flow.classList.add("on"); io.disconnect(); }
    }, { threshold: 0.35 });
    io.observe(flow);
  }

  /* ---------- the bear holding a card ----------
     It peeks over the card's top edge; if the sticky bar leaves no room there, it holds the card from below. */
  const topbar = $(".topbar");
  let held = null;
  let frame = 0;
  function placeBear(w) {
    const card = $(".card", w);
    const back = $(".peek-back", w);
    const r = card.getBoundingClientRect();
    const hb = Math.max(0, topbar.getBoundingClientRect().bottom);
    const need = (back ? back.offsetHeight : 90) - 10 + 12;
    const lift = w.classList.contains("is-held") ? 0 : 8;
    const topOK = r.top - lift - hb - need >= 4;
    const bottomOK = r.bottom - lift + need <= window.innerHeight - 4;
    let below;
    if (topOK) below = false;
    else if (bottomOK) below = true;
    else below = (r.top - hb) < (window.innerHeight - r.bottom);
    if (below === w.classList.contains("below")) return;
    // Switch sides while hidden without animating across the card, so the bear always slides out from behind it.
    const parts = [back, ...w.querySelectorAll(".paw")];
    parts.forEach((p) => { p.style.transition = "none"; });
    w.classList.toggle("below", below);
    void w.offsetWidth;
    parts.forEach((p) => { p.style.transition = ""; });
  }
  function hold(w) {
    if (held === w) return;
    if (held) release(held);
    held = w;
    placeBear(w);
    w.classList.add("is-held");
    w.parentElement.classList.add("up");
  }
  function release(w) {
    w.classList.remove("is-held");
    w.parentElement.classList.remove("up");
    w.style.removeProperty("--mx");
    w.style.removeProperty("--my");
    if (held === w) held = null;
    if (pending && !dlg.open) setTimeout(() => { if (pending && !held && !dlg.open) { const d = pending; pending = null; render(d, false, false); } }, 600);
  }
  function bindHold(w) {
    w.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") hold(w); });
    w.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse" && !w.contains(document.activeElement)) release(w); });
    w.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse" || reduce.matches || held !== w) return;
      const r = w.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      w.style.setProperty("--mx", `${(x * 24).toFixed(1)}px`);
      w.style.setProperty("--my", `${(y * 18).toFixed(1)}px`);
    });
    w.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") hold(w); });
    w.addEventListener("focusin", () => hold(w));
    w.addEventListener("focusout", (e) => { if (!w.contains(e.relatedTarget) && !w.matches(":hover")) release(w); });
  }
  document.addEventListener("pointerdown", (e) => { if (held && e.pointerType !== "mouse" && !held.contains(e.target)) release(held); });
  const replace = () => { if (!held || frame) return; frame = requestAnimationFrame(() => { frame = 0; if (held) placeBear(held); }); };
  window.addEventListener("scroll", replace, { passive: true });
  window.addEventListener("resize", replace);

  /* ---------- popup ---------- */
  const dlg = $("#dlg");
  const panel = $(".dlg-panel", dlg);
  let trigger = null, openId = null, closing = false, heldTimer = 0, closeTimer = 0;
  let downOnBackdrop = false;
  addPeek(dlg, "Here you go!");
  const canMorph = () => !!document.startViewTransition && !reduce.matches;
  const cardOf = (id) => { const w = grid.querySelector(`.widget[data-topic="${CSS.escape(id)}"]`); return w && $(".card", w); };
  const onScreen = (n) => { if (!n) return false; const r = n.getBoundingClientRect(); return r.bottom > 0 && r.top < window.innerHeight; };

  function itemNode(item, n) {
    const title = str(item.title) || "Untitled";
    const url = safeUrl(item.url);
    const kids = [el("span", { cls: "item-title", text: title })];
    const src = str(item.source);
    if (src) kids.push(el("span", { cls: "item-src", text: src }));
    const body = url
      ? el("a", { attrs: { href: url, target: "_blank", rel: "noopener noreferrer" } }, [...kids, el("span", { cls: "sr-only", text: " (opens in a new tab)" })])
      : el("div", { cls: "item-plain" }, kids);
    const li = el("li", { cls: "item" }, [body]);
    li.style.setProperty("--n", String(Math.min(n, 10)));
    return li;
  }

  function fill(t) {
    dlg.className = `dlg acc-${t.accent}`;
    $("#dlgEmoji").replaceChildren(topicIcon(t));
    $("#dlgTopic").textContent = t.title;
    const created = parseDate(t.report.created_at);
    const old = created ? dayLabel(created) : "";
    $("#dlgTime").textContent = created ? `${old ? `${old}, ` : ""}${hhmm(ist(created))} IST` : t.time ? `${t.time} IST` : "";
    $("#dlgHeadline").textContent = str(t.report.headline) || t.title;
    $("#dlgSummary").textContent = str(t.report.summary);
    $("#dlgItems").replaceChildren(...t.items.map(itemNode));
    $(".dlg-body", dlg).scrollTop = 0;
  }

  function openReport(id, from) {
    const t = topics.get(id);
    if (!t || !t.report || dlg.open) return;
    clearTimeout(closeTimer);
    trigger = from;
    openId = id;
    closing = false;
    if (held) release(held);
    fill(t);
    const show = () => {
      dlg.showModal();
      document.body.classList.add("dlg-open");
      $(".dlg-close", dlg).focus();
    };
    const card = cardOf(id);
    if (canMorph() && card) {
      dlg.classList.add("vt");
      card.style.viewTransitionName = "mb-pop";
      const vt = quietVT(document.startViewTransition(() => {
        card.style.viewTransitionName = "";
        panel.style.viewTransitionName = "mb-pop";
        show();
      }));
      vt.finished.finally(() => { panel.style.viewTransitionName = ""; });
    } else show();
    heldTimer = setTimeout(() => { if (dlg.open && !closing) dlg.classList.add("is-held"); }, reduce.matches ? 0 : 450);
  }

  function closeReport() {
    if (!dlg.open || closing) return;
    closing = true;
    clearTimeout(heldTimer);
    dlg.classList.remove("is-held");
    const card = openId && cardOf(openId);
    if (canMorph() && card && onScreen(card)) {
      panel.style.viewTransitionName = "mb-pop";
      const vt = quietVT(document.startViewTransition(() => {
        panel.style.viewTransitionName = "";
        dlg.close();
        card.style.viewTransitionName = "mb-pop";
      }));
      vt.finished.finally(() => { card.style.viewTransitionName = ""; });
      return;
    }
    if (reduce.matches) { dlg.close(); return; }
    dlg.classList.remove("vt");
    dlg.classList.add("closing");
    closeTimer = setTimeout(() => dlg.close(), 240);
  }

  dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeReport(); });
  dlg.addEventListener("pointerdown", (e) => { downOnBackdrop = e.target === dlg; });
  dlg.addEventListener("click", (e) => { if (e.target === dlg && downOnBackdrop) closeReport(); downOnBackdrop = false; });
  $(".dlg-close", dlg).addEventListener("click", closeReport);
  // Keep Tab inside the popup.
  dlg.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const f = [...dlg.querySelectorAll("button, a[href]")].filter((n) => n.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  dlg.addEventListener("close", () => {
    clearTimeout(closeTimer);
    closing = false;
    dlg.classList.remove("closing", "is-held", "vt");
    document.body.classList.remove("dlg-open");
    let back = trigger;
    if (!back || !document.contains(back)) { const c = openId && cardOf(openId); back = c && $(".read", c); }
    trigger = null;
    if (back) back.focus({ preventScroll: true });
    if (pending) { const d = pending; pending = null; render(d, false, false); }
  });

  /* ---------- header shadow ---------- */
  const onScroll = () => topbar.classList.toggle("scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ---------- boot & refresh ---------- */
  let pending = null, busy = false, last = 0;
  async function refresh(initial) {
    if (busy) return;
    busy = true;
    last = Date.now();
    try {
      const { data, sample } = await load(initial);
      if (initial) render(data, sample, true);
      else if (data && str(data.generated_at) !== stamp) {
        if (dlg.open || held) pending = data;
        else render(data, false, false);
      }
    } finally { busy = false; }
  }

  initTheme();
  initFlow();
  document.querySelectorAll("[data-loop]").forEach(watchLoop);
  refresh(true);
  setInterval(() => { if (document.visibilityState === "visible") refresh(false); }, 10 * 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - last >= 60000) refresh(false);
  });
})();
