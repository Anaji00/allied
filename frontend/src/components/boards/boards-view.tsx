'use client';

import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { parseAsBoolean, parseAsString, parseAsStringLiteral, useQueryState, useQueryStates } from 'nuqs';
import { Loader2, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { DetailPanel } from '@/components/data/detail-panel';
import { FilterChips, NativeSelect, SectionTabs } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { since } from '@/lib/format';
import { cn } from '@/lib/utils';
import { ItemCard, NOTES_ON, TRACKED_ROLES, filterItems, peopleOnBoard, type Sort } from './board-parts';
import { AutomateButton, AutomatePanel } from './board-automations';
import { BoardTable } from './board-table';
import { BoardCalendar, BoardKanban } from './board-views';
import { READ_NOW_ROLE, needsAction, useAutomations, useReadNow } from './use-automations';
import { useBoard, useBoards, type Board, type Role } from './use-boards';
import { useBoardNotes } from './use-notes';

// Tracked boards first, projects leading (the one people check most); reference boards and Docs after.
const ORDER: Record<Role, number> = { projects: 0, vendors: 1, rfqs: 2, reference: 3, doc: 4 };
const VIEWS = ['table', 'kanban', 'calendar'] as const;
const BOARD_STATE = {
  item: parseAsString,
  q: parseAsString,
  person: parseAsString,
  status: parseAsString,
  sort: parseAsString,
  needs: parseAsBoolean,
};
const CLEARED = { item: null, q: null, person: null, status: null, sort: null, needs: null };
// The board someone last looked at opens first next time.
const LAST = 'boards:last';

function lastBoard() {
  try {
    return localStorage.getItem(LAST);
  } catch {
    return null;
  }
}

export function BoardsView() {
  const boards = useBoards();
  const list = useMemo(() => [...(boards.data ?? [])].sort((a, b) => ORDER[a.role] - ORDER[b.role]), [boards.data]);
  const [chosen, setChosen] = useQueryState('board', parseAsString);
  const [showDone, setShowDone] = useQueryState('all', parseAsBoolean.withDefault(false));
  // A board's filters, sort and open item mean nothing on another board.
  const [, setBoardState] = useQueryStates(BOARD_STATE);
  const [stored] = useState(lastBoard);
  const pick = chosen ?? stored;
  const boardId = pick && list.some((b) => b.board_id === pick) ? pick : (list[0]?.board_id ?? null);
  useEffect(() => {
    if (!boardId) return;
    try {
      localStorage.setItem(LAST, boardId);
    } catch {
      // Private windows refuse storage; the first board opens instead.
    }
  }, [boardId]);
  const role = list.find((b) => b.board_id === boardId)?.role;
  const tracked = Boolean(role && TRACKED_ROLES.includes(role));
  const board = useBoard(boardId, tracked && !showDone);

  if (boards.isPending) return <RowsSkeleton rows={8} />;
  if (boards.error) return <ErrorState error={boards.error} onRetry={() => void boards.refetch()} />;
  if (!list.length)
    return (
      <EmptyState title="No monday boards have been read yet">
        Boards show up here once the app reads them from monday. In practice mode nothing is read, so this stays empty.
      </EmptyState>
    );

  return (
    <div className="grid min-w-0 grid-cols-1 gap-3">
      <FilterChips
        label="Board"
        value={boardId ?? ''}
        onChange={(v) => {
          if (v === boardId) return;
          void setChosen(v);
          void setBoardState(CLEARED);
        }}
        options={list.map((b) => ({ value: b.board_id, label: b.name, count: b.items }))}
      />
      {board.isPending ? (
        <RowsSkeleton rows={10} />
      ) : board.error ? (
        <ErrorState error={board.error} onRetry={() => void board.refetch()} />
      ) : board.data ? (
        <BoardScreen
          key={board.data.board_id}
          board={board.data}
          tracked={tracked}
          showDone={showDone}
          setShowDone={(v) => void setShowDone(v || null)}
        />
      ) : null}
    </div>
  );
}

function BoardScreen({
  board,
  tracked,
  showDone,
  setShowDone,
}: {
  board: Board;
  tracked: boolean;
  showDone: boolean;
  setShowDone: (v: boolean) => void;
}) {
  const wide = useMediaQuery('(min-width: 1280px)');
  const [view, setView] = useQueryState('view', parseAsStringLiteral(VIEWS).withDefault('table'));
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''));
  const [person, setPerson] = useQueryState('person', parseAsString.withDefault(''));
  const [status, setStatus] = useQueryState('status', parseAsString.withDefault(''));
  const [sortParam, setSortParam] = useQueryState('sort', parseAsString);
  const [itemId, setItemId] = useQueryState('item');
  const [automate, setAutomate] = useQueryState('automate', parseAsBoolean.withDefault(false));
  const [needs, setNeeds] = useQueryState('needs', parseAsBoolean.withDefault(false));
  const automations = useAutomations(board.board_id, tracked);
  const notes = useBoardNotes(board.board_id, NOTES_ON.has(board.role));
  const readNow = useReadNow(board.board_id, board.role);
  const { hasRole } = useAuth();
  const readRole = READ_NOW_ROLE[board.role];

  // Filtering trails the typing slightly on big boards (Engineering has hundreds of items).
  const deferredQ = useDeferredValue(q);
  const matching = filterItems(board.items, { person, status, q: deferredQ }, board);
  const canDraft = hasRole('OPERATOR');
  const items = needs && tracked ? matching.filter((i) => needsAction(automations.data, i.id, canDraft).any) : matching;
  const needCount = tracked ? board.items.filter((i) => needsAction(automations.data, i.id, canDraft).any).length : 0;
  const people = peopleOnBoard(board);
  const statusColumn = board.columns.find((c) => c.id === board.status_column && c.labels);
  const sort: Sort = sortParam ? { column: sortParam.replace(/:(asc|desc)$/, ''), desc: sortParam.endsWith(':desc') } : null;
  const selected = board.items.find((i) => i.id === itemId);
  const select = (id: string | null) => void setItemId(id);
  const filtered = Boolean(person || status || deferredQ.trim() || (needs && tracked));

  return (
    <div className="grid min-w-0 grid-cols-1 gap-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <SectionTabs
          label="Board views"
          value={view}
          onChange={(v) => void setView(v === 'table' ? null : v)}
          tabs={[
            ['table', 'Main table'],
            ['kanban', 'Kanban'],
            ['calendar', 'Calendar'],
          ]}
        />
        <div className="flex flex-wrap items-center gap-2 pb-1">
          {readRole && hasRole(readRole) && (
            <Button variant="ghost" size="sm" disabled={readNow.isPending} onClick={() => readNow.mutate()}>
              {readNow.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Read from monday<span className="max-sm:hidden"> now</span>
            </Button>
          )}
          {tracked && <AutomateButton data={automations.data} onOpen={() => void setAutomate(true)} />}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-60">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={q}
            onChange={(e) => void setQ(e.target.value || null)}
            placeholder="Search this board"
            aria-label="Search this board"
            className="pl-8"
          />
        </div>
        {people.length > 0 && (
          <NativeSelect aria-label="Person" value={person} onChange={(e) => void setPerson(e.target.value || null)} className="w-auto">
            <option value="">Everyone</option>
            {people.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </NativeSelect>
        )}
        {statusColumn && (
          <NativeSelect
            aria-label={statusColumn.title}
            value={status}
            onChange={(e) => void setStatus(e.target.value || null)}
            className="w-auto"
          >
            <option value="">Any {statusColumn.title.toLowerCase()}</option>
            {Object.keys(statusColumn.labels ?? {}).map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </NativeSelect>
        )}
        {tracked && (
          <button
            type="button"
            aria-pressed={needs}
            onClick={() => void setNeeds(needs ? null : true)}
            className={cn(
              'inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs',
              needs ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            Needs action <span className="tabular-nums">{needCount}</span>
          </button>
        )}
        {/* Only the tracked boards know which statuses mean finished. */}
        {tracked && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} />
            Show finished
          </label>
        )}
        {filtered && (
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => {
              void setQ(null);
              void setPerson(null);
              void setStatus(null);
              void setNeeds(null);
            }}
          >
            Clear filters
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {board.label} · read from monday {since(board.read_at) || 'not yet'} · {items.length} of {board.items.length} item
        {board.items.length === 1 ? '' : 's'}
        {board.hide_done && !showDone ? ', finished ones hidden' : ''}. Read-only: change things on monday, and they show here after the
        next read.
      </p>

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_24rem]')}>
        <div className="min-w-0">
          {items.length === 0 ? (
            <EmptyState title={filtered ? 'Nothing on this board matches' : 'This board has no items to show'}>
              {filtered
                ? 'Clear the filters or try a shorter search.'
                : tracked && !showDone
                  ? 'Tick Show finished to include finished items.'
                  : ''}
            </EmptyState>
          ) : view === 'kanban' ? (
            <BoardKanban board={board} items={items} onSelect={(i) => select(i.id)} automations={automations.data} notes={notes.data} />
          ) : view === 'calendar' ? (
            <BoardCalendar board={board} items={items} onSelect={(i) => select(i.id)} />
          ) : (
            <BoardTable
              board={board}
              items={items}
              sort={sort}
              onSort={(s) => void setSortParam(s ? `${s.column}:${s.desc ? 'desc' : 'asc'}` : null)}
              selected={itemId}
              onSelect={(i) => select(i.id === itemId ? null : i.id)}
              automations={automations.data}
              notes={notes.data}
            />
          )}
        </div>
        <DetailPanel
          open={Boolean(selected)}
          onClose={() => select(null)}
          title={selected?.name || 'Item'}
          description={selected?.group_title ?? undefined}
        >
          {selected && <ItemCard board={board} item={selected} automations={automations.data} />}
        </DetailPanel>
      </div>
      {tracked && (
        <AutomatePanel
          open={automate}
          onClose={() => void setAutomate(null)}
          board={board}
          data={automations.data}
          onOpenItem={(id) => {
            void setAutomate(null);
            // A finished item isn't in the board copy while finished ones are hidden.
            if (!board.items.some((i) => i.id === id)) {
              if (tracked && !showDone) setShowDone(true);
              else toast('That item isn’t on this board any more.');
            }
            select(id);
          }}
        />
      )}
    </div>
  );
}
