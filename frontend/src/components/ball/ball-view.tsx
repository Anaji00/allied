'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { HandOver, ballCall, statusOf, waitText, type Ball } from '@/components/data/ball';
import { FilterChips, PanelHeading } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';

type Summary = {
  open: number;
  late: number;
  escalated: number;
  by_kind: {
    kind: string;
    label: string;
    count: number;
    late: number;
    oldest_days: number;
  }[];
  by_holder: {
    name: string;
    kind: string;
    count: number;
    late: number;
    oldest_days: number;
  }[];
  oldest: Ball[];
  rules: { nudge_days: number; escalate_days: number; leadership_days: number };
};

const VIEWS = ['all', 'me'] as const;
const KIND_LABEL: Record<string, string> = {
  order: 'Order',
  rfq: 'Quote request',
  vendor_job: 'Vendor job',
  shipment: 'Shipment',
  milestone: 'Milestone',
  board_item: 'Board item',
};

function Big({ label, value, tone }: { label: string; value: number; tone?: 'bad' | 'warn' }) {
  return (
    <div className="grid gap-0.5 rounded-lg border bg-card p-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-2xl font-semibold tabular-nums ${tone === 'bad' && value ? 'text-bad' : tone === 'warn' && value ? 'text-warn' : ''}`}
      >
        {value}
      </span>
    </div>
  );
}

/** Where everything is waiting: how many items, how late, who holds the most. */
export function BallView() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [view, setView] = useQueryState('who', parseAsStringLiteral(VIEWS).withDefault('all'));
  const [handing, setHanding] = useState<Ball | null>(null);
  const canWrite = hasRole('OPERATOR');

  const summary = useQuery({
    queryKey: ['ball', 'summary'],
    queryFn: async (): Promise<Summary | null> => {
      try {
        return await ballCall<Summary>('/summary');
      } catch (e) {
        const s = (e as { status?: number }).status;
        if (s === 404) return null;
        throw e;
      }
    },
    refetchInterval: 60_000,
    retry: false,
  });
  const list = useQuery({
    queryKey: ['ball', 'open', view],
    queryFn: () => ballCall<Ball[]>(`/open?who=${view}`),
    enabled: Boolean(summary.data),
    refetchInterval: 60_000,
    retry: false,
  });
  const done = useMutation({
    mutationFn: (b: Ball) =>
      ballCall('/resolve', 'POST', {
        item_type: b.item_type,
        item_ref: b.item_ref,
      }),
    onSuccess: () => {
      toast.success('Marked as done');
      void client.invalidateQueries({ queryKey: ['ball'] });
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  if (summary.isPending) return <RowsSkeleton rows={5} />;
  if (summary.isError) return <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />;
  if (!summary.data)
    return (
      <EmptyState title="Not available yet">Once it is switched on, this shows who each job is waiting on and for how long.</EmptyState>
    );
  const s = summary.data;
  const biggest = Math.max(1, ...s.by_kind.map((k) => k.count));

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4">
      <p className="text-sm text-muted-foreground">
        Every job waits on someone. They are reminded after {s.rules.nudge_days} working days, the owner is told after{' '}
        {s.rules.escalate_days}, and leadership after {s.rules.leadership_days}.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Big label="Waiting on someone" value={s.open} />
        <Big label="Late" value={s.late} tone="warn" />
        <Big label="Sent to a manager" value={s.escalated} tone="bad" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="grid content-start gap-2 rounded-lg border bg-card p-4">
          <PanelHeading>Where it is waiting</PanelHeading>
          {s.by_kind.length === 0 && <p className="text-sm text-muted-foreground">Nothing is waiting.</p>}
          {s.by_kind.map((k) => (
            <div key={k.kind} className="grid gap-1 text-[13px]">
              <div className="flex items-baseline gap-2">
                <span className="font-medium">{k.label}</span>
                <span className="ml-auto tabular-nums">{k.count}</span>
                {k.late > 0 && <span className="text-xs text-warn tabular-nums">{k.late} late</span>}
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label={`${k.count} with ${k.label}`}>
                <div className="h-full rounded-full bg-foreground/70" style={{ width: `${(k.count / biggest) * 100}%` }} />
              </div>
            </div>
          ))}
        </section>
        <section className="grid content-start gap-2 rounded-lg border bg-card p-4">
          <PanelHeading>Who is holding the most</PanelHeading>
          {s.by_holder.length === 0 && <p className="text-sm text-muted-foreground">Nobody yet.</p>}
          <ul className="grid gap-1.5 text-[13px]">
            {s.by_holder.slice(0, 8).map((h) => (
              <li key={`${h.kind}-${h.name}`} className="flex items-baseline gap-2">
                <span className="font-medium">{!h.name || h.name === '?' ? 'Nobody yet' : h.name}</span>
                <span className="text-xs text-muted-foreground">
                  oldest {h.oldest_days} {h.oldest_days === 1 ? 'day' : 'days'}
                </span>
                <span className="ml-auto tabular-nums">{h.count}</span>
                {h.late > 0 && <span className="text-xs text-warn tabular-nums">{h.late} late</span>}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={view}
          onChange={(v) => void setView(v === 'all' ? null : v)}
          options={[
            { value: 'all', label: 'Everything waiting' },
            { value: 'me', label: 'My turn' },
          ]}
        />
      </div>
      {list.isPending ? (
        <RowsSkeleton rows={4} />
      ) : (list.data ?? []).length === 0 ? (
        <EmptyState title={view === 'me' ? 'Nothing is waiting on you' : 'Nothing is waiting'}>
          Items show up here when someone is given the ball.
        </EmptyState>
      ) : (
        <ul className="divide-y rounded-lg border bg-card">
          {(list.data ?? []).map((b) => {
            const st = statusOf(b);
            return (
              <li key={`${b.item_type}-${b.item_ref}`} className="grid gap-1 p-3 text-[13px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{b.label || `${KIND_LABEL[b.item_type] ?? b.item_type} ${b.item_ref}`}</span>
                  <span className="text-xs text-muted-foreground">{KIND_LABEL[b.item_type] ?? b.item_type}</span>
                  <ToneBadge tone={st.tone}>{st.label}</ToneBadge>
                  {b.overdue && <ToneBadge tone="bad">Late</ToneBadge>}
                  {canWrite && (
                    <span className="ml-auto flex gap-1.5">
                      <Button size="xs" variant="outline" onClick={() => setHanding(b)}>
                        Pass to someone
                      </Button>
                      {!b.derived && (
                        <Button size="xs" variant="ghost" disabled={done.isPending} onClick={() => done.mutate(b)}>
                          Done
                        </Button>
                      )}
                    </span>
                  )}
                </div>
                <p className="text-muted-foreground">
                  With {b.ball_name || b.ball_person}
                  {b.ball_label ? `, ${b.ball_label}` : ''} · {waitText(b)}
                  {b.note ? ` · waiting for ${b.note}` : ''}
                </p>
              </li>
            );
          })}
        </ul>
      )}
      {handing && (
        <HandOver
          itemType={handing.item_type}
          itemRef={handing.item_ref}
          current={handing.derived ? null : handing}
          onClose={() => setHanding(null)}
        />
      )}
    </div>
  );
}
