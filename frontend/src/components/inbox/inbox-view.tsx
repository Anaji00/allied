'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { parseAsBoolean, parseAsStringLiteral, useQueryState } from 'nuqs';
import { useHotkeys } from 'react-hotkeys-hook';
import { toast } from 'sonner';
import { Loader2, Sparkles } from 'lucide-react';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
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
import {
  explainProblem,
  inboxPost,
  useInbox,
  useRefreshSource,
  type InboxAction,
  type InboxItem,
} from './use-inbox';

const GROUPS = ['all', 'approvals', 'problems', 'schedule'] as const;
const GROUP_LABEL: Record<(typeof GROUPS)[number], string> = {
  all: 'All',
  approvals: 'Approvals',
  problems: 'Problems',
  schedule: 'Schedule',
};
const SOURCE_LABEL = { alerts: 'alerts', problems: 'order problems', ai: 'AI results', monday: 'monday.com events' };

const STRIPE = { critical: 'bg-bad', warning: 'bg-warn', info: 'bg-info' } as const;

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

  // Decided items leave the list at once; Undo brings them back.
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const visible = useMemo(() => items.filter((i) => !hidden.has(i.key)), [items, hidden]);
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

  function decide(item: InboxItem, action: InboxAction, commit = action.commit) {
    if (action.href) {
      router.push(action.href);
      return;
    }
    if (!commit) return;
    const next = shown[index + 1] ?? shown[index - 1];
    setHidden((h) => new Set(h).add(item.key));
    // Wide screens move straight on. On narrow ones the sheet is modal and would
    // block the toast's Undo button, so it closes instead.
    if (selected?.key === item.key) select(wide ? next : undefined);
    const restore = () =>
      setHidden((h) => {
        const n = new Set(h);
        n.delete(item.key);
        return n;
      });
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
  const primary = allowed(selected).find((a) => a.primary && !a.needsPrice);

  useHotkeys(['j', 'ArrowDown'], () => shown[index + 1] && select(shown[index + 1]), { preventDefault: true }, [shown, index]);
  useHotkeys(['k', 'ArrowUp'], () => shown[index - 1] && select(shown[index - 1]), { preventDefault: true }, [shown, index]);
  useHotkeys('e', () => selected && primary && decide(selected, primary), [selected, primary]);
  useHotkeys(
    ['a', 'r'],
    (e) => {
      const action = allowed(selected).find((a) => a.hotkey === e.key.toLowerCase());
      if (selected && action) decide(selected, action);
    },
    [selected],
  );
  useHotkeys('enter', () => {
    const open = allowed(selected).find((a) => a.href);
    if (open) router.push(open.href!);
  }, [selected]);

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
          <Kbd>R</Kbd> approve or reject · <Kbd>Enter</Kbd> open
        </p>
      </div>

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
          ) : failed.length === 4 ? (
            <ErrorState error={failed[0].error} onRetry={() => void refetch()} />
          ) : shown.length === 0 ? (
            <EmptyState title={show === 'all' ? 'Nothing waiting on you' : `No ${GROUP_LABEL[show].toLowerCase()} waiting`}>
              New problems, approvals and schedule warnings show up here as they happen.
            </EmptyState>
          ) : (
            <ul role="listbox" aria-label="Inbox items">
              {shown.map((item) => (
                <li key={item.key} id={`inbox-${item.key}`} role="option" aria-selected={item.key === selected?.key}>
                  <button
                    type="button"
                    onClick={() => select(item)}
                    className={cn(
                      'grid w-full grid-cols-[3px_minmax(0,1fr)_auto] items-start gap-3 border-b py-2.5 pr-3 text-left last:border-b-0 hover:bg-muted/60',
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
  const [pricing, setPricing] = useState<InboxAction | null>(null);
  const [price, setPrice] = useState('');
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

      {pricing ? (
        <form
          className="grid gap-2 rounded-md border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            const value = Number(price);
            if (!(value > 0)) return;
            onDecide(item, pricing, (keepalive) =>
              inboxPost(
                `/exceptions/${encodeURIComponent(item.explainId!)}/resolve`,
                { action_type: pricing.id, new_unit_price: value, resolution_notes: `Price approved at ${value} from the Inbox` },
                keepalive,
              ),
            );
          }}
        >
          <label htmlFor="inbox-price" className="text-sm font-medium">
            New unit price ($)
          </label>
          <Input id="inbox-price" type="number" step="0.01" min="0.01" required autoFocus value={price} onChange={(e) => setPrice(e.target.value)} />
          <div className="flex gap-2">
            <Button type="submit" size="sm">
              Approve price
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setPricing(null)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => (
            <Button
              key={action.id}
              size="sm"
              variant={action.primary ? 'default' : 'outline'}
              onClick={() => (action.needsPrice ? setPricing(action) : onDecide(item, action))}
            >
              {action.label}
              {action.primary && !action.needsPrice && <Kbd className="bg-transparent text-current opacity-60">E</Kbd>}
              {action.hotkey && !action.primary && <Kbd className="bg-transparent text-current opacity-60">{action.hotkey.toUpperCase()}</Kbd>}
            </Button>
          ))}
        </div>
      )}
      {viewOnly && item.source !== 'alerts' && (
        <p className="text-xs text-muted-foreground">Your role can see this but not act on it.</p>
      )}
    </div>
  );
}

/** What an AI tool produced, laid out for a reviewer rather than as raw JSON. */
