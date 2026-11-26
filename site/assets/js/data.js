/* Report data: loading, validation and normalisation (data is never trusted). */

import { own } from './util.js';
import { SHORT } from './look.js';
import { dueText, localStamp } from './time.js';

/* The normalised topics on the page (live binding; set by setTopics after each load). */
export let topics = [];
export function setTopics(list) { topics = list; }

/* Only ISO-like strings count as dates (new Date(null) would be 1970). */
export function validDate(iso) {
  if (typeof iso !== 'string' || !iso.trim() || iso.length > 64) return null;
  const d = new Date(iso);
  return isNaN(d) ? null : d;
}
/* Data text: strings (and finite numbers) only, trimmed and capped; anything else is ''. */
function txt(v, max) {
  let s = typeof v === 'string' ? v : (typeof v === 'number' && isFinite(v) ? String(v) : '');
  s = s.trim();
  return s.length > max ? s.slice(0, max - 1).trimEnd() + '…' : s;
}
/* Only absolute https: links are allowed (data contract). */
function safeUrl(u) {
  if (typeof u !== 'string' || u.length > 2048) return null;
  const s = u.trim();
  if (!/^https:\/\/[^/\\\s]/i.test(s)) return null;
  try {
    const x = new URL(s);
    return x.protocol === 'https:' && x.hostname ? x.href : null;
  } catch (e) { return null; }
}
/* The first `max` sentences. A sentence ends at . ! or ? (plus closing quotes/brackets)
   followed by a space or the end, so "v2.5" or "example.com" never split a sentence. */
function firstSentences(text, max) {
  const s = txt(text, 4000);
  if (!s) return '';
  const re = /[.!?]+["')\]]*(?=\s|$)/g;
  let m, k = 0, end = s.length;
  while ((m = re.exec(s))) {
    if (++k === max) { end = m.index + m[0].length; break; }
  }
  return txt(s.slice(0, end), 600);
}
const MAX_TOPICS = 60;
export function normalize(raw) {
  const list = Array.isArray(raw.topics) ? raw.topics : [];
  const used = new Set();
  const out = [];
  for (const t of list) {
    if (out.length >= MAX_TOPICS) break;
    if (!t || typeof t !== 'object') continue;
    const id = txt(t.id, 64);
    if (!id || used.has(id)) continue; // no id, or a duplicate: skip
    used.add(id);
    const k = out.length;
    const r = t.report && typeof t.report === 'object' && !Array.isArray(t.report) ? t.report : null;
    const d = r ? validDate(r.created_at) : null;
    const when = d ? localStamp(d) : dueText(txt(t.schedule, 80));
    const items = r && Array.isArray(r.items) ? r.items : [];
    const title = txt(t.title, 80) || id;
    const head = r ? txt(r.headline, 2000) : '';
    out.push({
      id, ang: 135 + (k % 5) * 15,
      bit: t.bit, // stable subscription bit (may be missing in older data)
      title,
      short: own(SHORT, id) || title,
      emoji: txt(t.emoji, 64),
      ready: !!head,
      headline: head || 'Coming soon',
      summary: r ? firstSentences(r.summary, 2) : '',
      stamp: r ? txt(r.created_at, 64) || 'x' : 'none',
      when,
      items: items.slice(0, 50).map((it) => ({
        url: safeUrl(it && it.url),
        title: txt(it && it.title, 200),
        source: txt(it && it.source, 60),
      })).filter((it) => it.url && it.title).slice(0, 3),
    });
  }
  return out;
}
/* "Headline. Open story" without doubling the headline's own full stop. */
export const withAction = (s, action) => s + (/[.!?…]["')\]]*$/.test(s) ? ' ' : '. ') + action;

/* data/reports.json, or the bundled sample when it is missing/unreachable (fresh install).
   A file that exists but is broken is an error, not a reason to show sample data. */
export const LOAD_TIMEOUT_MS = 15000; // a stalled request shows the error + Retry, not a forever skeleton
export async function fetchReports(signal) {
  const opts = { cache: 'no-cache', signal };
  let res;
  try { res = await fetch('data/reports.json', opts); } catch (e) { if (signal && signal.aborted) throw e; res = null; }
  if (!res || !res.ok) res = await fetch('data/reports.sample.json', opts);
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}
