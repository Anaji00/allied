'use client';

import { useState } from 'react';
import { NativeSelect } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { cn } from '@/lib/utils';
import { datesOf, isDateColumn } from './board-parts';
import type { Board, Item } from './use-boards';

const DAY = 86_400_000;
const at = (iso: string) => new Date(`${iso}T00:00:00`).getTime();
const label = (ms: number) => new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

/** A timeline: each item with a date or a date range as a bar across the weeks. View only; dates are changed in the table. */
export function BoardTimeline({ board, items, onSelect }: { board: Board; items: Item[]; onSelect: (item: Item) => void }) {
  const dateColumns = board.columns.filter((c) => isDateColumn(c, items));
  const [by, setBy] = useState(dateColumns[0]?.id ?? '');
  const [today] = useState(() => Date.now());
  const column = dateColumns.find((c) => c.id === by) ?? dateColumns[0];
  if (!column) return <EmptyState title="This board has no date column to lay out on a timeline" />;

  const rows = items
    .map((i) => ({ item: i, range: datesOf(i.values[column.id] ?? '') }))
    .filter((r): r is { item: Item; range: [string, string] } => Boolean(r.range))
    .sort((a, b) => a.range[0].localeCompare(b.range[0]));
  if (rows.length === 0) return <EmptyState title={`No items have a date in ${column.title}`} />;

  const start = Math.min(...rows.map((r) => at(r.range[0])));
  const end = Math.max(...rows.map((r) => at(r.range[1]))) + DAY;
  const span = Math.max(end - start, 7 * DAY);
  const ticks = Array.from(
    { length: Math.min(8, Math.ceil(span / (7 * DAY)) + 1) },
    (_, n) => start + (n * span) / Math.min(7, Math.ceil(span / (7 * DAY))),
  );

  return (
    <div className="grid gap-2">
      {dateColumns.length > 1 && (
        <NativeSelect aria-label="Date column" value={column.id} onChange={(e) => setBy(e.target.value)} className="w-auto text-xs">
          {dateColumns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </NativeSelect>
      )}
      <div className="overflow-x-auto rounded-lg border bg-card">
        <div className="min-w-[44rem]">
          <div className="relative ml-56 h-7 border-b text-[11px] text-muted-foreground">
            {ticks.map((t, n) => (
              <span key={n} className="absolute top-1.5" style={{ left: `${((t - start) / span) * 100}%` }}>
                {label(t)}
              </span>
            ))}
          </div>
          <ul>
            {rows.map(({ item, range }) => {
              const left = ((at(range[0]) - start) / span) * 100;
              const width = Math.max(((at(range[1]) + DAY - at(range[0])) / span) * 100, 1.5);
              return (
                <li key={item.id} className="flex items-center border-b last:border-0">
                  <button
                    type="button"
                    onClick={() => onSelect(item)}
                    className="w-56 shrink-0 truncate px-3 py-1.5 text-left text-[13px] hover:underline"
                    title={item.name}
                  >
                    {item.name}
                  </button>
                  <div className="relative h-7 flex-1">
                    {today >= start && today <= end && (
                      <span
                        className="absolute inset-y-0 w-px bg-bad/60"
                        style={{ left: `${((today - start) / span) * 100}%` }}
                        aria-hidden
                      />
                    )}
                    <span
                      className={cn('absolute top-1.5 h-4 rounded bg-primary/70')}
                      style={{ left: `${left}%`, width: `${width}%` }}
                      title={`${item.name}: ${range[0] === range[1] ? range[0] : `${range[0]} to ${range[1]}`}`}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}
