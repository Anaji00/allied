'use client';

import { ViewTransition, startTransition, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  createColumnHelper,
  tableFeatures,
  useTable,
  type CellData,
  type ColumnDef,
  type RowData,
  type TableFeatures,
} from '@tanstack/react-table';
import { cn } from '@/lib/utils';
import { useFreshKeys } from './motion';

declare module '@tanstack/react-table' {
  // Per-column layout hints the table reads; nothing else uses ColumnMeta.
  interface ColumnMeta<in out TFeatures extends TableFeatures, in out TData extends RowData, TValue extends CellData = CellData> {
    align?: 'right';
    className?: string;
  }
}

// Server-side paging and filtering: the table only lays rows out, so it needs
// no optional features. Kept at module scope so the row model stays stable.
export const tableFeatureSet = tableFeatures({});
export type TableFeatureSet = typeof tableFeatureSet;
export const columnHelper = <T extends RowData>() => createColumnHelper<TableFeatureSet, T>();

type Props<T extends RowData> = {
  // Same shape columnHelper.columns() returns: each column keeps its own value type.
  columns: ColumnDef<TableFeatureSet, T, any>[];
  data: T[];
  getRowId: (row: T) => string;
  selectedId?: string | null;
  onSelect?: (row: T) => void;
  label: string;
  empty?: ReactNode;
  minWidth?: number;
  /**
   * Names the first cell for the record morph: the same name on the open
   * DetailPanel's heading makes it travel from the row into the panel.
   */
  recordName?: (row: T) => string;
  /** Called once the pointer rests on a row (or it gets focus), to load its details early. */
  onPrefetch?: (row: T) => void;
  /** Rows ticked for a group action. Give both props to show tick boxes; rows `canCheck` refuses get none. */
  checked?: Set<string>;
  onCheckedChange?: (ids: Set<string>) => void;
  canCheck?: (row: T) => boolean;
};

const PREFETCH_DELAY = 100;

/** A view-transition name for one record, safe as a CSS identifier. */
export const recordKey = (kind: string, id: string | number) => `${kind}-${String(id).replace(/[^A-Za-z0-9_-]/g, '_')}`;

/**
 * A dense, read-only grid: 36px rows, sticky header, tabular figures.
 * Clicking a row selects it; the page decides what selecting means.
 */
export function DataTable<T extends RowData>({
  columns,
  data,
  getRowId,
  selectedId,
  onSelect,
  label,
  empty,
  minWidth = 720,
  recordName,
  onPrefetch,
  checked,
  onCheckedChange,
  canCheck = () => true,
}: Props<T>) {
  const table = useTable({
    features: tableFeatureSet,
    columns,
    data,
    getRowId: (row) => getRowId(row),
  });
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const prefetchTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Selecting is a transition, so the record morph and panel slide can run.
  const select = (row: T) => onSelect && startTransition(() => onSelect(row));
  const prefetchSoon = (row: T) => {
    if (!onPrefetch) return;
    clearTimeout(prefetchTimer.current);
    prefetchTimer.current = setTimeout(() => onPrefetch(row), PREFETCH_DELAY);
  };
  const cancelPrefetch = () => clearTimeout(prefetchTimer.current);
  useEffect(() => cancelPrefetch, []);

  // Keep the selected row in view as J/K move through a long list.
  useEffect(() => {
    if (!selectedId) return;
    bodyRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  const rows = table.getRowModel().rows;
  const hasSelection = rows.some((r) => r.id === selectedId);
  // Rows that arrived in a refresh get a short wash; on first load they settle in.
  const fresh = useFreshKeys(rows.map((r) => r.id));
  const [entering, setEntering] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setEntering(false), 400);
    return () => clearTimeout(t);
  }, []);

  const tickable = Boolean(checked && onCheckedChange);
  const allowed = rows.filter((r) => canCheck(r.original)).map((r) => r.id);
  const allTicked = allowed.length > 0 && allowed.every((id) => checked?.has(id));
  const flip = (id: string) => {
    const next = new Set(checked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onCheckedChange?.(next);
  };

  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]" style={{ minWidth }} aria-label={label}>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {tickable && (
                  <th scope="col" className="sticky top-0 z-[1] h-9 w-9 border-b bg-card pl-3">
                    {allowed.length > 0 && (
                      <input
                        type="checkbox"
                        aria-label="Tick all"
                        checked={allTicked}
                        onChange={() => onCheckedChange?.(allTicked ? new Set() : new Set(allowed))}
                        className="size-4 accent-foreground"
                      />
                    )}
                  </th>
                )}
                {group.headers.map((header) => {
                  const meta = header.column.columnDef.meta;
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      className={cn(
                        'sticky top-0 z-[1] h-9 border-b bg-card px-3 text-left text-xs font-medium whitespace-nowrap text-muted-foreground',
                        meta?.align === 'right' && 'text-right',
                        meta?.className,
                      )}
                    >
                      {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody ref={bodyRef} className={cn(entering && 'rows-enter')}>
            {rows.map((row, i) => {
              const selected = row.id === selectedId;
              // One row is in the Tab order (the selected one, else the first);
              // the arrow keys move between rows and Enter opens one.
              const tabStop = hasSelection ? selected : i === 0;
              return (
                <tr
                  key={row.id}
                  aria-current={selected || undefined}
                  tabIndex={onSelect ? (tabStop ? 0 : -1) : undefined}
                  onClick={() => select(row.original)}
                  onPointerEnter={() => prefetchSoon(row.original)}
                  onPointerLeave={cancelPrefetch}
                  onFocus={() => prefetchSoon(row.original)}
                  onKeyDown={
                    onSelect
                      ? (e) => {
                          if (e.target !== e.currentTarget) return;
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            select(row.original);
                          } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                            // Moves focus only; the page's own J/K would move the selection too.
                            e.preventDefault();
                            e.stopPropagation();
                            const next =
                              e.key === 'ArrowDown' ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling;
                            (next as HTMLElement | null)?.focus();
                          }
                        }
                      : undefined
                  }
                  className={cn(
                    'border-b last:border-b-0',
                    fresh.has(row.id) && 'row-fresh',
                    onSelect &&
                      'cursor-pointer outline-none hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:shadow-[inset_2px_0_0_var(--ring)]',
                    selected && 'bg-accent hover:bg-accent',
                  )}
                >
                  {tickable && (
                    <td className="h-9 w-9 pl-3" onClick={(e) => e.stopPropagation()}>
                      {canCheck(row.original) && (
                        <input
                          type="checkbox"
                          aria-label={`Tick row ${row.id}`}
                          checked={checked?.has(row.id) ?? false}
                          onChange={() => flip(row.id)}
                          className="size-4 accent-foreground"
                        />
                      )}
                    </td>
                  )}
                  {row.getAllCells().map((cell, c) => {
                    const meta = cell.column.columnDef.meta;
                    // The open record's name moves to the panel heading, so the row drops it.
                    const morph = c === 0 && recordName && !selected ? recordName(row.original) : undefined;
                    return (
                      <td
                        key={cell.id}
                        className={cn('h-9 px-3 whitespace-nowrap', meta?.align === 'right' && 'text-right tabular-nums', meta?.className)}
                      >
                        {morph ? (
                          <ViewTransition name={morph} share="record" default="none">
                            <span className="inline-block max-w-full">
                              <table.FlexRender cell={cell} />
                            </span>
                          </ViewTransition>
                        ) : (
                          <table.FlexRender cell={cell} />
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {/* Outside the scrolling area, so the message stays in view on a phone. */}
      {rows.length === 0 && empty}
    </div>
  );
}
