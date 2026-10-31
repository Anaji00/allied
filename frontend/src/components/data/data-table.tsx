'use client';

import { useEffect, useRef, type ReactNode } from 'react';
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
};

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
}: Props<T>) {
  const table = useTable({
    features: tableFeatureSet,
    columns,
    data,
    getRowId: (row) => getRowId(row),
  });
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  // Keep the selected row in view as J/K move through a long list.
  useEffect(() => {
    if (!selectedId) return;
    bodyRef.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  const rows = table.getRowModel().rows;
  const hasSelection = rows.some((r) => r.id === selectedId);

  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full border-collapse text-[13px]" style={{ minWidth }} aria-label={label}>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
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
        <tbody ref={bodyRef}>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          ) : (
            rows.map((row, i) => {
              const selected = row.id === selectedId;
              // One row is in the Tab order (the selected one, else the first);
              // the arrow keys move between rows and Enter opens one.
              const tabStop = hasSelection ? selected : i === 0;
              return (
                <tr
                  key={row.id}
                  aria-current={selected || undefined}
                  tabIndex={onSelect ? (tabStop ? 0 : -1) : undefined}
                  onClick={() => onSelect?.(row.original)}
                  onKeyDown={
                    onSelect
                      ? (e) => {
                          if (e.target !== e.currentTarget) return;
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            onSelect(row.original);
                          } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                            // Moves focus only; the page's own J/K would move the selection too.
                            e.preventDefault();
                            e.stopPropagation();
                            const next = e.key === 'ArrowDown' ? e.currentTarget.nextElementSibling : e.currentTarget.previousElementSibling;
                            (next as HTMLElement | null)?.focus();
                          }
                        }
                      : undefined
                  }
                  className={cn(
                    'border-b last:border-b-0',
                    onSelect && 'cursor-pointer outline-none hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:shadow-[inset_2px_0_0_var(--ring)]',
                    selected && 'bg-accent hover:bg-accent',
                  )}
                >
                  {row.getAllCells().map((cell) => {
                    const meta = cell.column.columnDef.meta;
                    return (
                      <td
                        key={cell.id}
                        className={cn(
                          'h-9 px-3 whitespace-nowrap',
                          meta?.align === 'right' && 'text-right tabular-nums',
                          meta?.className,
                        )}
                      >
                        <table.FlexRender cell={cell} />
                      </td>
                    );
                  })}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
