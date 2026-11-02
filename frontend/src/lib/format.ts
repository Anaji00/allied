// Formatting shared by the rebuilt screens. Money arrives from the API as exact
// decimal strings ("1234.50"); it is only turned into a number for display.

const moneyCache = new Map<string, Intl.NumberFormat>();

export function money(value: unknown, currency = 'USD') {
  const n = Number(value);
  if (value === null || value === undefined || value === '' || Number.isNaN(n)) return '–';
  let fmt = moneyCache.get(currency);
  if (!fmt) {
    fmt = new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 2 });
    moneyCache.set(currency, fmt);
  }
  return fmt.format(n);
}

export function pct(value: unknown, digits = 1) {
  const n = Number(value);
  return value === null || value === undefined || Number.isNaN(n) ? '–' : `${n.toFixed(digits)}%`;
}

/** "29 Oct, 14:05" in the viewer's time zone. */
export function dateTime(iso?: string | null) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '–';
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function date(iso?: string | null) {
  if (!iso) return '–';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '–' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** "8m", "3h", "Yesterday", or a date: how long ago, for list rows. */
export function ago(iso?: string | null, now = Date.now()) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const mins = Math.max(0, Math.round((now - t) / 60_000));
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  if (hours < 48) return 'Yesterday';
  return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/** SNAKE_CASE codes from the API, as words: "SAGE_ERROR" → "Sage error". */
export function words(code?: string | null) {
  if (!code) return '';
  const s = code.replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * A calendar date ("2026-11-12") as "Nov 12", with the year only when it is not
 * this year. Read as a local date: `new Date('2026-11-12')` is UTC midnight,
 * which shows as the day before anywhere west of Greenwich.
 */
export function day(value?: string | null, { weekday = false } = {}) {
  if (!value) return '–';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    weekday: weekday ? 'short' : undefined,
    month: 'short',
    day: 'numeric',
    year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
  });
}

/** A date as YYYY-MM-DD in local time (toISOString would give the UTC day). */
export function localIso(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Today as YYYY-MM-DD in local time, plus `days`. */
export function isoDay(days = 0) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return localIso(d);
}

/** "jane.doe@allied.com" as "Jane Doe"; the signed-in person as "You". */
export function person(email?: string | null, me?: string | null) {
  if (!email) return '';
  if (me && email.trim().toLowerCase() === me.trim().toLowerCase()) return 'You';
  const name = email
    .split('@')[0]
    .split(/[._-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
  return name || email;
}

/** "3 milestones", "1 milestone". */
export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** How long ago, to sit inside a sentence: "just now", "3h ago", "yesterday", "on Oct 28". */
export function since(iso?: string | null, now = Date.now()) {
  const short = ago(iso, now);
  if (!short) return '';
  if (short === 'now') return 'just now';
  if (short === 'Yesterday') return 'yesterday';
  return /^\d+[mh]$/.test(short) ? `${short} ago` : `on ${short}`;
}
