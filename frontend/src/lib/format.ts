// Formatting shared by the rebuilt screens. Money arrives from the API as exact
// decimal strings ("1234.50"); it is only turned into a number for display.

/** Dates are shown in the business's own time zone, so everyone sees the same day. */
const BUSINESS_TZ = 'America/Chicago';

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

/** An amount with its currency named when it is not US dollars: "CN¥54,242.28 (Chinese Yuan)". Nothing is converted. */
export function moneyNamed(value: unknown, currency = 'USD') {
  const text = money(value, currency);
  if (text === '–' || currency === 'USD') return text;
  // One code per amount ("CNY 5,659.16"), so mixed currencies are never mistaken for dollars.
  const n = Number(value);
  return `${currency} ${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: BUSINESS_TZ });
}

export function date(iso?: string | null) {
  if (!iso) return '–';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '–'
    : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', timeZone: BUSINESS_TZ });
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
  return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: BUSINESS_TZ });
}

/** SNAKE_CASE codes from the API, as words: "SAGE_ERROR" → "Sage error". */
export function words(code?: string | null) {
  if (!code) return '';
  const s = code.replace(/_/g, ' ').toLowerCase();
  return (s.charAt(0).toUpperCase() + s.slice(1))
    .replace(/\bRfq\b/g, 'RFQ')
    .replace(/\bPo\b/g, 'PO')
    .replace(/\bSku\b/g, 'part number');
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

const KNOWN_CODES: Record<string, string> = {
  VENDOR_CHECKIN: 'Check in with the vendor',
  AUTOPILOT_DRAFTS: 'Drafts waiting for you',
  BOARD_ROW: 'Add to the monday board',
  RFQ_OVERDUE: 'RFQ overdue',
  VENDOR_LATE: 'Vendor is late',
  SHIPMENT_FREE_TIME: 'Port free time is running out',
  UNMATCHED_SKU: 'Part not matched',
  INVOICE_COST_VARIANCE: 'Bill costs more than expected',
  STALE_VENDOR_PRICE: 'Vendor price is old',
  VENDOR_NOACK: 'Vendor hasn’t confirmed',
  vendor_noack: 'Vendor hasn’t confirmed',
  DISCREPANCY_FLAGGED: 'Needs a check',
  DUPLICATE_INVOICE: 'Already entered',
  NEW: 'New',
  SYNCED_TO_SAGE: 'Not sent to Sage yet',
  VALIDATION_ERROR: 'Needs a check',
  SAGE_ERROR: 'Sage problem',
  no_ack: 'PO not confirmed',
  will_miss: 'Will miss its date',
  at_risk: 'At risk',
};

/** Turns a raw code inside a line of text (SYNCED_TO_SAGE, no_ack, NEW) into plain words. Ordinary words are left alone. */
/** A raw code as a short plain phrase. */
export function kindWord(code?: string | null) {
  if (!code) return '';
  return KNOWN_CODES[code] ?? KNOWN_CODES[code.toUpperCase()] ?? words(code);
}

export function plain(text?: string | null) {
  if (!text) return '';
  // A full date such as 2026-11-08 reads "Nov 8".
  text = text.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (m, y, mo, d) => {
    const dt = new Date(Number(y), Number(mo) - 1, Number(d));
    return Number.isNaN(dt.getTime()) ? m : dt.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
  });
  if (/^[A-Z][A-Z0-9]*$/.test(text)) return KNOWN_CODES[text] ?? words(text);
  // A code standing alone between dots ("NEW · E2E test", "SYNCED_TO_SAGE · CAD 1640.00").
  text = text.replace(
    /(^|·\s*)([A-Z][A-Z0-9_]{2,})(?=\s*(·|$))/g,
    (m, lead, code) => `${lead}${KNOWN_CODES[code] ?? (/^(CAD|USD|CNY|EUR|GBP|JPY|RFQ|PO|SO)$/.test(code) ? code : words(code))}`,
  );
  return text.replace(/\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b/g, (m) => KNOWN_CODES[m] ?? words(m));
}

/** monday's "simulation" message in plain words. */
export function practice(text?: string | null) {
  if (!text) return '';
  return /simulat|practice/i.test(text) ? 'Practice mode: the app is not connected to monday, so nothing is read.' : text;
}
