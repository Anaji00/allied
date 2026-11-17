'use client';

import { ViewTransition, startTransition, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  createColumnHelper,
  tableFeatures,
  useTable,
  type CellData,
  type ColumnDef,
  type RowData,
  type TableFeatures,
} from '@tanstack/react-table';
import { ArrowDown, ArrowUp, ChevronsUpDown, ListFilter, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useFreshKeys } from './motion';

declare module '@tanstack/react-table' {
  // Per-column layout hints the table reads; nothing else uses ColumnMeta.
  interface ColumnMeta<in out TFeatures extends TableFeatures, in out TData extends RowData, TValue extends CellData = CellData> {
    align?: 'right';
    className?: string;
    /** Hide this column on screens narrower than this, so the table fits instead of scrolling sideways. */
    hideBelow?: 'md' | 'lg' | 'xl' | '2xl';
    /** A total for this column over the rows currently shown (after filtering): a sum, an average, a count. */
    footer?: (rows: TData[]) => ReactNode;
  }
}

// Server-side paging: the table only lays rows out, and sorts, filters and totals what it was given.
// Kept at module scope so the row model stays stable.
const HIDE = {
  md: 'hidden md:table-cell',
  lg: 'hidden lg:table-cell',
  xl: 'hidden xl:table-cell',
  '2xl': 'hidden 2xl:table-cell',
} as const;
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
  /** Turn off the per-column filter boxes (sorting and totals stay). */
  filterable?: boolean;
};

const PREFETCH_DELAY = 100;

/** A view-transition name for one record, safe as a CSS identifier. */
export const recordKey = (kind: string, id: string | number) => `${kind}-${String(id).replace(/[^A-Za-z0-9_-]/g, '_')}`;

const blank = (v: unknown) => v === null || v === undefined || v === '';
/** Numbers sort as numbers (even when they arrive as "1,200.50" text), everything else as words with numbers in order. */
function compare(a: unknown, b: unknown) {
  if (blank(a) || blank(b)) return blank(a) === blank(b) ? 0 : blank(a) ? 1 : -1;
  const na = typeof a === 'number' ? a : Number(String(a).replace(/,/g, ''));
  const nb = typeof b === 'number' ? b : Number(String(b).replace(/,/g, ''));
  if (Number.isFinite(na) && Number.isFinite(nb) && !/[a-z]/i.test(String(a)) && !/[a-z]/i.test(String(b))) return na - nb;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/**
 * A dense grid: 36px rows, sticky header, tabular figures. Click a heading to sort by it (again to reverse,
 * a third time to clear), open "Filter" to narrow any column by what it shows, and a totals row follows the filter.
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
  filterable = true,
}: Props<T>) {
  const table = useTable({
    features: tableFeatureSet,
    columns,
    data,
    getRowId: (row) => getRowId(row),
  });
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const prefetchTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [sort, setSort] = useState<{ id: string; desc: boolean } | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [showFilters, setShowFilters] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

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

  const baseRows = table.getRowModel().rows;
  const rows = useMemo(() => {
    if (!sort) return baseRows;
    const sign = sort.desc ? -1 : 1;
    // Blanks stay last either way round.
    return [...baseRows].sort((x, y) => {
      const a = x.getValue(sort.id);
      const b = y.getValue(sort.id);
      if (blank(a) || blank(b)) return compare(a, b);
      return sign * compare(a, b);
    });
  }, [baseRows, sort]);

  const hasSelection = rows.some((r) => r.id === selectedId);
  // Rows that arrived in a refresh get a short wash; on first load they settle in.
  const fresh = useFreshKeys(rows.map((r) => r.id));
  const [entering, setEntering] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setEntering(false), 400);
    return () => clearTimeout(t);
  }, []);

  const tickable = Boolean(checked && onCheckedChange);
  const offset = tickable ? 1 : 0;
  const active = Object.entries(filters).filter(([, v]) => v.trim());
  const filterKey = JSON.stringify(active);
  const rowKey = rows.map((r) => r.id).join('|');

  // A filter matches what the cell shows (a label, a date, a name), read from the page after it draws.
  useEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const cols = table.getAllLeafColumns();
    const next = new Set<string>();
    if (active.length) {
      body.querySelectorAll<HTMLTableRowElement>('tr[data-row-id]').forEach((tr) => {
        const ok = active.every(([id, text]) => {
          const i = cols.findIndex((c) => c.id === id);
          const shown = (tr.children[i + offset]?.textContent ?? '').toLowerCase();
          return text
            .toLowerCase()
            .split(/\s+/)
            .filter(Boolean)
            .every((w) => shown.includes(w));
        });
        if (!ok) next.add(tr.dataset.rowId!);
      });
    }
    setHidden((prev) => (prev.size === next.size && [...next].every((id) => prev.has(id)) ? prev : next));
    // `active` is read through filterKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, rowKey, offset]);

  const visible = rows.filter((r) => !hidden.has(r.id));
  const leaf = table.getAllLeafColumns();
  const hasFooter = leaf.some((c) => c.columnDef.meta?.footer);
  const sortable = (def: { accessorKey?: unknown; accessorFn?: unknown }) => Boolean(def.accessorKey || def.accessorFn);

  const allowed = visible.filter((r) => canCheck(r.original)).map((r) => r.id);
  const allTicked = allowed.length > 0 && allowed.every((id) => checked?.has(id));
  const flip = (id: string) => {
    const next = new Set(checked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onCheckedChange?.(next);
  };
  const cycle = (id: string) => setSort((s) => (!s || s.id !== id ? { id, desc: false } : s.desc ? null : { id, desc: true }));

  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      {filterable && rows.length > 1 && (
        <div className="flex items-center justify-end gap-2 border-b px-2 py-1 text-xs text-muted-foreground">
          {active.length > 0 && (
            <button type="button" className="inline-flex items-center gap-1 hover:text-foreground" onClick={() => setFilters({})}>
              <X className="size-3" /> Clear filters
            </button>
          )}
          <button
            type="button"
            aria-pressed={showFilters}
            onClick={() => setShowFilters((v) => !v)}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-muted hover:text-foreground',
              showFilters && 'bg-muted text-foreground',
            )}
          >
            <ListFilter className="size-3.5" /> Filter{active.length > 0 ? ` (${active.length})` : ''}
          </button>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px] max-xl:min-w-0!" style={{ minWidth }} aria-label={label}>
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
                  const canSort = sortable(header.column.columnDef as never);
                  const on = sort?.id === header.column.id ? sort : null;
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={on ? (on.desc ? 'descending' : 'ascending') : undefined}
                      className={cn(
                        'sticky top-0 z-[1] h-9 border-b bg-card px-3 text-left text-xs font-medium whitespace-nowrap text-muted-foreground',
                        meta?.align === 'right' && 'text-right',
                        meta?.hideBelow && HIDE[meta.hideBelow],
                        meta?.className,
                      )}
                    >
                      {header.isPlaceholder ? null : canSort ? (
                        <button
                          type="button"
                          onClick={() => cycle(header.column.id)}
                          className={cn('group inline-flex items-center gap-1 hover:text-foreground', on && 'text-foreground')}
                          title="Sort by this column"
                        >
                          <table.FlexRender header={header} />
                          {on ? (
                            on.desc ? (
                              <ArrowDown className="size-3" />
                            ) : (
                              <ArrowUp className="size-3" />
                            )
                          ) : (
                            <ChevronsUpDown className="size-3 opacity-0 transition-opacity group-hover:opacity-50 group-focus-visible:opacity-50" />
                          )}
                        </button>
                      ) : (
                        <table.FlexRender header={header} />
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
            {showFilters && (
              <tr>
                {tickable && <th className="border-b bg-card" />}
                {leaf.map((c) => (
                  <th
                    key={c.id}
                    className={cn(
                      'border-b bg-card px-2 py-1 font-normal',
                      c.columnDef.meta?.hideBelow && HIDE[c.columnDef.meta.hideBelow],
                    )}
                  >
                    {sortable(c.columnDef as never) ? (
                      <input
                        type="search"
                        value={filters[c.id] ?? ''}
                        onChange={(e) => setFilters((f) => ({ ...f, [c.id]: e.target.value }))}
                        aria-label={`Filter ${typeof c.columnDef.header === 'string' ? c.columnDef.header : c.id}`}
                        placeholder="Filter"
                        className="h-7 w-full min-w-16 rounded-md border bg-transparent px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      />
                    ) : null}
                  </th>
                ))}
              </tr>
            )}
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
                  data-row-id={row.id}
                  hidden={hidden.has(row.id)}
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
                            let next = (
                              e.key === 'ArrowDown' ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling
                            ) as HTMLElement | null;
                            // Skip rows a filter hides.
                            while (next?.hidden)
                              next = (e.key === 'ArrowDown' ? next.nextElementSibling : next.previousElementSibling) as HTMLElement | null;
                            next?.focus();
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
                        className={cn(
                          'h-9 px-3 whitespace-nowrap',
                          meta?.align === 'right' && 'text-right tabular-nums',
                          meta?.hideBelow && HIDE[meta.hideBelow],
                          meta?.className,
                        )}
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
          {hasFooter && visible.length > 0 && (
            <tfoot>
              <tr className="border-t bg-muted/30 text-xs font-medium">
                {tickable && <td />}
                {leaf.map((c, i) => {
                  const meta = c.columnDef.meta;
                  return (
                    <td
                      key={c.id}
                      className={cn(
                        'min-h-9 px-3 py-1.5',
                        meta?.align === 'right' && 'text-right tabular-nums',
                        meta?.hideBelow && HIDE[meta.hideBelow],
                      )}
                    >
                      {meta?.footer ? (
                        meta.footer(visible.map((r) => r.original))
                      ) : i === 0 ? (
                        <span className="text-muted-foreground">Totals · {visible.length} shown</span>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      {/* Outside the scrolling area, so the message stays in view on a phone. */}
      {rows.length === 0 && empty}
      {rows.length > 0 && visible.length === 0 && (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          Nothing matches those filters.{' '}
          <button type="button" className="underline" onClick={() => setFilters({})}>
            Clear them
          </button>
        </p>
      )}
    </div>
  );
}
