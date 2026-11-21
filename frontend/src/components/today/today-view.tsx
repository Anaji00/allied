'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { MoreHorizontal } from 'lucide-react';
import { RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { useInbox } from '@/components/inbox/use-inbox';
import { BallCard } from './ball-card';
import { InboxView } from '@/components/inbox/inbox-view';
import { authFetch } from '@/lib/api';
import { useUndoQueue } from '@/lib/undo';
import { cn } from '@/lib/utils';

type Action = { label: string; method: 'POST' | 'LINK' | string; path: string; body?: unknown };
type Row = {
  id: string;
  kind: string;
  sentence: string;
  reason?: string;
  primary_action?: Action | null;
  other_actions?: Action[];
  link?: string;
  age?: string;
  severity?: 'high' | 'normal' | 'low';
};
type MyDay = { total: number; need_action?: number; items: Row[] };
type Brief = { headline: string; count: number; items: { sentence: string; link?: string }[] };

/**
 * Today: one list of everything waiting on you, oldest and most urgent first. Each line says what it is, where it came from,
 * and has one button for the next step. A line goes away when it is done. The detailed view with filters is one click away.
 */
export function TodayView() {
  const router = useRouter();
  const client = useQueryClient();
  const undo = useUndoQueue();
  const [detailed, setDetailed] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);
  const [gone, setGone] = useState<Set<string>>(() => new Set());

  const day = useQuery({
    queryKey: ['today', 'my-day'],
    queryFn: async (): Promise<MyDay | null> => {
      const res = await authFetch('/api/v1/my-day', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as MyDay) : null;
    },
    refetchInterval: 30_000,
    retry: false,
  });
  const brief = useQuery({
    queryKey: ['today', 'brief'],
    queryFn: async (): Promise<Brief | null> => {
      const res = await authFetch('/api/v1/brief', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Brief) : null;
    },
    staleTime: 5 * 60_000,
    retry: false,
  });

  // An email waiting for a person's OK: find it by the id in the row's button, so the row can say who it goes to and what it says.
  const inbox = useInbox();
  const draftOf = (r: Row) => {
    const id = /\/assistant\/actions\/([^/?]+)/.exec(r.primary_action?.path ?? '')?.[1] ?? r.id;
    return inbox.items.find((i) => i.source === 'drafts' && i.draft && (i.draft.id === id || i.draft.id === r.id))?.draft?.email;
  };

  function run(row: Row, a: Action) {
    if (a.method === 'LINK') return router.push(a.path);
    // A change is shown as done at once, with a few seconds to undo, then sent.
    setGone((g) => new Set(g).add(row.id));
    const mail = draftOf(row);
    undo(`${a.label}${mail?.to ? ` to ${mail.to}` : ''}: ${row.sentence}`, {
      commit: async (keepalive) => {
        const res = await authFetch(a.path, {
          method: a.method,
          headers: a.body ? { 'Content-Type': 'application/json' } : undefined,
          body: a.body ? JSON.stringify(a.body) : undefined,
          keepalive,
        });
        if (!res.ok) {
          const d = await res
            .json()
            .then((j) => j?.detail)
            .catch(() => null);
          throw new Error(typeof d === 'string' ? d : `That didn’t work (${res.status}).`);
        }
        void client.invalidateQueries({ queryKey: ['today'] });
        void client.invalidateQueries({ queryKey: ['inbox'] });
      },
      onUndo: () =>
        setGone((g) => {
          const n = new Set(g);
          n.delete(row.id);
          return n;
        }),
      onError: (e) => {
        setGone((g) => {
          const n = new Set(g);
          n.delete(row.id);
          return n;
        });
        toast.error('That didn’t go through', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  function dismissAll(list: Row[]) {
    setGone((g) => new Set([...g, ...list.map((r) => r.id)]));
    const back = () => setGone((g) => new Set([...g].filter((id) => !list.some((r) => r.id === id))));
    undo(`Marking ${list.length} older notices handled`, {
      commit: async (keepalive) => {
        for (const r of list) {
          const a = r.primary_action!;
          const res = await authFetch(a.path, {
            method: a.method,
            headers: a.body ? { 'Content-Type': 'application/json' } : undefined,
            body: a.body ? JSON.stringify(a.body) : undefined,
            keepalive,
          });
          if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
        }
        void client.invalidateQueries({ queryKey: ['today'] });
        void client.invalidateQueries({ queryKey: ['inbox'] });
      },
      onUndo: back,
      onError: (e) => {
        back();
        toast.error('That didn’t go through', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  if (day.isPending) return <RowsSkeleton rows={6} />;
  // An older server has no "my day" list: show the detailed view so nothing is lost.
  if (!day.data || detailed) {
    return (
      <div className="grid min-w-0 gap-3">
        {day.data && (
          <button
            type="button"
            className="justify-self-start text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => setDetailed(false)}
          >
            Back to my day
          </button>
        )}
        <InboxView />
      </div>
    );
  }
  const visible = day.data.items.filter((r) => !gone.has(r.id));
  // Safety net: identical sentences show once with a count.
  const seen = new Map<string, number>();
  const unique: Row[] = [];
  const calm = (r: Row) => r.severity === 'low';
  for (const r of visible) {
    const n = seen.get(r.sentence) ?? 0;
    seen.set(r.sentence, n + 1);
    if (n === 0) unique.push(r);
  }
  unique.sort((a, b) => Number(calm(a)) - Number(calm(b)));
  const times = (r: Row) => seen.get(r.sentence) ?? 1;
  // Old "for your information" lines that only need to be marked handled go in one row.
  const days = (r: Row) => Number(/(\d+)\s*day/i.exec(r.age ?? '')?.[1] ?? 0);
  const isNotice = (r: Row) => r.primary_action?.label === 'Mark handled' && r.severity !== 'high' && days(r) >= 7;
  const notices = unique.filter(isNotice);
  const rows = notices.length >= 3 ? unique.filter((r) => !isNotice(r)) : unique;
  const grouped = notices.length >= 3 ? notices : [];
  const decisions = day.data.need_action ?? rows.filter((r) => !calm(r)).length;
  const reasonOf = (r: Row) => {
    const why = (r.reason ?? '').replace(/\s*\([^)]*\)/g, '').trim();
    const age = r.age ? (/^\d+\s*(day|hour|minute|week)/i.test(r.age) ? `${r.age} old` : r.age) : '';
    return [/^an alert for you$/i.test(why) ? '' : why, age].filter(Boolean).join(' · ');
  };
  return (
    <div className="grid min-w-0 gap-4">
      {brief.data && brief.data.headline && (
        <section className="grid gap-1.5 rounded-lg border bg-card px-4 py-3" aria-label="Your day">
          <p className="text-sm font-medium">{brief.data.headline}</p>
          {brief.data.items.length > 0 && (
            <ul className="grid gap-0.5 text-[13px] text-muted-foreground">
              {brief.data.items.slice(0, 5).map((i, n) => (
                <li key={n}>
                  {i.link ? (
                    <Link href={i.link} className="underline-offset-2 hover:underline">
                      {i.sentence}
                    </Link>
                  ) : (
                    i.sentence
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {(rows.length > 0 || grouped.length > 0) && (
        <p className="text-sm text-muted-foreground">
          {visible.length} {visible.length === 1 ? 'thing' : 'things'} waiting
          {decisions < visible.length ? `; ${decisions} for you to act on` : ''}.
        </p>
      )}
      <BallCard />
      {rows.length === 0 && grouped.length === 0 ? (
        <p className="rounded-lg border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          Nothing is waiting on you. Enjoy the quiet.
        </p>
      ) : (
        <ul className="grid divide-y rounded-lg border bg-card" aria-label="Waiting on you">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5">
              <span className={cn('size-2 shrink-0 rounded-full', r.severity === 'high' ? 'bg-bad' : 'bg-border')} aria-hidden />
              <div className="grid min-w-0 flex-1 gap-0.5">
                <span className="text-[13px] font-medium">
                  {r.sentence.charAt(0).toUpperCase() + r.sentence.slice(1)}
                  {times(r) > 1 && <span className="ml-1.5 font-normal text-muted-foreground">×{times(r)}</span>}
                </span>
                <span className="text-xs text-muted-foreground">{reasonOf(r)}</span>
                {(() => {
                  const mail = draftOf(r);
                  if (!mail) return null;
                  const first = (mail.body ?? '').split(/\r?\n/).find((l) => l.trim()) ?? '';
                  return (
                    <details className="text-xs text-muted-foreground">
                      <summary className="cursor-pointer">
                        To {mail.to || 'nobody yet'}
                        {first ? `: ${first.length > 90 ? `${first.slice(0, 90)}…` : first}` : ''}
                      </summary>
                      <p className="mt-1 font-medium text-foreground">{mail.subject}</p>
                      <pre className="mt-1 max-h-48 overflow-auto rounded bg-muted/50 p-2 font-sans whitespace-pre-wrap">{mail.body}</pre>
                      <p className="mt-1">Nothing is sent until you press the button and the few seconds to undo pass.</p>
                    </details>
                  );
                })()}
              </div>
              {r.primary_action && (
                <Button
                  size="sm"
                  variant={/send/i.test(r.primary_action.label) && armed === r.id ? 'destructive' : 'default'}
                  onClick={() => {
                    // Anything that sends an email asks once more, so one stray click cannot send it.
                    if (/send/i.test(r.primary_action!.label) && armed !== r.id) return setArmed(r.id);
                    setArmed(null);
                    run(r, r.primary_action!);
                  }}
                  onBlur={() => setArmed((a) => (a === r.id ? null : a))}
                >
                  {/send/i.test(r.primary_action.label) && armed === r.id ? 'Click again to send' : r.primary_action.label}
                </Button>
              )}
              {(r.other_actions?.length ?? 0) > 0 || r.link ? (
                <DropdownMenu>
                  <DropdownMenuTrigger
                    aria-label="More for this line"
                    className="grid size-8 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <MoreHorizontal className="size-4" />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {r.link && <DropdownMenuItem onClick={() => router.push(r.link!)}>Open it</DropdownMenuItem>}
                    {(r.other_actions ?? []).map((a) => (
                      <DropdownMenuItem key={a.label} onClick={() => run(r, a)}>
                        {a.label}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
            </li>
          ))}
          {grouped.length > 0 && (
            <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5">
              <span className="size-2 shrink-0 rounded-full bg-border" aria-hidden />
              <div className="grid min-w-0 flex-1 gap-0.5">
                <span className="text-[13px] font-medium">{grouped.length} older notices</span>
                <span className="text-xs text-muted-foreground">Nothing to decide, 7 days old or more</span>
              </div>
              <Button size="sm" variant="outline" onClick={() => dismissAll(grouped)}>
                Dismiss all
              </Button>
            </li>
          )}
        </ul>
      )}
      <button
        type="button"
        className="justify-self-start text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        onClick={() => setDetailed(true)}
      >
        Show the detailed view with filters
      </button>
      <Link
        href="/chase"
        className="justify-self-start text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      >
        See who owes us something
      </Link>
    </div>
  );
}
