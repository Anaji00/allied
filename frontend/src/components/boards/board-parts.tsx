'use client';

import Link from 'next/link';
import { Facts } from '@/components/data/detail-panel';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { milestoneState, type Milestone } from '@/components/projects/use-projects';
import { STATE as RFQ_STATE } from '@/components/rfqs/rfqs-view';
import { STATE as VENDOR_STATE } from '@/components/vendors/vendors-view';
import { day, words } from '@/lib/format';
import { cn } from '@/lib/utils';
import { ItemAutomations } from './board-automations';
import { ItemNotes } from './board-notes';
import type { Automations } from './use-automations';
import { TRACKED, inkOn, type AppView, type Board, type Column, type Item, type Role } from './use-boards';

/* ----------------------------------------------------------- column kinds */

const PEOPLE = new Set(['people', 'person', 'multiple-person']);
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const RANGE = /^(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})$/;
const NUMBER = /^-?\d+(\.\d+)?$/;

export const isStatus = (c: Column) => Boolean(c.labels);
export const isPeople = (c: Column) => PEOPLE.has(c.type);
export const isDateColumn = (c: Column, items: Item[]) =>
  c.type === 'date' || (c.type === 'formula' && items.some((i) => ISO.test(i.values[c.id] ?? '')));
export const isNumberColumn = (c: Column, items: Item[]) =>
  c.type === 'numbers' || (c.type === 'formula' && items.some((i) => NUMBER.test(i.values[c.id] ?? '')) && !isDateColumn(c, items));

/** The dates a value covers: one for a date, two for a timeline range. */
export function datesOf(value: string): [string, string] | null {
  if (ISO.test(value)) return [value, value];
  const r = RANGE.exec(value);
  return r ? [r[1], r[2]] : null;
}

export const peopleOf = (value: string) =>
  value
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean);

/* ------------------------------------------------------------------ cells */

/** One value the way monday shows it: status as a coloured label, people as faces, dates as dates. */
export function Cell({ column, value, wide = false }: { column: Column; value: string; wide?: boolean }) {
  if (!value) return null;
  if (column.labels) return <StatusLabel column={column} value={value} wide={wide} />;
  if (isPeople(column)) return <People names={peopleOf(value)} />;
  if (column.type === 'checkbox') return <span aria-label="Ticked">✓</span>;
  const dates = datesOf(value);
  if (dates)
    return (
      <span className="whitespace-nowrap">
        {day(dates[0])}
        {dates[1] !== dates[0] && ` – ${day(dates[1])}`}
      </span>
    );
  if (column.type === 'numbers' || (column.type === 'formula' && NUMBER.test(value)))
    return <span className="block text-right tabular-nums">{value}</span>;
  return (
    <span className={cn('block', !wide && 'max-w-80 truncate')} title={value}>
      {value}
    </span>
  );
}

export function StatusLabel({ column, value, wide = false }: { column: Column; value: string; wide?: boolean }) {
  const colour = column.labels?.[value];
  if (!colour) return <span className="whitespace-nowrap">{value}</span>;
  return (
    <span
      className={cn('inline-flex h-6 items-center justify-center rounded px-2 text-xs font-medium whitespace-nowrap', !wide && 'min-w-24')}
      style={{ background: colour, color: inkOn(colour) }}
    >
      {value}
    </span>
  );
}

const FACE_COLOURS = ['#579bfc', '#a25ddc', '#ff642e', '#00c875', '#e2445c', '#fdab3d', '#037f4c', '#66ccff'];

/** People as round initials, the way monday's People column shows them. */
export function People({ names, max = 3 }: { names: string[]; max?: number }) {
  const shown = names.slice(0, max);
  return (
    <span className="inline-flex items-center" title={names.join(', ')}>
      <span className="sr-only">{names.join(', ')}</span>
      {shown.map((n, i) => {
        const colour = FACE_COLOURS[[...n].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7) % FACE_COLOURS.length];
        const initials = n
          .split(/\s+/)
          .map((w) => w[0])
          .join('')
          .slice(0, 2)
          .toUpperCase();
        return (
          <span
            key={n}
            aria-hidden="true"
            className={cn('grid size-6 place-items-center rounded-full border-2 border-card text-[10px] font-semibold', i > 0 && '-ml-1.5')}
            style={{ background: colour, color: inkOn(colour) }}
          >
            {initials}
          </span>
        );
      })}
      {names.length > max && (
        <span aria-hidden="true" className="-ml-1.5 grid size-6 place-items-center rounded-full border-2 border-card bg-muted text-[10px]">
          +{names.length - max}
        </span>
      )}
    </span>
  );
}

/* -------------------------------------------------------------- summaries */

/**
 * What monday shows under a group for one column: a colour "battery" for a
 * status, the earliest-to-latest span for dates, a total for numbers.
 */
export function ColumnSummary({ column, items, board }: { column: Column; items: Item[]; board: Board }) {
  if (column.labels) {
    const counts = new Map<string, number>();
    for (const i of items) {
      const v = i.values[column.id] ?? '';
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    const total = items.length || 1;
    const parts = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    const describe = parts.map(([label, n]) => `${label || 'No status'}: ${n}`).join(', ');
    return (
      <span className="flex h-5 w-full min-w-24 overflow-hidden rounded-sm" role="img" aria-label={describe} title={describe}>
        {parts.map(([label, n]) => (
          <span key={label || '-'} style={{ width: `${(n / total) * 100}%`, background: column.labels?.[label] || 'var(--muted)' }} />
        ))}
      </span>
    );
  }
  if (isDateColumn(column, board.items) || column.type === 'timeline') {
    const all = items.flatMap((i) => datesOf(i.values[column.id] ?? '') ?? []).sort();
    if (!all.length) return null;
    return (
      <span className="block text-xs whitespace-nowrap text-muted-foreground">
        {day(all[0])}
        {all.at(-1) !== all[0] && ` – ${day(all.at(-1))}`}
      </span>
    );
  }
  if (isNumberColumn(column, board.items)) {
    const nums = items.map((i) => i.values[column.id]).filter((v): v is string => Boolean(v && NUMBER.test(v)));
    if (!nums.length) return null;
    const sum = nums.reduce((s, v) => s + Number(v), 0);
    return (
      <span className="block text-right text-xs text-muted-foreground tabular-nums" title="Sum">
        {Number.isInteger(sum) ? sum : sum.toFixed(2)}
      </span>
    );
  }
  return null;
}

/* ------------------------------------------------------------- the app */

export const TRACKED_ROLES = TRACKED;

export function describeApp(role: Role, item: Item, app: AppView): { label: string; tone: Tone; href: string } {
  if (role === 'projects' && !('job_id' in app) && !('rfq_id' in app)) {
    const s = milestoneState({ state: app.state, days: app.days ?? null } as Milestone);
    return { ...s, href: `/projects?milestone=${encodeURIComponent(item.id)}` };
  }
  if (role === 'vendors' && 'job_id' in app) {
    const s = VENDOR_STATE[app.state] ?? { label: words(app.state), tone: 'neutral' as Tone };
    return { ...s, href: `/vendors?job=${app.job_id}` };
  }
  if ('rfq_id' in app) {
    const s = (app.state && RFQ_STATE[app.state]) || { label: words(app.status), tone: 'neutral' as Tone };
    return { ...s, href: `/rfqs?id=${app.rfq_id}` };
  }
  return { label: 'Tracked', tone: 'neutral', href: '/' };
}

/** How the app reads a row, linking to its record there. */
export function InApp({ role, item, compact = false }: { role: Role; item: Item; compact?: boolean }) {
  const app = item.app;
  if (!app) return <span className="text-xs text-muted-foreground">Not tracked</span>;
  const { label, tone, href } = describeApp(role, item, app);
  return (
    <Link
      href={href}
      onClick={(e) => e.stopPropagation()}
      className="inline-flex items-center gap-1.5 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ToneBadge tone={tone}>{label}</ToneBadge>
      {!compact && <span className="text-xs text-muted-foreground underline-offset-2 hover:underline">Open</span>}
    </Link>
  );
}

/* --------------------------------------------------------------- item card */

/** Every field of one item, top to bottom, like monday's item card. */
/** Boards whose items take notes from management to the engineers: the Engineering board for now. */
export const NOTES_ON = new Set<Role>(['projects']);

export function ItemCard({ board, item, automations }: { board: Board; item: Item; automations?: Automations }) {
  const columns = board.columns.filter((c) => c.type !== 'name');
  const tracked = TRACKED_ROLES.includes(board.role);
  return (
    <div className="grid gap-4">
      {tracked && (
        <div className="grid gap-1">
          <span className="text-xs font-medium text-muted-foreground">In the app</span>
          <InApp role={board.role} item={item} />
        </div>
      )}
      {tracked && <ItemAutomations boardId={board.board_id} itemId={item.id} data={automations} />}
      {NOTES_ON.has(board.role) && <ItemNotes boardId={board.board_id} role={board.role} itemId={item.id} />}
      <Facts
        items={[
          ['Group', item.group_title || '–'],
          ...columns.map(
            (c) =>
              [c.title, item.values[c.id] ? <Cell key={c.id} column={c} value={item.values[c.id]} wide /> : '–'] as [
                string,
                React.ReactNode,
              ],
          ),
          ...(item.created_at ? ([['Created on monday', day(item.created_at)]] as [string, string][]) : []),
        ]}
      />
      <p className="text-xs text-muted-foreground">
        A copy of monday, read-only: change the item on monday and it shows here after the next read. The automations above act in the app
        (drafts, alerts, tracking) and never write to monday.
      </p>
    </div>
  );
}

/* ----------------------------------------------------- sorting & filtering */

export type Sort = { column: string; desc: boolean } | null;

/** Orders items by one column, empty values last, as monday's column sort does. */
export function sortItems(items: Item[], sort: Sort, board: Board): Item[] {
  if (!sort) return items;
  const column = board.columns.find((c) => c.id === sort.column);
  const value = (i: Item) => (sort.column === 'name' ? i.name : (i.values[sort.column] ?? ''));
  const numeric = column ? isNumberColumn(column, board.items) : false;
  const labelOrder = column?.labels ? Object.keys(column.labels) : null;
  const rank = (v: string) => (labelOrder ? labelOrder.indexOf(v) : 0);
  return [...items].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (!va || !vb) return !va && !vb ? 0 : va ? -1 : 1;
    const c = labelOrder ? rank(va) - rank(vb) : numeric ? Number(va) - Number(vb) : va.localeCompare(vb, undefined, { numeric: true });
    return sort.desc ? -c : c;
  });
}

export type Filters = { person: string; status: string; q: string };

export function filterItems(items: Item[], f: Filters, board: Board): Item[] {
  const people = board.columns.filter(isPeople);
  const needle = f.q.trim().toLowerCase();
  return items.filter(
    (i) =>
      (!f.person || people.some((c) => peopleOf(i.values[c.id] ?? '').includes(f.person))) &&
      (!f.status || (i.values[board.status_column] ?? '') === f.status) &&
      (!needle || [i.name, ...Object.values(i.values)].some((v) => v?.toLowerCase().includes(needle))),
  );
}

/** Everyone named in the board's People columns, for the person filter. */
export function peopleOnBoard(board: Board) {
  const names = new Set<string>();
  for (const c of board.columns.filter(isPeople)) for (const i of board.items) peopleOf(i.values[c.id] ?? '').forEach((n) => names.add(n));
  return [...names].sort();
}
