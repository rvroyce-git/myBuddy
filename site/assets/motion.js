/* MyBuddy · motion layer (progressive enhancement).
   Uses GSAP + ScrollTrigger + SplitText + Lenis when they load. If they don't, the page is already
   complete: this file then only turns on the CSS-only loops. (The hero intro is pure CSS.) */
(() => {
  "use strict";

  const root = document.documentElement;
  const $ = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));
  const MB = (window.MyBuddy = window.MyBuddy || {});
  const RM = window.matchMedia("(prefers-reduced-motion: reduce)");
  const FINE = window.matchMedia("(hover: hover) and (pointer: fine)");
  const NS = "http://www.w3.org/2000/svg";
  const full = !RM.matches;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const pad = (n) => String(n).padStart(2, "0");
  const onRender = (fn) => {
    document.addEventListener("mybuddy:render", (e) => fn(e.detail && e.detail.initial));
    if (MB.rendered) fn(true);
  };

  /* ---------- 1. Pause CSS loops while their section is off-screen ---------- */
  function initLoopPause() {
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => e.target.classList.toggle("off", !e.isIntersecting)), { rootMargin: "120px 0px" });
    $$("[data-loop]").forEach((n) => io.observe(n));
    const bento = $("#bento");
    if (bento) io.observe(bento);
  }

  /* ---------- 2. Cursor spotlight on cards (fine pointers only) ---------- */
  function initSpotlight() {
    const bento = $("#bento");
    if (!bento || !FINE.matches || !full) return;
    bento.addEventListener("pointermove", (e) => {
      const card = e.target.closest(".card");
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${(e.clientX - r.left).toFixed(0)}px`);
      card.style.setProperty("--my", `${(e.clientY - r.top).toFixed(0)}px`);
    }, { passive: true });
  }

  /* ---------- 3. Animated beams between pipeline nodes (geometry only; drawing state is CSS vars) ---------- */
  function initBeams() {
    const flow = $("#flow"), g = $("#flowPaths");
    if (!flow || !g) return;
    const desk = window.matchMedia("(min-width: 900px)");
    const pathD = (a, b, dir) => {
      const f = flow.getBoundingClientRect(), ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      if (dir === "h") {
        const x1 = ra.right - f.left + 6, y1 = ra.top + ra.height / 2 - f.top;
        const x2 = rb.left - f.left - 6, y2 = rb.top + rb.height / 2 - f.top + 0.01;
        const dx = (x2 - x1) * 0.55;
        return `M${x1.toFixed(1)} ${y1.toFixed(1)} C${(x1 + dx).toFixed(1)} ${y1.toFixed(1)} ${(x2 - dx).toFixed(1)} ${y2.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
      }
      const x1 = ra.left + ra.width / 2 - f.left, y1 = ra.bottom - f.top + 6;
      const x2 = rb.left + rb.width / 2 - f.left + 0.01, y2 = rb.top - f.top - 6;
      const dy = (y2 - y1) * 0.5;
      return `M${x1.toFixed(1)} ${y1.toFixed(1)} C${x1.toFixed(1)} ${(y1 + dy).toFixed(1)} ${x2.toFixed(1)} ${(y2 - dy).toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
    };
    const build = () => {
      const src = $$(".node-a", flow), server = $("#nodeServer"), ai = $("#nodeAI"), outs = $$(".node-d", flow);
      if (!server || !ai || !src.length || !outs.length) return;
      const pairs = desk.matches
        ? [...src.map((s) => [s, server, "h", 0]), [server, ai, "h", 1], ...outs.map((o) => [ai, o, "h", 2])]
        : [[src[src.length - 1], server, "v", 0], [server, ai, "v", 1], [ai, outs[0], "v", 2]];
      const frag = document.createDocumentFragment();
      pairs.forEach(([a, b, dir, stage], j) => {
        const grp = document.createElementNS(NS, "g");
        grp.setAttribute("class", `bp s${stage}`);
        grp.style.setProperty("--j", String(j));
        const d = pathD(a, b, dir);
        for (const cls of ["b-base", "b-draw", "b-glow", "b-pulse"]) {
          const p = document.createElementNS(NS, "path");
          p.setAttribute("d", d);
          p.setAttribute("class", cls);
          p.setAttribute("pathLength", "1");
          grp.append(p);
        }
        frag.append(grp);
      });
      g.replaceChildren(frag);
    };
    let raf = 0;
    const queue = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(build); };
    build();
    if ("ResizeObserver" in window) new ResizeObserver(queue).observe(flow);
    else addEventListener("resize", queue);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(queue);
  }

  initLoopPause();
  initSpotlight();
  initBeams();

  /* ---------- Load GSAP + Lenis after the page has painted ----------
     Fetched only after "load" (or 1.5 s at most) so they never compete with the CSS and hero fonts.
     async=false keeps execution order (GSAP before its plugins). Any failure → static fallback. */
  const CDN = [
    "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.15.1/gsap.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.15.1/ScrollTrigger.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.15.1/SplitText.min.js",
    "https://cdn.jsdelivr.net/npm/lenis@1.3.27/dist/lenis.min.js",
  ];
  const loadScript = (src) => new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = false;
    s.crossOrigin = "anonymous";
    s.onload = () => resolve(true);
    s.onerror = () => resolve(false);
    document.head.append(s);
  });
  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    Promise.all(CDN.map(loadScript)).then(initMotion, initMotion);
  };
  if (document.readyState === "complete") start();
  else { addEventListener("load", start, { once: true }); setTimeout(start, 1500); }

  // Only content still below the fold is hidden for a reveal; anything already seen stays put.
  const belowFold = (n) => n.getBoundingClientRect().top > innerHeight;

  function initMotion() {
    const gsap = window.gsap;
    const ST = window.ScrollTrigger;
    const Split = window.SplitText;

    /* ---------- No GSAP: show everything, run the CSS loops ---------- */
    if (!gsap || !ST) {
      const flow = $("#flow");
      if (flow) flow.classList.add("live");
      $$(".reveal").forEach((n) => n.classList.add("in"));
      $$(".slot").forEach((n) => n.classList.add("lit"));
      return;
    }
    gsap.registerPlugin(...[ST, Split].filter(Boolean));
    gsap.defaults({ ease: "power4.out", duration: 0.6 });

    /* ---------- 4. Lenis smooth wheel scrolling, synced with ScrollTrigger ---------- */
    let lenis = null;
    const easeOutQuart = (t) => 1 - Math.pow(1 - t, 4);
    if (window.Lenis && full) {
      try {
        lenis = new window.Lenis({ lerp: 0.1, smoothWheel: true, wheelMultiplier: 1 });
        lenis.on("scroll", ST.update);
        gsap.ticker.add((t) => lenis.raf(t * 1000));
        gsap.ticker.lagSmoothing(0);
        MB.lenis = lenis;
      } catch (err) { lenis = null; }
    }
    MB.scrollToEl = (node) => {
      const h = Math.min(node.offsetHeight, innerHeight - 140);
      const offset = -Math.max(90, (innerHeight - h) / 2);
      if (lenis) lenis.scrollTo(node, { offset, duration: 1.1, easing: easeOutQuart });
      else node.scrollIntoView({ behavior: full ? "smooth" : "auto", block: "center" });
    };
    if (lenis) {
      document.addEventListener("click", (e) => {
        const a = e.target.closest && e.target.closest('a[href^="#"]');
        if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey) return;
        const hash = a.getAttribute("href");
        if (!hash || hash === "#") return;
        let target = null;
        try { target = document.querySelector(hash); } catch { return; }
        if (!target) return;
        e.preventDefault();
        lenis.scrollTo(hash === "#top" ? 0 : target, { offset: -88, duration: 1.2, easing: easeOutQuart });
        try { history.pushState(null, "", hash); } catch { /* file:// */ }
        if (!target.matches("a, button, input, [tabindex]")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
      });
    }

    /* ---------- helpers ---------- */
    function splitWords(node) {
      if (!Split || !node) return null;
      try { return Split.create(node, { type: "words", wordsClass: "w" }); } catch { return null; }
    }
    /* ---------- 6. Hero depth: pointer parallax + gentle scroll drift ---------- */
    function heroDepth() {
      if (!full) return;
      const hero = $(".hero");
      if (hero && FINE.matches) {
        const qs = $$(".hv-par").map((p) => ({
          x: gsap.quickTo(p, "x", { duration: 1, ease: "power3.out" }),
          y: gsap.quickTo(p, "y", { duration: 1, ease: "power3.out" }),
          d: Number(p.dataset.depth) || 10,
        }));
        hero.addEventListener("pointermove", (e) => {
          const nx = e.clientX / innerWidth - 0.5, ny = e.clientY / innerHeight - 0.5;
          qs.forEach((q) => { q.x(nx * q.d * 1.6); q.y(ny * q.d); });
        }, { passive: true });
        hero.addEventListener("pointerleave", () => qs.forEach((q) => { q.x(0); q.y(0); }));
      }
      gsap.to(".hv", { yPercent: -10, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });
      gsap.to(".hero-copy", { y: -50, opacity: 0.35, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "60% top", scrub: true } });
    }

    /* ---------- Safety net for jumps ----------
       A jump (anchor, timeline click, End key) can skip past reveal triggers. When scrolling settles,
       anything at or above the viewport that is still hidden is finished in its visible state. */
    function revealSafety() {
      let t = 0;
      const settle = () => {
        const vh = innerHeight;
        $$("main .cell, main .sec-head > *, main .sec-head .w, .reveal, .countdown, .tl, .tabs-wrap, .card-hl .w").forEach((n) => {
          if (n.getBoundingClientRect().top >= vh) return;
          if (parseFloat(getComputedStyle(n).opacity) < 0.99 && !gsap.isTweening(n)) {
            n.classList.add("in");
            gsap.to(n, { opacity: 1, y: 0, yPercent: 0, filter: "blur(0px)", duration: 0.35, overwrite: true, clearProps: "transform,filter" });
          }
        });
      };
      const queue = () => { clearTimeout(t); t = setTimeout(settle, 220); };
      addEventListener("scroll", queue, { passive: true });
      addEventListener("hashchange", queue);
    }

    /* ---------- 7. Number tickers ---------- */
    function tickers() {
      const ticks = $$(".tick");
      const fmt = (n, v) => (n.dataset.fmt === "time" ? `${pad(Math.floor(v / 60))}:${pad(v % 60)}` : String(v));
      const stats = $(".stats");
      if (!full || !stats || !belowFold(stats)) return; // already on screen: keep the real numbers
      let seen = false, ready = !!MB.rendered;
      const run = () => {
        if (!seen || !ready) return;
        ticks.forEach((n) => {
          const to = Number(n.dataset.to) || 0;
          const from = n.dataset.from != null ? Number(n.dataset.from) : 0;
          const o = { v: from };
          n.textContent = fmt(n, from);
          gsap.to(o, { v: to, duration: n.dataset.fmt === "time" ? 1.6 : 1.2, ease: "power3.out", onUpdate: () => { n.textContent = fmt(n, Math.round(o.v)); } });
        });
        seen = false;
      };
      ticks.forEach((n) => { if (n.id !== "statStories") n.textContent = fmt(n, n.dataset.from != null ? Number(n.dataset.from) : 0); });
      ST.create({ trigger: ".stats", start: "top 92%", once: true, onEnter: () => { seen = true; run(); } });
      document.addEventListener("mybuddy:render", () => { ready = true; run(); });
    }

    /* ---------- 8. Section headings + generic reveals ---------- */
    function sectionReveals() {
      if (!full) return; // reduced motion: nothing is held back waiting for a scroll trigger
      $$(".sec-head").filter(belowFold).forEach((h) => {
        const title = $("[data-split]", h);
        const others = Array.from(h.children).filter((c) => c !== title && !c.classList.contains("countdown"));
        const tl = gsap.timeline({ scrollTrigger: { trigger: h, start: "top 86%", once: true } });
        if (full) {
          const s = splitWords(title);
          if (s) {
            gsap.set(s.words, { opacity: 0, yPercent: 50, filter: "blur(10px)" });
            tl.to(s.words, { opacity: 1, yPercent: 0, filter: "blur(0px)", duration: 1, stagger: 0.08, onComplete: () => s.revert() }, 0);
          }
          if (others.length) tl.from(others, { opacity: 0, y: 18, duration: 0.7, stagger: 0.08 }, 0.1);
        }
      });
      const reveals = $$(".reveal, .countdown, .tl, .tabs-wrap").filter(belowFold);
      gsap.set(reveals, { opacity: 0, y: 28 });
      ST.batch(reveals, {
        start: "top 90%", once: true,
        onEnter: (batch) => {
          batch.forEach((n) => n.classList.add("in"));
          gsap.to(batch, { opacity: 1, y: 0, duration: 0.8, stagger: 0.09, clearProps: "transform" });
        },
      });
    }

    /* ---------- 9. Bento cards: staggered entrance + headline blur-in + streaming summary ---------- */
    function cardReveal(cell) {
      if (!full) return;
      const hl = $(".card-hl", cell);
      const s = splitWords(hl);
      if (s) gsap.from(s.words, { opacity: 0, filter: "blur(8px)", y: 8, duration: 0.8, stagger: 0.04, delay: 0.12, ease: "power3.out", onComplete: () => s.revert() });
      const sum = $(".card.big .card-sum .short", cell) || $(".card.big .card-sum", cell);
      if (sum && Split) {
        try {
          const c = Split.create(sum, { type: "words,chars", charsClass: "ch" });
          gsap.from(c.chars, { opacity: 0, duration: 0.05, stagger: 0.009, delay: 0.45, ease: "none", onComplete: () => c.revert() });
        } catch { /* leave text as is */ }
      }
    }
    function bentoReveal(initial) {
      const all = $$("#bento .cell");
      if (!all.length) return;
      if (!initial) { gsap.set(all, { clearProps: "opacity,transform" }); ST.refresh(); return; }
      const cells = all.filter(belowFold);
      if (!cells.length) { requestAnimationFrame(() => ST.refresh()); return; }
      if (!full) { gsap.from(cells, { opacity: 0, duration: 0.4, stagger: 0.03, ease: "none" }); requestAnimationFrame(() => ST.refresh()); return; }
      gsap.set(cells, { opacity: 0, y: 36 });
      ST.batch(cells, {
        start: "top 92%", once: true,
        onEnter: (batch) => {
          gsap.to(batch, { opacity: 1, y: 0, duration: 0.9, stagger: 0.08, clearProps: "transform" });
          batch.forEach(cardReveal);
        },
      });
      requestAnimationFrame(() => ST.refresh());
    }

    /* ---------- 10. How it works: pinned, scrubbed sequence on desktop; play-once elsewhere ---------- */
    function howSequence() {
      const flow = $("#flow"), stage = $("#howStage");
      if (!flow || !stage) return;
      if (!full) { flow.classList.add("live"); return; }
      const groups = [$$(".node-a", flow), $$(".node-b", flow), $$(".node-c", flow), $$(".node-d", flow)];
      const txt = $$(".ftxt", flow);
      const build = () => {
        gsap.set(groups.flat(), { opacity: 0.45, scale: 0.97 });
        gsap.set(txt, { opacity: 0.35, y: 8 });
        gsap.set(flow, { "--d0": 0, "--d1": 0, "--d2": 0 });
        const tl = gsap.timeline({ defaults: { ease: "power2.inOut" } });
        groups.forEach((els, i) => {
          tl.to(els, {
            opacity: 1, scale: 1, duration: 0.5, stagger: 0.05,
            onStart: () => els.forEach((e) => e.classList.add("lit")),
            onReverseComplete: () => els.forEach((e) => e.classList.remove("lit")),
          });
          tl.to(txt[i], { opacity: 1, y: 0, duration: 0.5 }, "<");
          if (i < 3) tl.to(flow, { [`--d${i}`]: 1, duration: 0.8, ease: "none" });
        });
        return tl;
      };
      const mm = gsap.matchMedia();
      mm.add("(min-width: 1024px) and (min-height: 640px)", () => {
        const tl = build();
        ST.create({
          trigger: stage, start: "center center", end: "+=80%", pin: true, scrub: 0.5, animation: tl, refreshPriority: 1,
          onUpdate: (self) => flow.classList.toggle("live", self.progress > 0.95),
        });
        return () => { flow.classList.remove("live"); $$(".lit", flow).forEach((n) => n.classList.remove("lit")); };
      });
      mm.add("(max-width: 1023px), (max-height: 639px)", () => {
        const tl = build().pause();
        tl.eventCallback("onComplete", () => flow.classList.add("live"));
        ST.create({ trigger: flow, start: "top 85%", once: true, onEnter: () => tl.timeScale(4.4).play() });
        return () => { flow.classList.remove("live"); $$(".lit", flow).forEach((n) => n.classList.remove("lit")); };
      });
    }

    /* ---------- 11. Morning timeline: progress line fills on scroll, dots light up ---------- */
    function timeline() {
      const fill = $("#tlFill");
      if (!fill) return;
      if (!full) { $$(".slot").forEach((n) => n.classList.add("lit")); return; }
      gsap.fromTo(fill, { scaleY: 0 }, { scaleY: 1, ease: "none", scrollTrigger: { trigger: ".tl-rail", start: "top 72%", end: "bottom 58%", scrub: 0.5 } });
      $$(".slot").forEach((s) => ST.create({
        trigger: s, start: "top 68%",
        onEnter: () => s.classList.add("lit"),
        onLeaveBack: () => s.classList.remove("lit"),
      }));
      const big = $("#cdBig");
      if (big) gsap.from(big, { opacity: 0, filter: "blur(10px)", y: 10, duration: 1, scrollTrigger: { trigger: big, start: "top 90%", once: true } });
    }

    /* ---------- boot ---------- */
    // The pin is created first so every trigger below it is measured with the pin spacer in place.
    howSequence();
    heroDepth();
    tickers();
    sectionReveals();
    timeline();
    onRender(bentoReveal);
    ST.sort();
    if (full) revealSafety();
    // Re-measure every trigger (incl. the pin) whenever the page above it changes size: filters,
    // re-renders, late fonts. Debounced to one refresh per settled layout.
    let refreshT = 0;
    const refreshSoon = () => { clearTimeout(refreshT); refreshT = setTimeout(() => requestAnimationFrame(() => ST.refresh()), 60); };
    document.addEventListener("mybuddy:layout", refreshSoon);
    if ("ResizeObserver" in window) { const b = $("#bento"); if (b) new ResizeObserver(refreshSoon).observe(b); }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => ST.refresh());
    addEventListener("load", () => ST.refresh());
  }
})();
