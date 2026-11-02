'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { parseAsBoolean, parseAsStringLiteral, useQueryState } from 'nuqs';
import { useHotkeys } from 'react-hotkeys-hook';
import { toast } from 'sonner';
import { Loader2, Sparkles } from 'lucide-react';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { useFreshKeys } from '@/components/data/motion';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { ago, dateTime, words } from '@/lib/format';
import { AiOutput } from '@/components/tools/ai-output';
import { useUndoQueue } from '@/lib/undo';
import { cn } from '@/lib/utils';
import { DRAFT_KIND, decideDrafts, explainProblem, useInbox, useRefreshSource, type InboxAction, type InboxItem } from './use-inbox';

const GROUPS = ['all', 'approvals', 'problems', 'schedule'] as const;
const GROUP_LABEL: Record<(typeof GROUPS)[number], string> = {
  all: 'All',
  approvals: 'Approvals',
  problems: 'Problems',
  schedule: 'Schedule',
};
const SOURCE_LABEL = {
  alerts: 'alerts',
  problems: 'order problems',
  ai: 'AI results',
  drafts: 'drafts to send',
  monday: 'monday.com events',
};

const STRIPE = { critical: 'bg-bad', warning: 'bg-warn', info: 'bg-info' } as const;

// How long a decided item takes to fold away; matches the CSS duration below.
const FOLD_MS = 200;

const without = (set: Set<string>, key: string) => {
  const next = new Set(set);
  next.delete(key);
  return next;
};

export function InboxView() {
  const router = useRouter();
  const { hasRole } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [show, setShow] = useQueryState('show', parseAsStringLiteral(GROUPS).withDefault('all'));
  const [everyone, setEveryone] = useQueryState('everyone', parseAsBoolean.withDefault(false));
  const [selectedKey, setSelectedKey] = useQueryState('item');
  const { items, isPending, failed, canSeeAll, refetch } = useInbox({ everyone });
  const refresh = useRefreshSource();
  const queue = useUndoQueue();

  // A decided item folds away (`leaving`), then is hidden; Undo unfolds it
  // (`returning`). Counts and J/K skip a leaving item at once.
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [leaving, setLeaving] = useState<Set<string>>(() => new Set());
  const [returning, setReturning] = useState<Set<string>>(() => new Set());
  const listed = useMemo(() => items.filter((i) => !hidden.has(i.key) && (show === 'all' || i.group === show)), [items, hidden, show]);
  const visible = useMemo(() => items.filter((i) => !hidden.has(i.key) && !leaving.has(i.key)), [items, hidden, leaving]);
  // Items that arrive with the 30-second refresh get a short wash.
  const fresh = useFreshKeys(listed.map((i) => i.key));
  const shown = useMemo(() => visible.filter((i) => show === 'all' || i.group === show), [visible, show]);
  const counts = useMemo(() => {
    const c = { all: visible.length, approvals: 0, problems: 0, schedule: 0 };
    visible.forEach((i) => (c[i.group] += 1));
    return c;
  }, [visible]);

  // On wide screens the first item is always open; on narrow ones a tap opens
  // the sheet, so nothing is preselected.
  const selected = shown.find((i) => i.key === selectedKey) ?? (wide ? shown[0] : undefined);
  const index = selected ? shown.indexOf(selected) : -1;

  useEffect(() => {
    if (!selected) return;
    document.getElementById(`inbox-${selected.key}`)?.scrollIntoView({ block: 'nearest' });
  }, [selected]);

  const select = (item?: InboxItem) => void setSelectedKey(item ? item.key : null);

  // Drafts picked to send or cancel together (checkboxes, or Space on the open one).
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const togglePick = (item: InboxItem) => setPicked((p) => (p.has(item.key) ? without(p, item.key) : new Set(p).add(item.key)));
  const pickedItems = shown.filter((i) => picked.has(i.key));
  // The open draft's whole kind ("all vendor follow-ups"), for deciding a group at once.
  const sameKind = selected?.draft ? shown.filter((i) => i.draft?.kind === selected.draft!.kind) : [];

  // Each item's fold timer, so an Undo during the fold can stop it hiding the item after all.
  const folding = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  /** Folds items away now and moves the selection on; returns what brings them back. */
  function foldAway(list: InboxItem[]) {
    const keys = new Set(list.map((i) => i.key));
    const next =
      shown.slice(index + 1).find((i) => !keys.has(i.key)) ?? shown.slice(0, Math.max(index, 0)).findLast((i) => !keys.has(i.key));
    for (const key of keys) {
      setLeaving((l) => new Set(l).add(key));
      clearTimeout(folding.current.get(key));
      folding.current.set(
        key,
        setTimeout(() => {
          folding.current.delete(key);
          setHidden((h) => new Set(h).add(key));
          setLeaving((l) => without(l, key));
        }, FOLD_MS),
      );
    }
    setPicked((p) => new Set([...p].filter((k) => !keys.has(k))));
    // Wide screens move straight on. On narrow ones the sheet is modal and would
    // block the toast's Undo button, so it closes instead.
    if (selected && keys.has(selected.key)) select(wide ? next : undefined);
    return (back: Iterable<string> = keys) => {
      for (const key of back) {
        clearTimeout(folding.current.get(key));
        folding.current.delete(key);
        setHidden((h) => without(h, key));
        setLeaving((l) => without(l, key));
        setReturning((r) => new Set(r).add(key));
        setTimeout(() => setReturning((r) => without(r, key)), FOLD_MS * 2);
      }
    };
  }

  /** Sends or cancels several drafts as one decision, with one Undo. Any that fail come back. */
  function decideMany(list: InboxItem[], confirm: boolean) {
    const drafts = list.filter((i) => i.draft);
    if (!drafts.length) return;
    const restore = foldAway(drafts);
    const byId = new Map(drafts.map((i) => [i.draft!.id, i.key]));
    const n = drafts.length;
    queue(`${confirm ? 'Sent' : 'Cancelled'} ${n} draft${n === 1 ? '' : 's'}`, {
      commit: async (keepalive) => {
        const failed = await decideDrafts([...byId.keys()], confirm, keepalive);
        await refresh('drafts');
        if (failed.length) {
          restore(failed.map((f) => byId.get(f.id)!).filter(Boolean));
          toast.error(`${failed.length} of ${n} didn’t go through`, { description: failed[0].error });
        }
      },
      onUndo: () => restore(),
      onError: (error) => {
        restore();
        toast.error(`${confirm ? 'Sending' : 'Cancelling'} didn’t go through`, {
          description: error instanceof Error ? error.message : undefined,
        });
      },
    });
  }

  function decide(item: InboxItem, action: InboxAction, commit = action.commit) {
    if (action.href) {
      router.push(action.href);
      return;
    }
    if (!commit) return;
    const restore = foldAway([item]);
    queue(`${action.verb}: ${item.title}`, {
      commit: async (keepalive) => {
        await commit(keepalive);
        await refresh(item.source);
      },
      onUndo: () => {
        restore();
        select(item);
      },
      onError: (error) => {
        restore();
        toast.error(`${action.label} didn’t go through`, {
          description: error instanceof Error ? error.message : undefined,
        });
      },
    });
  }

  const allowed = (item?: InboxItem) => (item ? item.actions.filter((a) => hasRole(a.minRole)) : []);
  const primary = allowed(selected).find((a) => a.primary);

  useHotkeys(['j', 'ArrowDown'], () => shown[index + 1] && select(shown[index + 1]), { preventDefault: true }, [shown, index]);
  useHotkeys(['k', 'ArrowUp'], () => shown[index - 1] && select(shown[index - 1]), { preventDefault: true }, [shown, index]);
  useHotkeys('e', () => selected && primary && decide(selected, primary), [selected, primary]);
  useHotkeys(
    ['a', 'r', 'x'],
    (e) => {
      const action = allowed(selected).find((a) => a.hotkey === e.key.toLowerCase());
      if (selected && action) decide(selected, action);
    },
    [selected],
  );
  useHotkeys(
    'enter',
    () => {
      // A draft's Enter sends it; anything else's opens where it points.
      if (selected?.draft && primary) return decide(selected, primary);
      const open = allowed(selected).find((a) => a.href);
      if (open) router.push(open.href!);
    },
    [selected, primary],
  );
  useHotkeys('space', () => selected?.draft && togglePick(selected), { preventDefault: true }, [selected]);
  // Shift+Enter: the picked drafts, or else every draft of the open one's kind.
  useHotkeys('shift+enter', () => decideMany(pickedItems.length ? pickedItems : sameKind, true), [pickedItems, sameKind]);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Show" className="flex flex-wrap gap-1.5">
          {GROUPS.map((g) => (
            <button
              key={g}
              role="tab"
              type="button"
              aria-selected={show === g}
              onClick={() => {
                void setShow(g === 'all' ? null : g);
                select(undefined);
              }}
              className={cn(
                'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors',
                show === g ? 'border-foreground bg-foreground text-background' : 'bg-card text-muted-foreground hover:text-foreground',
              )}
            >
              {GROUP_LABEL[g]}
              <span className="tabular-nums opacity-70">{counts[g]}</span>
            </button>
          ))}
        </div>
        {canSeeAll && (
          <label className="ml-1 inline-flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={everyone}
              onChange={(e) => void setEveryone(e.target.checked || null)}
              className="size-3.5 accent-foreground"
            />
            Everyone’s alerts
          </label>
        )}
        <p className="ml-auto hidden items-center gap-1.5 text-xs text-muted-foreground lg:flex">
          <Kbd>J</Kbd>
          <Kbd>K</Kbd> move · <Kbd>E</Kbd> do the first action · <Kbd>A</Kbd>
          <Kbd>R</Kbd> approve or reject · <Kbd>Enter</Kbd> open or send · <Kbd>X</Kbd> cancel a draft · <Kbd>Space</Kbd> pick ·{' '}
          <Kbd>Shift</Kbd>
          <Kbd>Enter</Kbd> send picked
        </p>
      </div>

      {(pickedItems.length > 0 || sameKind.length > 1) && (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm"
          role="group"
          aria-label="Several drafts at once"
        >
          {pickedItems.length > 0 ? (
            <>
              <span className="font-medium tabular-nums">{pickedItems.length} picked</span>
              <Button size="sm" onClick={() => decideMany(pickedItems, true)}>
                Send {pickedItems.length}
              </Button>
              <Button size="sm" variant="outline" onClick={() => decideMany(pickedItems, false)}>
                Cancel {pickedItems.length}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>
                Clear
              </Button>
            </>
          ) : (
            <>
              <span className="text-muted-foreground">
                {sameKind.length} {(DRAFT_KIND[selected!.draft!.kind] ?? 'similar').toLowerCase()} drafts waiting
              </span>
              <Button size="sm" variant="outline" onClick={() => decideMany(sameKind, true)}>
                Send all {sameKind.length}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setPicked(new Set(sameKind.map((i) => i.key)))}>
                Pick them
              </Button>
            </>
          )}
        </div>
      )}

      {failed.length > 0 && (
        <p role="alert" className="rounded-md bg-warn-soft px-3 py-2 text-sm text-warn">
          Couldn’t load {failed.map((f) => SOURCE_LABEL[f.name]).join(', ')}, so this list may be missing items.{' '}
          <button type="button" className="font-medium underline" onClick={() => void refetch()}>
            Try again
          </button>
        </p>
      )}

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]')}>
        <section aria-label="Waiting on you" className="overflow-hidden rounded-lg border bg-card">
          {isPending && items.length === 0 ? (
            <RowsSkeleton />
          ) : failed.length === Object.keys(SOURCE_LABEL).length ? (
            <ErrorState error={failed[0].error} onRetry={() => void refetch()} />
          ) : listed.length === 0 ? (
            <EmptyState title={show === 'all' ? 'Nothing waiting on you' : `No ${GROUP_LABEL[show].toLowerCase()} waiting`}>
              New problems, approvals and schedule warnings show up here as they happen.
            </EmptyState>
          ) : (
            <ul role="listbox" aria-label="Inbox items" className="rows-enter">
              {listed.map((item) => (
                <li
                  key={item.key}
                  id={`inbox-${item.key}`}
                  role="option"
                  aria-selected={item.key === selected?.key}
                  aria-hidden={leaving.has(item.key) || undefined}
                  className={cn(
                    'grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-[cubic-bezier(0.2,0.8,0.2,1)]',
                    leaving.has(item.key) && 'pointer-events-none grid-rows-[0fr] opacity-0',
                    returning.has(item.key) && 'starting:grid-rows-[0fr] starting:opacity-0',
                  )}
                >
                  <div
                    className={cn(
                      'flex min-h-0 overflow-hidden border-b',
                      fresh.has(item.key) && 'row-fresh',
                      picked.has(item.key) && 'bg-accent/60',
                    )}
                  >
                    {item.draft && (
                      <label className="grid shrink-0 cursor-pointer place-items-center pl-3" title="Pick to send or cancel with others">
                        <input
                          type="checkbox"
                          checked={picked.has(item.key)}
                          onChange={() => togglePick(item)}
                          aria-label={`Pick: ${item.title}`}
                          className="size-4 accent-foreground"
                        />
                      </label>
                    )}
                    <button
                      type="button"
                      onClick={() => select(item)}
                      className={cn(
                        'grid min-w-0 flex-1 grid-cols-[3px_minmax(0,1fr)_auto] items-start gap-3 py-2.5 pr-3 text-left hover:bg-muted/60',
                        item.draft && 'pl-2',
                        item.key === selected?.key && 'bg-accent hover:bg-accent',
                      )}
                    >
                      <span className={cn('h-full self-stretch rounded-r-sm', STRIPE[item.severity])} aria-hidden="true" />
                      <span className="grid min-w-0 gap-0.5">
                        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                          {item.kind}
                          {item.severity === 'critical' && <ToneBadge tone="bad">Critical</ToneBadge>}
                        </span>
                        <span className="truncate text-sm font-medium">{item.title}</span>
                        {item.meta && <span className="truncate text-xs text-muted-foreground">{item.meta}</span>}
                      </span>
                      <span className="pt-0.5 text-xs text-muted-foreground tabular-nums">{ago(item.at)}</span>
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <DetailPanel
          open={Boolean(selected)}
          onClose={() => select(undefined)}
          title={selected?.title ?? ''}
          description={selected ? `${selected.kind}${selected.at ? ` · ${dateTime(selected.at)}` : ''}` : undefined}
        >
          {selected && <ItemDetail key={selected.key} item={selected} actions={allowed(selected)} onDecide={decide} />}
        </DetailPanel>
      </div>
    </div>
  );
}

function ItemDetail({
  item,
  actions,
  onDecide,
}: {
  item: InboxItem;
  actions: InboxAction[];
  onDecide: (item: InboxItem, action: InboxAction, commit?: InboxAction['commit']) => void;
}) {
  const [explanation, setExplanation] = useState<{ explanation?: string; suggested_fix?: string } | null>(null);
  const [explaining, setExplaining] = useState(false);
  const viewOnly = actions.every((a) => a.href);

  async function explain() {
    if (!item.explainId) return;
    setExplaining(true);
    try {
      setExplanation(await explainProblem(item.explainId));
    } catch (error) {
      toast.error('No explanation this time', { description: error instanceof Error ? error.message : undefined });
    } finally {
      setExplaining(false);
    }
  }

  return (
    <div className="grid gap-4">
      {item.body && <p className="text-sm leading-relaxed text-pretty whitespace-pre-line text-foreground/90">{item.body}</p>}

      {item.explainId && (
        <div className="grid gap-2 border-l-2 border-info pl-3">
          {explanation ? (
            <>
              <p className="text-sm">{explanation.explanation}</p>
              {explanation.suggested_fix && (
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Suggested fix:</span> {explanation.suggested_fix}
                </p>
              )}
              <p className="text-xs text-muted-foreground">Written by the AI from the details above. Check before acting.</p>
            </>
          ) : (
            <Button variant="ghost" size="sm" className="justify-self-start text-info" onClick={explain} disabled={explaining}>
              {explaining ? <Loader2 className="animate-spin" /> : <Sparkles />}
              Explain in plain words
            </Button>
          )}
        </div>
      )}

      {item.draft?.email?.body && (
        <div className="grid gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">What will be sent</h3>
          <pre className="max-h-80 overflow-auto rounded-md border bg-muted/40 p-3 font-sans text-[13px] leading-relaxed whitespace-pre-wrap">
            {item.draft.email.body}
          </pre>
        </div>
      )}
      {item.draft && item.draft.why.length > 0 && (
        <div className="grid gap-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">Why it was drafted</h3>
          <ul className="grid list-disc gap-0.5 pl-5 text-sm text-foreground/90">
            {item.draft.why.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="rounded-md border p-3">
        <Facts items={item.facts} />
      </div>

      {item.output !== undefined && item.output !== null && (
        <div className="grid gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">What the AI produced</h3>
          <div className="rounded-md border bg-muted/40 p-3">
            <AiOutput feature={item.feature} output={item.output} />
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {actions.map((action) => (
          <Button key={action.id} size="sm" variant={action.primary ? 'default' : 'outline'} onClick={() => onDecide(item, action)}>
            {action.label}
            {action.primary && <Kbd className="bg-transparent text-current opacity-60">{item.draft ? 'Enter' : 'E'}</Kbd>}
            {action.hotkey && !action.primary && (
              <Kbd className="bg-transparent text-current opacity-60">{action.hotkey.toUpperCase()}</Kbd>
            )}
          </Button>
        ))}
      </div>
      {viewOnly && item.source !== 'alerts' && <p className="text-xs text-muted-foreground">Your role can see this but not act on it.</p>}
    </div>
  );
}

/** What an AI tool produced, laid out for a reviewer rather than as raw JSON. */
