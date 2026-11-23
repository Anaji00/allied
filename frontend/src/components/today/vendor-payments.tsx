'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ToneBadge } from '@/components/data/tone-badge';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { day, moneyOrig } from '@/lib/format';

type Step = {
  id: number;
  seq: number;
  label: string;
  percent?: string | number;
  amount: string;
  status: 'PENDING' | 'DUE' | 'PAID' | string;
  due_on?: string | null;
};
type Schedule = {
  id: number;
  po_id: number;
  po_number?: string | null;
  job_ref?: string | null;
  vendor: string;
  currency?: string | null;
  terms_code?: string | null;
  total: string;
  paid: string;
  remaining: string;
  next_step: Step | null;
  steps: Step[];
};
type Out = { schedules: Schedule[]; vendors: { vendor: string; po_count: number; next_due_on?: string | null }[] };

const WORD: Record<string, string> = { PENDING: 'Not due yet', DUE: 'Due now', PAID: 'Paid' };

/**
 * For managers: what is owed to each vendor, grouped by vendor with its payment steps (such as 30/50/20), what is paid and left,
 * and the next step. Amounts stay in each vendor's own currency, so nothing from different currencies is added together.
 */
export function VendorPayments() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['payments', 'schedules'],
    enabled: hasRole('MANAGER'),
    retry: false,
    queryFn: async () => {
      const res = await authFetch('/api/v1/payments/schedules', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json().catch(() => null)) as Out | null) : null;
    },
  });
  if (!hasRole('MANAGER') || !q.data) return null;
  const byVendor = new Map<string, Schedule[]>();
  for (const s of q.data.schedules ?? []) byVendor.set(s.vendor, [...(byVendor.get(s.vendor) ?? []), s]);
  if (byVendor.size === 0) return null;
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Payments by vendor">
      <h2 className="text-sm font-semibold">Payments by vendor</h2>
      <p className="text-xs text-muted-foreground">
        Each vendor’s payment steps, what is paid and what is left, and the next step. Open the order to record a payment.
      </p>
      <div className="grid gap-3">
        {[...byVendor.entries()].map(([vendor, list]) => (
          <div key={vendor} className="grid gap-1 rounded-md border">
            <p className="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-1.5 text-[13px] font-medium">{vendor}</p>
            <ul className="grid divide-y text-[13px]">
              {list.map((s) => {
                const n = s.next_step;
                return (
                  <li key={s.id} className="grid gap-1 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      {s.job_ref ? (
                        <Link href={`/jobs/${encodeURIComponent(s.job_ref)}`} className="font-medium underline underline-offset-2">
                          PO {s.po_number ?? s.po_id}
                        </Link>
                      ) : (
                        <span className="font-medium">PO {s.po_number ?? s.po_id}</span>
                      )}
                      <span className="text-xs text-muted-foreground">Terms {s.terms_code ?? 'not set'}</span>
                      <span className="ml-auto tabular-nums text-xs">
                        paid {moneyOrig(s.paid, s.currency || 'USD')} of {moneyOrig(s.total, s.currency || 'USD')} ·{' '}
                        {moneyOrig(s.remaining, s.currency || 'USD')} left
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {s.steps.map((st) => (
                        <ToneBadge key={st.id} tone={st.status === 'PAID' ? 'ok' : st.status === 'DUE' ? 'warn' : 'neutral'}>
                          {st.label} · {WORD[st.status] ?? st.status}
                        </ToneBadge>
                      ))}
                    </div>
                    {n ? (
                      <p className="text-xs text-muted-foreground">
                        Next: {n.label}, {moneyOrig(n.amount, s.currency || 'USD')}
                        {n.status === 'DUE' ? ', due now' : n.due_on ? `, due ${day(n.due_on)}` : ', when its step is reached'}.
                      </p>
                    ) : (
                      <p className="text-xs text-ok">All steps paid.</p>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
