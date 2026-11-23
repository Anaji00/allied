'use client';

import { SinceStrip } from '@/components/data/since-strip';
import { BoardChanges } from './board-changes';
import { BoardHealth } from './board-health';
import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { parseAsBoolean, parseAsString, parseAsStringLiteral, useQueryState, useQueryStates } from 'nuqs';
import { Loader2, RefreshCw, Search } from 'lucide-react';
import { toast } from 'sonner';
import { UpdatesPanel } from '@/components/data/updates-panel';
import { DetailPanel } from '@/components/data/detail-panel';
import Link from 'next/link';
import { LateUrgent } from './late-urgent';
import { useMondayStatus } from '@/components/admin/monday-changes';
import { NativeSelect, SectionTabs } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DownloadMenu } from '@/components/data/download-menu';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { since } from '@/lib/format';
import { cn } from '@/lib/utils';
import { ItemCard, NOTES_ON, TRACKED_ROLES, filterItems, peopleOnBoard, type Sort } from './board-parts';
import { AutomateButton, AutomatePanel } from './board-automations';
import { BoardTable } from './board-table';
import { SavedViews } from '@/components/data/saved-views';
import { BoardTimeline } from './board-timeline';
import { BoardCalendar, BoardKanban } from './board-views';
import { READ_NOW_ROLE, needsAction, useAutomations, useReadNow } from './use-automations';
import { useBoard, useBoards, type Board, type Role } from './use-boards';
import { useBoardNotes } from './use-notes';

// Tracked boards first, projects leading (the one people check most); reference boards and Docs after.
const ORDER: Record<Role, number> = { projects: 0, vendors: 1, rfqs: 2, reference: 3, doc: 4 };
const VIEWS = ['table', 'kanban', 'calendar', 'timeline', 'late'] as const;
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
  const list = useMemo(
    () =>
      [...(boards.data ?? [])].sort(
        (a, b) => Number(b.board_id === 'jobs') - Number(a.board_id === 'jobs') || ORDER[a.role] - ORDER[b.role],
      ),
    [boards.data],
  );
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
        Boards show up here once the app reads them from monday. In practice mode nothing is read, so this stays empty.{' '}
        <Link href="/health?tab=monday" className="underline underline-offset-2">
          Check the monday connection
        </Link>{' '}
        to see whether it is connected and read the boards.
      </EmptyState>
    );

  return (
    <div className="grid min-w-0 grid-cols-1 gap-3">
      <div role="group" aria-label="Board" className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
        {list.map((b) => (
          <button
            key={b.board_id}
            type="button"
            aria-pressed={b.board_id === boardId}
            title={b.name}
            onClick={() => {
              if (b.board_id === boardId) return;
              void setChosen(b.board_id);
              void setBoardState(CLEARED);
            }}
            className={cn(
              'inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-sm whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring',
              b.board_id === boardId
                ? 'border-foreground bg-foreground text-background'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            {b.name}
          </button>
        ))}
      </div>
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
  // Group the rows by any status, person or choice column. This only changes how they are laid out here; monday is untouched.
  const [groupBy, setGroupBy] = useQueryState('group');
  const automations = useAutomations(board.board_id, tracked);
  const notes = useBoardNotes(board.board_id, NOTES_ON.has(board.role));
  const readNow = useReadNow(board.board_id, board.role);
  const { hasRole } = useAuth();
  const readRole = READ_NOW_ROLE[board.role];

  // Filtering trails the typing slightly on big boards (Engineering has hundreds of items).
  const deferredQ = useDeferredValue(q);
  const isJobs = board.board_id === 'jobs';
  const [notJobs, setNotJobs] = useQueryState('notjobs', parseAsBoolean.withDefault(false));
  // The Jobs board shows real jobs; monday items that are not jobs yet wait behind one chip.
  const pool = isJobs ? board.items.filter((i) => (notJobs ? !i.job : i.job)) : board.items;
  const notJobCount = isJobs ? board.items.filter((i) => !i.job).length : 0;
  const waiting = useMondayStatus().data?.staged ?? 0;
  const matching = filterItems(pool, { person, status, q: deferredQ }, board);
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
            ['table', 'Table'],
            ...(hasRole('MANAGER')
              ? ([
                  ['kanban', 'Columns by status'],
                  ['calendar', 'Calendar'],
                ] as const)
              : []),
            ['late', 'Late or urgent'],
          ]}
        />
        <div className="flex flex-wrap items-center gap-2 pb-1">
          {hasRole('MANAGER') && (
            <NativeSelect
              aria-label="More ways to look at this board"
              value={view === 'timeline' ? 'timeline' : ''}
              onChange={(e) => void setView(e.target.value === 'timeline' ? 'timeline' : null)}
              className={`h-8 w-auto text-xs ${view === 'timeline' ? 'border-foreground font-medium' : 'text-muted-foreground'}`}
            >
              <option value="">More views…</option>
              <option value="timeline">Timeline</option>
            </NativeSelect>
          )}
          <SavedViews screen="boards" />
          {readRole && hasRole(readRole) && (
            <Button variant="ghost" size="sm" disabled={readNow.isPending} onClick={() => readNow.mutate()}>
              {readNow.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Read from monday<span className="max-sm:hidden"> now</span>
            </Button>
          )}
          <DownloadMenu list={`boards/${board.board_id}`} />
          {tracked && hasRole('MANAGER') && <AutomateButton data={automations.data} onOpen={() => void setAutomate(true)} />}
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
        {view === 'table' &&
          board.columns.some((c) => ['status', 'color', 'people', 'person', 'multiple-person', 'dropdown'].includes(c.type)) && (
            <NativeSelect
              aria-label="Group by"
              value={groupBy ?? ''}
              onChange={(e) => void setGroupBy(e.target.value || null)}
              className="w-auto"
            >
              <option value="">Grouped as on monday</option>
              {board.items.some((i) => i.family) && <option value="family">Group by customer group</option>}
              {board.columns
                .filter((c) => ['status', 'color', 'people', 'person', 'multiple-person', 'dropdown'].includes(c.type))
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    Group by {c.title.toLowerCase()}
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
        {isJobs
          ? 'Jobs are saved in the app. Put one on a monday board from its row.'
          : `${board.label} · ${items.length} of ${pool.length} item${pool.length === 1 ? '' : 's'}${board.hide_done && !showDone ? ', finished ones hidden' : ''} · last updated from monday: ${since(board.read_at) || 'never'}. ${waiting > 0 ? `${waiting} change${waiting === 1 ? '' : 's'} waiting across all boards. ` : ''}Changes go to monday only when a manager sends them.`}
      </p>
      {pool.length >= 100 &&
        (() => {
          const counts = new Map<string, number>();
          for (const i of pool) {
            const v = (i.values[board.status_column] ?? '').trim();
            if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
          }
          const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
          return top.length ? (
            <p className="text-sm">
              {pool.length} items: {top.map(([k, n]) => `${n} ${k.toLowerCase()}`).join(', ')}.
            </p>
          ) : null;
        })()}
      {isJobs && notJobCount > 0 && (
        <button
          type="button"
          className="justify-self-start rounded-full border px-3 py-1 text-xs hover:bg-muted"
          onClick={() => void setNotJobs(notJobs ? null : true)}
        >
          {notJobs ? 'Back to the jobs' : `${notJobCount} monday items are not jobs yet`}
        </button>
      )}

      <SinceStrip kind="board" refId={board.board_id} />
      <BoardChanges boardId={board.board_id} />
      <BoardHealth boardId={board.board_id} onOpenItem={(id) => select(id)} />

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_24rem]')}>
        <div className="min-w-0">
          {view === 'late' ? (
            <LateUrgent />
          ) : items.length === 0 && isJobs && !notJobs && !filtered ? (
            <EmptyState title="No jobs yet">
              <span className="grid justify-items-center gap-3">
                <span>Start one here, or turn the items already on monday into jobs.</span>
                <span className="flex flex-wrap justify-center gap-2">
                  <Button size="sm" onClick={() => window.dispatchEvent(new CustomEvent('allied:new', { detail: { kind: 'job' } }))}>
                    Start a job
                  </Button>
                  {notJobCount > 0 && (
                    <Button size="sm" variant="outline" onClick={() => void setNotJobs(true)}>
                      Make jobs from monday items ({notJobCount})
                    </Button>
                  )}
                </span>
              </span>
            </EmptyState>
          ) : items.length === 0 ? (
            <EmptyState title={filtered ? 'Nothing on this board matches' : 'This board has no items to show'}>
              {filtered
                ? 'Clear the filters or try a shorter search.'
                : tracked && !showDone
                  ? 'Tick Show finished to include finished items.'
                  : ''}
            </EmptyState>
          ) : view === 'kanban' ? (
            <BoardKanban board={board} items={items} onSelect={(i) => select(i.id)} automations={automations.data} notes={notes.data} />
          ) : view === 'timeline' ? (
            <BoardTimeline board={board} items={items} onSelect={(i) => select(i.id)} />
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
              groupBy={groupBy}
            />
          )}
        </div>
        <DetailPanel
          open={Boolean(selected)}
          onClose={() => select(null)}
          title={selected?.name || 'Item'}
          description={selected?.group_title ?? undefined}
        >
          {selected && (
            <div className="grid gap-4">
              <ItemCard board={board} item={selected} automations={automations.data} />
              <UpdatesPanel itemType="board_item" itemRef={String(selected.id)} />
            </div>
          )}
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
