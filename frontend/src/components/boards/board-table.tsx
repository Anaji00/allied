'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowDown, ArrowUp, ChevronRight, Calculator, MoreHorizontal, PanelRight } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { toast } from 'sonner';
import { authFetch } from '@/lib/api';
import { PutOnMonday } from '@/components/data/put-on-monday';
import { cn } from '@/lib/utils';
import { AutomationChip } from './board-automations';
import { NoteChip } from './board-notes';
import type { Note } from './use-notes';
import { Cell, ColumnSummary, InApp, TRACKED_ROLES, sortItems, type Sort } from './board-parts';
import type { Automations } from './use-automations';
import type { Board, Column, Group, Item } from './use-boards';
import { AddColumn } from './add-column';
import { CellEditor, type Move } from './cell-editor';
import { PasteBlock } from './paste-block';
import { EDITABLE_TYPES, useCellDrag } from './use-cell-drag';
import { cellKey, idKey, useStaged } from './use-staged';

// Columns monday fills in by itself: shown locked, with the reason, when someone tries to type in them.
const LOCKED = new Set([
  'formula',
  'mirror',
  'lookup',
  'auto_number',
  'creation_log',
  'last_updated',
  'board_relation',
  'subtasks',
  'dependency',
]);

const width = (c: Column) => Math.min(Math.max(c.width ?? 140, 80), 320);

/** Groups in monday's order; items in a group the layout doesn't list go last. */
/** The rows laid out by customer family (Owens Corning, CertainTeed...), from the app's own list of company names. */
export function groupByFamily(items: Item[]): [Group, Item[]][] {
  const names = [...new Set(items.map((i) => i.family ?? ''))].sort((a, b) => (!a ? 1 : !b ? -1 : a.localeCompare(b)));
  return names.map((n) => [
    { id: `family:${n}`, title: n || 'Company not listed yet', color: null },
    items.filter((i) => (i.family ?? '') === n),
  ]);
}

/** The same rows laid out by one column's value (a status, a person, a choice), in the column's own label order. */
export function groupByColumn(board: Board, items: Item[], columnId: string): [Group, Item[]][] {
  const column = board.columns.find((c) => c.id === columnId);
  if (!column) return groupsOf(board, items);
  const valuesOf = (i: Item) => {
    const v = (i.values[columnId] ?? '').trim();
    return v
      ? v
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean)
      : [''];
  };
  const order = Object.keys(column.labels ?? {});
  const names = [...new Set(items.flatMap(valuesOf))].sort((a, b) => {
    if (!a) return 1;
    if (!b) return -1;
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return ia === -1 && ib === -1 ? a.localeCompare(b) : ia === -1 ? 1 : ib === -1 ? -1 : ia - ib;
  });
  return names.map((name) => [
    { id: `by:${columnId}:${name}`, title: name || `No ${column.title.toLowerCase()}`, color: column.labels?.[name] ?? null },
    items.filter((i) => valuesOf(i).includes(name)),
  ]);
}

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
  groupBy,
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
  /** A column to group the rows by instead of monday's groups. Only the layout changes. */
  groupBy?: string | null;
}) {
  const router = useRouter();
  const client = useQueryClient();
  const isJobs = board.board_id === 'jobs';
  const [putting, setPutting] = useState<Item | null>(null);
  // Monday items that are not jobs yet can be ticked and turned into jobs together.
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [making, setMaking] = useState(false);
  const bulk = isJobs && items.length > 0 && items.every((i) => !i.job);
  const makeMany = async () => {
    setMaking(true);
    const ids = items.filter((i) => picked.has(i.id)).map((i) => i.id.replace(/^monday:/, ''));
    const res = await authFetch('/api/v1/board-edit/jobs/from-monday/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }).catch(() => null);
    const out = (await res?.json().catch(() => null)) as { made?: number; failed?: number; message?: string; detail?: string } | null;
    setMaking(false);
    setPicked(new Set());
    if (!res?.ok) toast.error('The jobs weren’t made', { description: typeof out?.detail === 'string' ? out.detail : undefined });
    else if ((out?.failed ?? 0) > 0) toast.warning(out?.message ?? `${out?.failed} could not be made into jobs`);
    else toast.success(out?.message ?? `Made ${out?.made ?? ids.length} jobs`);
    void client.invalidateQueries({ queryKey: ['boards'] });
  };
  // On the Jobs board only the title and customer can be typed; the rest is worked out. Elsewhere the server says per cell.
  const canType = (item: Item, c: Column) =>
    isJobs
      ? Boolean(item.job) && (c.id === 'title' || c.id === 'customer')
      : EDITABLE_TYPES.has(c.type) && (item.cells?.[c.id]?.editable ?? true);
  const makeJob = async (a: { label: string; path: string; method: string; body?: unknown }) => {
    const res = await authFetch(a.path, {
      method: a.method,
      headers: a.body ? { 'Content-Type': 'application/json' } : undefined,
      body: a.body ? JSON.stringify(a.body) : undefined,
    });
    const out = (await res.json().catch(() => null)) as { detail?: string; message?: string } | null;
    if (!res.ok) return toast.error('That didn’t work', { description: typeof out?.detail === 'string' ? out.detail : `(${res.status})` });
    toast.success(out?.message ?? (a.label === 'Make a job' ? 'Made a job from it' : 'Done'));
    void client.invalidateQueries({ queryKey: ['boards'] });
  };
  const openItem = (item: Item) => {
    if (item.pending) return;
    if (isJobs) {
      if (item.job) router.push(`/jobs/${encodeURIComponent(item.id)}`);
      return;
    }
    onSelect(item);
  };
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const staged = useStaged(board);
  const drag = useCellDrag(board);
  // The cell being typed in, as "itemId:columnId". Click a cell to type; Enter, Tab and Esc work like a spreadsheet.
  const [editing, setEditing] = useState<string | null>(null);
  const [newRow, setNewRow] = useState<Record<string, string>>({});
  // The cell last clicked: where a block pasted from Excel starts.
  const [active, setActive] = useState<{ itemId: string; columnId: string } | null>(null);
  // Ctrl+Z puts back the last thing changed on this board, unless a box is being typed in.
  useEffect(() => {
    if (!drag.can) return undefined;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (
        (e.ctrlKey || e.metaKey) &&
        e.key.toLowerCase() === 'z' &&
        !e.shiftKey &&
        tag !== 'INPUT' &&
        tag !== 'TEXTAREA' &&
        tag !== 'SELECT'
      ) {
        e.preventDefault();
        void drag.undoLast();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drag]);
  const nameColumn = board.columns.find((c) => c.type === 'name');
  // On the Jobs board a column nobody has filled in is left out.
  const columns = board.columns.filter(
    (c) =>
      c.type !== 'name' &&
      !board.empty_columns?.includes(c.id) &&
      (!isJobs || items.length === 0 || items.some((i) => (i.values[c.id] ?? '') !== '')),
  );
  const editable = columns.filter((c) => EDITABLE_TYPES.has(c.type));
  // A monday Doc's table has no item names; reference boards aren't tracked by the app.
  const showName = Boolean(nameColumn) || board.items.some((i) => i.name);
  const tracked = TRACKED_ROLES.includes(board.role);
  const span = columns.length + (showName ? 1 : 0) + (tracked ? 1 : 0);
  // Rows saved for monday but not on it yet are ordinary rows in their group: same cells, same editing.
  const firstGroup = board.groups[0]?.id ?? null;
  const newItems: Item[] = [
    // The server now sends pending rows in the board itself; the changes list is the fallback for an older one.
    ...(items.some((i) => i.pending) ? [] : staged.rows).map((r, i) => ({
      id: r.id ?? `new:s${i}`,
      staged_id: r.id,
      name: r.name || '(no name)',
      group: r.group ?? firstGroup,
      values: Object.fromEntries(columns.map((c) => [c.id, r.fields[c.id] ?? r.fields[c.title] ?? ''])),
      app: null,
      pending: true,
    })),
    ...drag.fresh.map((f) => ({ id: `fresh:${f.key}`, name: f.name, group: f.group ?? firstGroup, values: {}, app: null, pending: true })),
  ];
  const grouped =
    groupBy === 'family'
      ? groupByFamily([...items, ...newItems])
      : groupBy
        ? groupByColumn(board, [...items, ...newItems], groupBy)
        : groupsOf(board, [...items, ...newItems]);
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

  const header = (id: string, title: string, extra?: string, minWidth?: number, computed?: boolean) => {
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
          {computed && (
            <span title="Filled in by the app: monday leaves this column empty, so the app fills it in." className="inline-flex text-info">
              <Calculator className="size-3" aria-hidden />
              <span className="sr-only">Filled in by the app</span>
            </span>
          )}
          {active && (sort.desc ? <ArrowDown className="size-3" aria-hidden /> : <ArrowUp className="size-3" aria-hidden />)}
        </button>
      </th>
    );
  };

  return (
    <div className="grid gap-2">
      {bulk && (
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <button
            type="button"
            className="underline-offset-2 hover:underline"
            onClick={() => setPicked(picked.size === items.length ? new Set() : new Set(items.map((i) => i.id)))}
          >
            {picked.size === items.length ? 'Clear the ticks' : 'Tick all'}
          </button>
          <button
            type="button"
            disabled={picked.size === 0 || making}
            className="rounded-full bg-primary px-3 py-1 text-primary-foreground disabled:opacity-50"
            onClick={() => void makeMany()}
          >
            {making ? 'Making jobs…' : `Make jobs from the selected rows (${picked.size})`}
          </button>
        </div>
      )}
      <div className="flex items-center gap-3 text-xs">
        <button
          type="button"
          className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(grouped.map(([g]) => g.id)))}
        >
          {allCollapsed ? 'Expand all groups' : 'Collapse all groups'}
        </button>
        {drag.can && (
          <span className="rounded-full bg-muted px-2.5 py-1 text-foreground max-sm:hidden">
            Tip: drag a value onto another cell in the same column to copy it
          </span>
        )}
        {drag.held && (
          <span className="flex items-center gap-2 rounded-full bg-primary px-3 py-1 text-primary-foreground">
            Copying “{drag.held.value}”. Tap a matching cell to paste it.
            <button type="button" className="underline" onClick={drag.cancel}>
              Cancel
            </button>
          </span>
        )}
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
                {columns.map((c) => header(c.id, c.title, undefined, width(c), c.computed_by_app))}
                {drag.can && !isJobs && (
                  <th scope="col" className="border-b px-1 text-left">
                    <AddColumn boardId={board.board_id} />
                  </th>
                )}
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
                        onClick={() => openItem(item)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && e.target === e.currentTarget) openItem(item);
                        }}
                        className={cn(
                          'group/row cursor-pointer border-b outline-none hover:bg-muted/40 focus-visible:bg-muted/60',
                          selected === item.id && 'bg-accent hover:bg-accent',
                        )}
                      >
                        {showName && (
                          <th
                            scope="row"
                            className="sticky left-0 z-[1] max-w-80 truncate border-l-4 bg-card px-3 py-1.5 text-left font-medium"
                            style={{ borderLeftColor: item.pending ? 'var(--color-ok, #16a34a)' : (g.color ?? 'transparent') }}
                            title={item.name}
                          >
                            <span className="flex items-center gap-2">
                              {bulk && (
                                <input
                                  type="checkbox"
                                  aria-label={`Select ${item.name}`}
                                  className="size-3.5 shrink-0 accent-foreground"
                                  checked={picked.has(item.id)}
                                  onClick={(e) => e.stopPropagation()}
                                  onChange={() =>
                                    setPicked((p) => {
                                      const n = new Set(p);
                                      if (n.has(item.id)) n.delete(item.id);
                                      else n.add(item.id);
                                      return n;
                                    })
                                  }
                                />
                              )}
                              <span className="min-w-0 truncate">{item.name}</span>
                              {(item.tags ?? []).slice(0, 3).map((t) => (
                                <span
                                  key={t}
                                  className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground"
                                >
                                  {t}
                                </span>
                              ))}
                              {isJobs && item.job && (
                                <span
                                  className={cn(
                                    'shrink-0 rounded-full px-1.5 py-0.5 text-[11px] font-normal',
                                    item.on_monday ? 'bg-ok-soft text-ok' : 'bg-muted text-muted-foreground',
                                  )}
                                >
                                  {item.link_state === 'suggested' ? 'maybe on monday' : item.on_monday ? 'on monday' : 'not on monday yet'}
                                </span>
                              )}
                              {(item.actions?.length ?? 0) > 0 && (
                                <DropdownMenu>
                                  <DropdownMenuTrigger
                                    aria-label={`More for ${item.name}`}
                                    className="ml-auto shrink-0 rounded p-1 text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <MoreHorizontal className="size-3.5" />
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="start" onClick={(e) => e.stopPropagation()}>
                                    {item.actions!.map((a) => (
                                      <DropdownMenuItem key={a.label} onClick={() => void makeJob(a)}>
                                        {a.label}
                                      </DropdownMenuItem>
                                    ))}
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              )}
                              {item.pending && (
                                <span className="shrink-0 rounded-full bg-ok-soft px-1.5 py-0.5 text-[11px] font-normal text-ok">
                                  new, not on monday yet
                                </span>
                              )}
                              {!item.pending && !(isJobs && !item.job) && (
                                <button
                                  type="button"
                                  aria-label={`Open ${item.name}`}
                                  title="Open this row"
                                  className="ml-auto shrink-0 rounded p-1 text-muted-foreground opacity-0 outline-none group-hover/row:opacity-100 hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    openItem(item);
                                  }}
                                >
                                  <PanelRight className="size-3.5" />
                                </button>
                              )}
                              {drag.can && isJobs && item.job && !item.on_monday && (
                                <button
                                  type="button"
                                  className="shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-normal opacity-0 group-hover/row:opacity-100 hover:bg-muted focus-visible:opacity-100"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setPutting(item);
                                  }}
                                >
                                  Put on a monday board
                                </button>
                              )}
                              {drag.can && !isJobs && (
                                <DropdownMenu>
                                  <DropdownMenuTrigger
                                    aria-label={`More for ${item.name}`}
                                    className="shrink-0 rounded p-1 text-muted-foreground opacity-0 outline-none group-hover/row:opacity-100 hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <MoreHorizontal className="size-3.5" />
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="start" onClick={(e) => e.stopPropagation()}>
                                    <DropdownMenuItem onClick={() => drag.rowAction(item, 'duplicate')}>Make a copy</DropdownMenuItem>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuLabel>Move to</DropdownMenuLabel>
                                    {board.groups
                                      .filter((x) => x.id !== g.id)
                                      .map((x) => (
                                        <DropdownMenuItem key={x.id} onClick={() => drag.rowAction(item, 'move', x.id)}>
                                          {x.title}
                                        </DropdownMenuItem>
                                      ))}
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem onClick={() => drag.rowAction(item, 'archive')}>Archive</DropdownMenuItem>
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              )}
                            </span>
                          </th>
                        )}
                        {columns.map((c) => {
                          const mark = item.cells?.[c.id];
                          const change =
                            staged.cells.get(idKey(item.id, c.id)) ??
                            staged.cells.get(cellKey(item.name, c.title)) ??
                            (mark?.staged
                              ? {
                                  now: mark.was == null ? '' : String(mark.was),
                                  becomes: mark.staged_value == null ? '' : String(mark.staged_value),
                                }
                              : undefined);
                          return (
                            <td
                              key={c.id}
                              className={cn(
                                'px-3 py-1.5 align-middle',
                                change && 'bg-ok-soft',
                                drag.over === `${item.id}:${c.id}` && 'bg-accent outline-2 -outline-offset-2 outline-ring',
                              )}
                              style={{ minWidth: width(c) }}
                              title={change ? 'Waiting to be sent to monday' : undefined}
                              {...(editing === `${item.id}:${c.id}` || (isJobs && !canType(item, c))
                                ? {}
                                : drag.dragProps(item, c, item.values[c.id] ?? ''))}
                              onClick={
                                drag.can && canType(item, c)
                                  ? (e) => {
                                      e.stopPropagation();
                                      setActive({ itemId: item.id, columnId: c.id });
                                      setEditing(`${item.id}:${c.id}`);
                                    }
                                  : drag.can && !isJobs && LOCKED.has(c.type)
                                    ? (e) => {
                                        e.stopPropagation();
                                        toast.info(`${c.title} is worked out by monday.`, {
                                          description: 'It can’t be typed into. Change the columns it is built from.',
                                        });
                                      }
                                    : undefined
                              }
                            >
                              {editing === `${item.id}:${c.id}` ? (
                                <CellEditor
                                  column={c}
                                  value={item.values[c.id] ?? ''}
                                  onCancel={() => setEditing(null)}
                                  onSave={(text, move: Move) => {
                                    if (!text.trim() && (item.values[c.id] ?? '').trim())
                                      toast.info('Clearing a cell isn’t possible yet', { description: 'Type a new value instead.' });
                                    else if (text.trim() && text.trim() !== (item.values[c.id] ?? '').trim())
                                      drag.typed(item, c, text.trim());
                                    const at = rows.findIndex((x) => x.id === item.id);
                                    const col = editable.findIndex((x) => x.id === c.id);
                                    let next: string | null = null;
                                    if (move === 'down' && rows[at + 1]) next = `${rows[at + 1].id}:${c.id}`;
                                    else if (move === 'right' && editable[col + 1]) next = `${item.id}:${editable[col + 1].id}`;
                                    else if (move === 'right' && rows[at + 1] && editable[0]) next = `${rows[at + 1].id}:${editable[0].id}`;
                                    else if (move === 'left' && editable[col - 1]) next = `${item.id}:${editable[col - 1].id}`;
                                    setEditing(next);
                                  }}
                                />
                              ) : drag.pending[`${item.id}:${c.id}`] ? (
                                <span className="grid gap-0.5">
                                  <span className="rounded-sm bg-bad-soft px-1 text-bad line-through">
                                    {item.values[c.id] || '(blank)'}
                                  </span>
                                  <span className="font-medium text-ok">{drag.pending[`${item.id}:${c.id}`]} …</span>
                                </span>
                              ) : change ? (
                                <span className="grid gap-0.5">
                                  {change.now && <span className="rounded-sm bg-bad-soft px-1 text-bad line-through">{change.now}</span>}
                                  <span className="font-medium text-ok">{change.becomes || '(cleared)'}</span>
                                </span>
                              ) : (
                                <Cell column={c} value={item.values[c.id] ?? ''} />
                              )}
                            </td>
                          );
                        })}
                        {tracked && (
                          <td className="border-l px-3 py-1.5 whitespace-nowrap">
                            <span className={cn('flex items-center gap-2', item.pending && 'hidden')}>
                              <InApp role={board.role} item={item} compact />
                              <AutomationChip data={automations} itemId={item.id} />
                              <NoteChip notes={notes} itemId={item.id} />
                            </span>
                          </td>
                        )}
                      </tr>
                    ))}
                  {open && drag.can && !groupBy && (
                    <tr className="border-b">
                      <td colSpan={span} className="sticky left-0 px-3 py-1">
                        <input
                          aria-label={`Add a row to ${g.title}`}
                          placeholder="+ Add a row (type a name, press Enter)"
                          value={newRow[g.id] ?? ''}
                          onChange={(e) => setNewRow({ ...newRow, [g.id]: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && (newRow[g.id] ?? '').trim()) {
                              drag.addRow(newRow[g.id], g.id);
                              setNewRow({ ...newRow, [g.id]: '' });
                            }
                          }}
                          className="h-8 w-full max-w-sm rounded-md bg-transparent px-2 text-[13px] text-muted-foreground outline-none placeholder:text-muted-foreground/70 hover:bg-muted/40 focus:bg-background focus:text-foreground focus:ring-2 focus:ring-ring/40"
                        />
                      </td>
                    </tr>
                  )}
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
      {!isJobs && <PasteBlock boardId={board.board_id} active={active} />}
      {putting && <PutOnMonday kind="job" refId={putting.id} title={putting.name} onClose={() => setPutting(null)} />}
    </div>
  );
}
