'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { date, day, moneyOrig } from '@/lib/format';

type Due = {
  id: number;
  label: string;
  amount: string;
  trigger?: string;
  due_on?: string | null;
  triggered_at?: string | null;
  vendor?: string;
  currency?: string;
  po_id: number;
  po_number?: string | null;
  job_ref?: string | null;
};

/** For managers: every vendor payment step that is due and not yet paid, oldest first, in each vendor's own currency. It only reads. */
export function PaymentsDue() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['payments', 'due-list'],
    enabled: hasRole('MANAGER'),
    retry: false,
    queryFn: async () => {
      const res = await authFetch('/api/v1/payments/due', {
        cache: 'no-store',
      }).catch(() => null);
      if (!res?.ok) return null;
      const body = (await res.json().catch(() => null)) as {
        due?: Due[];
      } | null;
      return body?.due ?? null;
    },
  });
  if (!hasRole('MANAGER') || !q.data) return null;
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Vendor payments due">
      <h2 className="text-sm font-semibold">Vendor payments due</h2>
      {q.data.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Nothing is due right now.</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            Press Open the job on a row to record the payment there. Recording it keeps the payment sheet in step.
          </p>
          <ul className="grid divide-y rounded-md border text-[13px]">
            {q.data.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5">
                <span className="font-medium">{d.vendor ?? 'Vendor'}</span>
                <span className="text-xs text-muted-foreground">{d.label}</span>
                {d.job_ref && (
                  <Link href={`/jobs/${encodeURIComponent(d.job_ref)}`} className="text-xs underline underline-offset-2">
                    Open the job{d.po_number ? ` (PO ${d.po_number})` : ''}
                  </Link>
                )}
                <span className="ml-auto text-xs text-muted-foreground">
                  {d.due_on ? `due ${day(d.due_on)}` : d.triggered_at ? `since ${date(d.triggered_at)}` : ''}
                </span>
                <span className="tabular-nums">{moneyOrig(d.amount, d.currency)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
