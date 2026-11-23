'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { day } from '@/lib/format';

type Item = {
  po_id: number;
  po_number?: string | null;
  job_ref?: string | null;
  vendor: string;
  line_count: number;
  days_waiting?: number | null;
  last_asked_on?: string | null;
};
type Out = { total: number; items: Item[]; note?: string };

/** Orders that need a material test paper or certificate and have none attached, longest waiting first. It only reads. */
export function CertsToChase() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['certs-to-chase'],
    enabled: hasRole('OPERATOR'),
    retry: false,
    queryFn: async () => {
      const res = await authFetch('/api/v1/compliance/certs-to-chase', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json().catch(() => null)) as Out | null) : null;
    },
  });
  if (!hasRole('OPERATOR') || !q.data) return null;
  const items = [...(q.data.items ?? [])].sort((a, b) => (b.days_waiting ?? 0) - (a.days_waiting ?? 0));
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Certificates to chase">
      <h2 className="text-sm font-semibold">Certificates to chase</h2>
      {items.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">No order is waiting on a certificate.</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            Orders that need a material test paper or certificate and have none attached. {q.data.note ?? ''}
          </p>
          <ul className="grid divide-y rounded-md border text-[13px]">
            {items.map((i) => (
              <li key={i.po_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5">
                <span className="font-medium">{i.vendor}</span>
                {i.job_ref ? (
                  <Link href={`/jobs/${encodeURIComponent(i.job_ref)}`} className="text-xs underline underline-offset-2">
                    PO {i.po_number ?? i.po_id}
                  </Link>
                ) : (
                  <span className="text-xs text-muted-foreground">PO {i.po_number ?? i.po_id}</span>
                )}
                <span className="text-xs text-muted-foreground">
                  {i.line_count} line{i.line_count === 1 ? '' : 's'} need a certificate
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                  {i.days_waiting != null ? `${i.days_waiting} day${i.days_waiting === 1 ? '' : 's'} waiting` : ''}
                  {i.last_asked_on ? ` · asked ${day(i.last_asked_on)}` : ' · not asked yet'}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
