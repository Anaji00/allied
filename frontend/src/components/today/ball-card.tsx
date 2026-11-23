'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { authFetch } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';

type Summary = {
  open: number;
  late: number;
  escalated: number;
  late_means?: string;
  no_owner_set?: { label?: string; count?: number } | null;
  by_holder: { name: string; kind?: string; count: number; late?: number; oldest_days?: number; waiting_days?: number }[];
  holders?: { name: string; kind?: string; count: number; late?: number; waiting_days?: number; oldest_days?: number }[];
  oldest: { item_type: string; item_ref: string; label: string; ball_name?: string; days_waiting?: number; overdue?: boolean }[];
};

const days = (n?: number) => (n == null ? '' : n < 1 ? 'today' : `${Math.round(n)} day${Math.round(n) === 1 ? '' : 's'}`);

/** For managers: who holds the ball on how many things, and what has waited longest. */
export function BallCard() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['ball', 'summary'],
    queryFn: async (): Promise<Summary | null> => {
      const res = await authFetch('/api/v1/ball/summary', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Summary) : null;
    },
    enabled: hasRole('MANAGER'),
    refetchInterval: 120_000,
    retry: false,
  });
  const s = q.data;
  if (!hasRole('MANAGER') || !s || s.open === 0) return null;
  return (
    <section className="grid gap-2 rounded-lg border bg-card px-4 py-3" aria-label="Who owes what">
      <p className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
        <span>
          {s.open} thing{s.open === 1 ? ' is' : 's are'} waiting on someone else
          {s.late > 0 ? `, ${s.late} of them late` : ''}.
        </span>
        <Link href="/chase" className="text-xs font-normal underline underline-offset-2">
          See who owes us
        </Link>
      </p>
      {(s.holders ?? s.by_holder).length > 0 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted-foreground">
          {(s.holders ?? s.by_holder).slice(0, 6).map((h) => {
            const days = h.waiting_days ?? h.oldest_days;
            return (
              <li key={h.name}>
                {h.name === '?' || !h.name ? 'No owner yet' : h.name}: {h.count} thing{h.count === 1 ? '' : 's'}
                {days != null && days >= 1 ? `, waiting up to ${Math.round(days)} days` : ''}
              </li>
            );
          })}
        </ul>
      )}
      {s.no_owner_set && (s.no_owner_set.count ?? 0) > 0 && (
        <p className="text-[13px] text-muted-foreground">
          {s.no_owner_set.label ?? 'No owner set'}: {s.no_owner_set.count}
        </p>
      )}
      {s.late_means && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">What does “late” mean here?</summary>
          <p className="mt-1">{s.late_means}</p>
        </details>
      )}
      {s.oldest.length > 0 && (
        <ul className="grid gap-0.5 text-[13px]">
          {s.oldest.slice(0, 3).map((o) => (
            <li key={`${o.item_type}-${o.item_ref}`}>
              <Link href={`/chase`} className="underline-offset-2 hover:underline">
                {o.label}
              </Link>
              <span className="text-muted-foreground">
                {o.ball_name
                  ? o.label.toLowerCase().includes(o.ball_name.toLowerCase())
                    ? ', waiting on the vendor'
                    : `, waiting on ${o.ball_name}`
                  : ''}
                {o.days_waiting != null ? `, ${days(o.days_waiting)}` : ''}
                {o.overdue ? ', overdue' : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
