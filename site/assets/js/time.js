/* Times: the schedule is fixed in IST, shown in the visitor's own zone. */

import { $, doc } from './util.js';

/* ---------- time ----------
   The schedule is fixed in IST, but every time is shown in the visitor's own time zone,
   without a zone label. Words stay English (the page is English); only the 12/24-hour
   preference comes from the visitor's locale. IST is only used internally. */
const IST_OFFSET_MS = 330 * 60 * 1000; // UTC+05:30, no daylight saving
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
/* Day keys are computed by hand: no dependence on a locale's date format (en-CA has changed
   between ICU versions) or on the browser's time zone data. */
export const istDay = (d) => new Date(d.getTime() + IST_OFFSET_MS).toISOString().slice(0, 10);
const dayNum = (key) => Date.UTC(+key.slice(0, 4), +key.slice(5, 7) - 1, +key.slice(8, 10)) / 864e5;
const dayKey = (n) => new Date(n * 864e5).toISOString().slice(0, 10);
const localDayNum = (d) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 864e5;
const localGap = (d) => localDayNum(d) - localDayNum(new Date());
/* "5:00 PM" today, "Yesterday · 5:00 PM" or "Nov 22 · 5:00 PM" (visitor's zone).
   `mid` lowercases the word for use inside a phrase ("Updated yesterday · …"). */
export function localStamp(d, mid) {
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
  const d = IST_AT(istDay(new Date()), m[1], m[2]);
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
export function dueText(schedule) {
  const m = /(\d{1,2}):(\d{2})/.exec(String(schedule || ''));
  if (!m) return '';
  const days = scheduleDays(String(schedule).split('·')[0]);
  const now = Date.now(), base = dayNum(istDay(new Date()));
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
export function localizeStatic() {
  const ready = istClock('08:15');
  if (ready) $('#readyBy').textContent = `Ready by ${ready}.`;
  doc.querySelectorAll('[data-ist]').forEach((n) => { const t = istClock(n.dataset.ist); if (t) n.textContent = t; });
}
