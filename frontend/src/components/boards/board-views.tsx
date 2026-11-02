'use client';

import { useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { NativeSelect } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { localIso as iso } from '@/lib/format';
import { cn } from '@/lib/utils';
import { Cell, InApp, People, TRACKED_ROLES, datesOf, isDateColumn, isPeople, peopleOf } from './board-parts';
import { AutomationChip } from './board-automations';
import { NoteChip } from './board-notes';
import type { Note } from './use-notes';
import type { Automations } from './use-automations';
import { inkOn, type Board, type Column, type Item } from './use-boards';

/* ----------------------------------------------------------------- kanban */

/** monday's Kanban: a lane per status label, in monday's label order and colours. Read-only, so no dragging. */
export function BoardKanban({
  board,
  items,
  onSelect,
  automations,
  notes,
}: {
  board: Board;
  items: Item[];
  onSelect: (item: Item) => void;
  automations?: Automations;
  /** Engineering notes on this board, for the unread chip. */
  notes?: Note[];
}) {
  const statusColumns = board.columns.filter((c) => c.labels);
  const [by, setBy] = useState(board.status_column);
  const column = statusColumns.find((c) => c.id === by) ?? statusColumns[0];
  if (!column) return <EmptyState title="This board has no status column to lay out as cards" />;

  const labels = Object.keys(column.labels ?? {});
  // Labels removed from the column's settings can still be on items; they get a lane too, after monday's.
  const stray = [...new Set(items.map((i) => i.values[column.id] ?? ''))].filter((l) => l && !labels.includes(l));
  const lanes = [...labels, ...stray, ''].map((label) => [label, items.filter((i) => (i.values[column.id] ?? '') === label)] as const);
  const people = board.columns.find(isPeople);
  const date = board.columns.find((c) => isDateColumn(c, board.items));
  const tracked = TRACKED_ROLES.includes(board.role);

  return (
    <div className="grid gap-2">
      {statusColumns.length > 1 && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Cards by
          <NativeSelect value={column.id} onChange={(e) => setBy(e.target.value)} className="h-7 w-auto">
            {statusColumns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </NativeSelect>
        </label>
      )}
      <div className="flex max-h-[calc(100svh-16rem)] items-start gap-3 overflow-auto pb-2">
        {lanes
          .filter(([label, rows]) => rows.length > 0 || label)
          .map(([label, rows]) => {
            const colour = column.labels?.[label] || '#c4c4c4';
            return (
              <section key={label || '-'} aria-label={label || 'No status'} className="grid w-64 shrink-0 gap-2 rounded-lg bg-muted/40 p-2">
                <h3
                  className="flex items-center justify-between rounded px-2 py-1 text-xs font-semibold"
                  style={{ background: colour, color: inkOn(colour) }}
                >
                  {label || 'No status'}
                  <span className="tabular-nums">{rows.length}</span>
                </h3>
                {rows.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onSelect(item)}
                    className="grid gap-1.5 rounded-md border bg-card p-2.5 text-left text-[13px] shadow-xs hover:border-foreground/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <span className="font-medium text-pretty">{item.name || '(no name)'}</span>
                    <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      {item.group_title && <span>{item.group_title}</span>}
                      {date && item.values[date.id] && <Cell column={date} value={item.values[date.id]} />}
                    </span>
                    {(people && item.values[people.id]) ||
                    (tracked && (item.app || automations?.due[item.id] || automations?.drafts[item.id])) ? (
                      <span className="flex items-center justify-between gap-2">
                        {people && item.values[people.id] ? <People names={peopleOf(item.values[people.id])} /> : <span />}
                        <span className="flex items-center gap-1.5">
                          <AutomationChip data={automations} itemId={item.id} />
                          <NoteChip notes={notes} itemId={item.id} />
                          {tracked && item.app && <InApp role={board.role} item={item} compact />}
                        </span>
                      </span>
                    ) : null}
                  </button>
                ))}
              </section>
            );
          })}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- calendar */

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** monday's Calendar view: items on their date (or across their timeline), a month at a time. */
export function BoardCalendar({ board, items, onSelect }: { board: Board; items: Item[]; onSelect: (item: Item) => void }) {
  const dateColumns = board.columns.filter((c) => isDateColumn(c, board.items) || c.type === 'timeline');
  const [by, setBy] = useState(dateColumns[0]?.id ?? '');
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const column = dateColumns.find((c) => c.id === by) ?? dateColumns[0];
  if (!column) return <EmptyState title="This board has no date column to put on a calendar" />;

  const status: Column | undefined = board.columns.find((c) => c.id === board.status_column && c.labels);
  // Monday-first weeks covering the month.
  const first = new Date(month);
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));
  const days = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
  const placed = items
    .map((i) => [i, datesOf(i.values[column.id] ?? '')] as const)
    .filter((x): x is readonly [Item, [string, string]] => x[1] !== null);
  const today = iso(new Date());
  const inMonth = placed.filter(([, [a, b]]) => b >= iso(days[0]) && a <= iso(days[41])).length;

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Previous month"
          onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
        >
          <ChevronLeft />
        </Button>
        <h3 className="min-w-36 text-center text-sm font-semibold">
          {month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
        </h3>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Next month"
          onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
        >
          <ChevronRight />
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))}>
          Today
        </Button>
        {dateColumns.length > 1 && (
          <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            Dates from
            <NativeSelect value={column.id} onChange={(e) => setBy(e.target.value)} className="h-7 w-auto">
              {dateColumns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </NativeSelect>
          </label>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {inMonth} item{inMonth === 1 ? '' : 's'} on these weeks · {items.length - placed.length} without a {column.title.toLowerCase()}
      </p>
      <div className="overflow-x-auto rounded-lg border bg-card">
        <div className="grid min-w-[44rem] grid-cols-7">
          {WEEKDAYS.map((w) => (
            <div key={w} className="border-b px-2 py-1.5 text-xs font-medium text-muted-foreground">
              {w}
            </div>
          ))}
          {days.map((d) => {
            const key = iso(d);
            const here = placed.filter(([, [a, b]]) => a <= key && key <= b);
            return (
              <div
                key={key}
                className={cn(
                  'min-h-24 border-r border-b p-1 last:border-r-0 [&:nth-child(7n)]:border-r-0',
                  d.getMonth() !== month.getMonth() && 'bg-muted/30',
                )}
              >
                <div className={cn('mb-1 px-1 text-xs tabular-nums', key === today ? 'font-semibold text-bad' : 'text-muted-foreground')}>
                  {d.getDate()}
                </div>
                <div className="grid gap-0.5">
                  {here.slice(0, 4).map(([item]) => {
                    const colour = (status && status.labels?.[item.values[status.id] ?? '']) || '#579bfc';
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => onSelect(item)}
                        title={item.name}
                        className="truncate rounded px-1.5 py-0.5 text-left text-[11px] font-medium focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                        style={{ background: colour, color: inkOn(colour) }}
                      >
                        {item.name || '(no name)'}
                      </button>
                    );
                  })}
                  {here.length > 4 && <span className="px-1 text-[11px] text-muted-foreground">+{here.length - 4} more</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
