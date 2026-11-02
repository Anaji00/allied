'use client';

import { useState } from 'react';
import { ArrowDown, ArrowUp, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { AutomationChip } from './board-automations';
import { NoteChip } from './board-notes';
import type { Note } from './use-notes';
import { Cell, ColumnSummary, InApp, TRACKED_ROLES, sortItems, type Sort } from './board-parts';
import type { Automations } from './use-automations';
import type { Board, Column, Group, Item } from './use-boards';

const width = (c: Column) => Math.min(Math.max(c.width ?? 140, 80), 320);

/** Groups in monday's order; items in a group the layout doesn't list go last. */
export function groupsOf(board: Board, items: Item[]): [Group, Item[]][] {
  const groups = [...board.groups];
  for (const i of items)
    if (i.group && !groups.some((g) => g.id === i.group)) groups.push({ id: i.group, title: i.group_title ?? i.group });
  return groups.map((g) => [g, items.filter((i) => i.group === g.id)] as [Group, Item[]]).filter(([, rows]) => rows.length > 0);
}

/** monday's Main table: groups with their colour, sortable columns, a summary row under each group. */
export function BoardTable({
  board,
  items,
  sort,
  onSort,
  selected,
  onSelect,
  automations,
  notes,
}: {
  board: Board;
  items: Item[];
  sort: Sort;
  onSort: (sort: Sort) => void;
  selected: string | null;
  onSelect: (item: Item) => void;
  automations?: Automations;
  /** Engineering notes on this board, for the unread chip. */
  notes?: Note[];
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const nameColumn = board.columns.find((c) => c.type === 'name');
  const columns = board.columns.filter((c) => c.type !== 'name');
  // A monday Doc's table has no item names; reference boards aren't tracked by the app.
  const showName = Boolean(nameColumn) || board.items.some((i) => i.name);
  const tracked = TRACKED_ROLES.includes(board.role);
  const span = columns.length + (showName ? 1 : 0) + (tracked ? 1 : 0);
  const grouped = groupsOf(board, items);
  const allCollapsed = grouped.every(([g]) => collapsed.has(g.id));

  const toggle = (id: string) =>
    setCollapsed((c) => {
      const next = new Set(c);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // Click once to sort up, again to sort down, a third time to go back to monday's order.
  const cycle = (id: string) => onSort(sort?.column !== id ? { column: id, desc: false } : sort.desc ? null : { column: id, desc: true });

  const header = (id: string, title: string, extra?: string, minWidth?: number) => {
    const active = sort?.column === id;
    return (
      <th
        key={id}
        scope="col"
        aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : 'none'}
        className={cn('border-b p-0 text-left text-xs font-medium whitespace-nowrap text-muted-foreground', extra)}
        style={minWidth ? { minWidth } : undefined}
      >
        <button type="button" onClick={() => cycle(id)} className="flex w-full items-center gap-1 px-3 py-2 hover:text-foreground">
          {title}
          {active && (sort.desc ? <ArrowDown className="size-3" aria-hidden /> : <ArrowUp className="size-3" aria-hidden />)}
        </button>
      </th>
    );
  };

  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-3 text-xs">
        <button
          type="button"
          className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(grouped.map(([g]) => g.id)))}
        >
          {allCollapsed ? 'Expand all groups' : 'Collapse all groups'}
        </button>
        {sort && (
          <button
            type="button"
            className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => onSort(null)}
          >
            Back to monday’s order
          </button>
        )}
      </div>
      <div className="overflow-hidden rounded-lg border bg-card">
        <div className="max-h-[calc(100svh-16rem)] overflow-auto">
          <table className="w-max min-w-full border-collapse text-[13px]" aria-label={board.name}>
            <thead className="sticky top-0 z-[2] bg-card">
              <tr>
                {showName && header('name', nameColumn?.title ?? 'Item', 'sticky left-0 z-[1] min-w-40 bg-card sm:min-w-64')}
                {columns.map((c) => header(c.id, c.title, undefined, width(c)))}
                {tracked && (
                  <th
                    scope="col"
                    className="border-b border-l px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted-foreground"
                  >
                    In the app
                  </th>
                )}
              </tr>
            </thead>
            {grouped.map(([g, groupItems]) => {
              const open = !collapsed.has(g.id);
              const rows = sortItems(groupItems, sort, board);
              return (
                <tbody key={g.id}>
                  <tr>
                    <th colSpan={span} scope="colgroup" className="sticky left-0 border-b bg-muted/40 p-0 text-left">
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={() => toggle(g.id)}
                        className="sticky left-0 flex items-center gap-2 px-2 py-1.5 text-[13px] font-semibold"
                        style={{ color: g.color ?? undefined }}
                      >
                        <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} aria-hidden />
                        {g.title}
                        <span className="font-normal text-muted-foreground tabular-nums">{groupItems.length}</span>
                      </button>
                    </th>
                  </tr>
                  {open &&
                    rows.map((item) => (
                      <tr
                        key={item.id}
                        tabIndex={0}
                        aria-current={selected === item.id || undefined}
                        onClick={() => onSelect(item)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && e.target === e.currentTarget) onSelect(item);
                        }}
                        className={cn(
                          'cursor-pointer border-b outline-none hover:bg-muted/40 focus-visible:bg-muted/60',
                          selected === item.id && 'bg-accent hover:bg-accent',
                        )}
                      >
                        {showName && (
                          <th
                            scope="row"
                            className="sticky left-0 z-[1] max-w-80 truncate border-l-4 bg-card px-3 py-1.5 text-left font-medium"
                            style={{ borderLeftColor: g.color ?? 'transparent' }}
                            title={item.name}
                          >
                            {item.name}
                          </th>
                        )}
                        {columns.map((c) => (
                          <td key={c.id} className="px-3 py-1.5 align-middle" style={{ minWidth: width(c) }}>
                            <Cell column={c} value={item.values[c.id] ?? ''} />
                          </td>
                        ))}
                        {tracked && (
                          <td className="border-l px-3 py-1.5 whitespace-nowrap">
                            <span className="flex items-center gap-2">
                              <InApp role={board.role} item={item} />
                              <AutomationChip data={automations} itemId={item.id} />
                              <NoteChip notes={notes} itemId={item.id} />
                            </span>
                          </td>
                        )}
                      </tr>
                    ))}
                  {/* Under each group, monday's summary of every column that has one. */}
                  <tr className="border-b-4 border-b-transparent">
                    {showName && (
                      <td
                        className="sticky left-0 z-[1] border-l-4 bg-card px-3 py-1.5 text-xs text-muted-foreground"
                        style={{ borderLeftColor: g.color ?? 'transparent' }}
                      >
                        {open ? '' : `${groupItems.length} hidden`}
                      </td>
                    )}
                    {columns.map((c) => (
                      <td key={c.id} className="bg-muted/20 px-3 py-1.5" style={{ minWidth: width(c) }}>
                        <ColumnSummary column={c} items={groupItems} board={board} />
                      </td>
                    ))}
                    {tracked && <td className="border-l bg-muted/20" />}
                  </tr>
                </tbody>
              );
            })}
          </table>
        </div>
      </div>
    </div>
  );
}
