'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';
import { useUndoQueue } from '@/lib/undo';
import { cn } from '@/lib/utils';

type Action = { label: string; method: string; path: string; body?: unknown };
type Row = {
  id: string;
  kind: 'ball' | 'chase';
  who: string;
  role?: string;
  owes?: string;
  what: string;
  days_waiting?: number;
  age?: string;
  link?: string;
  overdue?: boolean;
  draft_id?: string | number | null;
  draft_summary?: string;
  send?: Action | null;
  dismiss?: Action | null;
};
type Chase = { total: number; with_draft: number; items: Row[] };

/**
 * Everyone who owes us something, oldest first: vendors, customers, carriers. Where the app has already written a follow-up,
 * press Send or Dismiss on the line. A follow-up goes out only when you press Send, with a few seconds to undo.
 */
export function ChaseView() {
  const client = useQueryClient();
  const undo = useUndoQueue();
  const [gone, setGone] = useState<Set<string>>(() => new Set());
  const q = useQuery({
    queryKey: ['chase'],
    queryFn: async (): Promise<Chase | null> => {
      const res = await authFetch('/api/v1/chase', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Chase) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  });

  function run(row: Row, a: Action, verb: string) {
    setGone((g) => new Set(g).add(row.id));
    const back = () =>
      setGone((g) => {
        const n = new Set(g);
        n.delete(row.id);
        return n;
      });
    undo(`${verb}: ${row.who}, ${row.what}`, {
      dropOnLeave: /send/i.test(verb),
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
        void client.invalidateQueries({ queryKey: ['chase'] });
        void client.invalidateQueries({ queryKey: ['today'] });
      },
      onUndo: back,
      onError: (e) => {
        back();
        toast.error('That didn’t go through', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  if (q.isPending) return <RowsSkeleton rows={6} />;
  if (!q.data) return <EmptyState title="Not available yet">This list shows here once it is switched on.</EmptyState>;
  const rows = q.data.items.filter((r) => !gone.has(r.id));
  return (
    <div className="grid min-w-0 gap-4">
      <p className="text-sm text-muted-foreground">
        {rows.length === 0
          ? 'Nobody owes us anything right now.'
          : `${rows.length} ${rows.length === 1 ? 'person owes' : 'people owe'} us something, oldest first.${q.data.with_draft ? ` ${q.data.with_draft} already ${q.data.with_draft === 1 ? 'has' : 'have'} a follow-up written for you to send.` : ''}`}
      </p>
      <ul className="grid divide-y rounded-lg border bg-card" aria-label="People who owe us something">
        {rows.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5">
            <span className={cn('size-2 shrink-0 rounded-full', r.overdue ? 'bg-bad' : 'bg-border')} aria-hidden />
            <div className="grid min-w-0 flex-1 gap-0.5">
              <span className="text-[13px] font-medium">
                {r.who}
                {r.role ? <span className="font-normal text-muted-foreground"> ({r.role})</span> : null}: {r.what}
              </span>
              <span className="text-xs text-muted-foreground">
                {[r.owes, r.age ?? (r.days_waiting != null ? `${r.days_waiting} days` : ''), r.draft_summary].filter(Boolean).join(' · ')}
              </span>
            </div>
            {r.send && (
              <Button size="sm" onClick={() => run(r, r.send!, 'Sending')}>
                {r.send.label}
              </Button>
            )}
            {r.dismiss && (
              <Button size="sm" variant="ghost" onClick={() => run(r, r.dismiss!, 'Dismissing')}>
                {r.dismiss.label}
              </Button>
            )}
            {r.link && (
              <Link href={r.link} className="text-xs text-muted-foreground underline-offset-2 hover:underline">
                Open
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
