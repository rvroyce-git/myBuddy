/* MyBuddy · Concept B "News stories"
   Vanilla JS. No dependencies. Data is rendered with textContent only.
   Entry module: site settings, start-up order and loading the reports. The parts live in
   assets/js/ (ES modules, no build step):
     util.js      DOM helpers, safe storage, reduced motion, text clamping
     look.js      topic icons, short names, gradient angle, orb
     time.js      IST schedule -> the visitor's own time zone
     data.js      reports.json loading, validation and normalisation
     prefs.js     palette, theme (never saved), seen stories
     page.js      navbar, "How it works" tooltips, scroll reveal
     bubbles.js   story bubbles and the headline list
     deck.js      the card deck
     story.js     the story viewer
     qr.js        QR code encoder
     subscribe.js daily updates (topic chips, start link, QR) */

import { applySpring, $, doc, initClamp, clampAll } from './js/util.js';
import { localizeStatic, localStamp, istDay } from './js/time.js';
import { topics, setTopics, fetchReports, normalize, validDate, LOAD_TIMEOUT_MS } from './js/data.js';
import { initPalette, initTheme, loadSeen } from './js/prefs.js';
import { initPage } from './js/page.js';
import { initBubbles, buildBubbles, buildList } from './js/bubbles.js';
import { Deck, initDeck, bindDeckKeys } from './js/deck.js';
import { initStory } from './js/story.js';
import { Subs, initSubscribe } from './js/subscribe.js';

/* Colour palette: change the default here (and in the inline <head> script). Preview any
   palette with ?palette=NAME. Each name is a data-palette block in styles.css. */
const DEFAULT_PALETTE = 'periwinkle';
const PALETTES = ['honey', 'mint', 'periwinkle'];

/* The chat bot that sends the daily updates (see subscribe.js). */
const BOT_USERNAME = 'myBuddy_update_bot';
/* fallback when reports.json has no `bit` for a topic (same as config/topics.yaml) */
const TOPIC_BITS = {
  ai_news: 0, security: 1, github_trending: 2, deep_dive: 3, research_papers: 4, startups: 5, ai_models: 6,
};

/* Start-up, in this order (later parts register their listeners after earlier ones). */
applySpring();
initPalette(PALETTES, DEFAULT_PALETTE);
initTheme();
initPage();
localizeStatic();
initClamp();
initBubbles();
initDeck();
initStory();
bindDeckKeys();
initSubscribe({ botUsername: BOT_USERNAME, topicBits: TOPIC_BITS });

/* =====================================================================
   LOAD
   ===================================================================== */
const errBox = $('#loadErr');
let loading = false;
async function load() {
  if (loading) return; // Retry clicked twice: one load at a time
  loading = true;
  errBox.hidden = true;
  const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
  let timer = 0;
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => { if (ctrl) ctrl.abort(); reject(new Error('timeout')); }, LOAD_TIMEOUT_MS);
  });
  try {
    const raw = await Promise.race([fetchReports(ctrl ? ctrl.signal : undefined), timeout]);
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('bad data');
    const gen = validDate(raw.generated_at);
    setTopics(normalize(raw));
    if (!topics.length) throw new Error('no topics');
    if (gen) {
      $('#updatedText').textContent = 'Updated ' + localStamp(gen, true);
      $('#updated').hidden = false;
    }
    loadSeen(gen ? istDay(gen) : '');
    buildBubbles();
    Deck.build();
    buildList();
    Subs.build(topics);
    $('#subsInvite').textContent = raw.signup === 'open'
      ? 'Tap Start, pick topics, ask anything.'
      : 'Invite-only for now — tap Start to ask for access.';
    clampAll();
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(clampAll).catch(() => {});
  } catch (err) {
    errBox.hidden = false;
    doc.querySelectorAll('.card.skeleton .shimmer').forEach((s) => s.remove());
  } finally {
    clearTimeout(timer);
    loading = false;
  }
}
$('#retryBtn').addEventListener('click', load);
load();
