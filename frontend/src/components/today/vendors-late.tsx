'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { ToneBadge } from '@/components/data/tone-badge';
import { fetchVendorJobs } from '@/lib/opsApi';
import { day } from '@/lib/format';

type Job = {
  id: number;
  vendor: string;
  description?: string | null;
  po_number?: string | null;
  promised_on?: string | null;
  state?: string;
  watchdog?: {
    tier: 'OVERDUE' | 'APPROACHING_3' | 'APPROACHING_7';
    days_left?: number | null;
    due_on?: string | null;
  } | null;
};

/** Vendors whose promised date has passed or is close, most late first. It only reads; the Vendors page does the follow-up. */
export function VendorsLate() {
  const q = useQuery({
    queryKey: ['today', 'vendors-late'],
    retry: false,
    refetchInterval: 120_000,
    queryFn: async () =>
      (
        (await fetchVendorJobs(false).catch(() => null)) as {
          jobs?: Job[];
        } | null
      )?.jobs ?? null,
  });
  if (!q.data) return null;
  const rank = { OVERDUE: 0, APPROACHING_3: 1, APPROACHING_7: 2 } as const;
  const rows = q.data
    .filter((j) => j.watchdog)
    .sort((a, b) => rank[a.watchdog!.tier] - rank[b.watchdog!.tier] || (a.watchdog!.days_left ?? 0) - (b.watchdog!.days_left ?? 0));
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Vendors past or near their date">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Vendors past or near their date</h2>
        <Link href="/contacts?section=vendors" className="ml-auto text-xs underline underline-offset-2">
          Open the vendors page
        </Link>
      </div>
      {rows.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">No vendor is late or due within a week.</p>
      ) : (
        <ul className="grid divide-y rounded-md border text-[13px]">
          {rows.slice(0, 10).map((j) => {
            const w = j.watchdog!;
            const left = w.days_left;
            return (
              <li key={j.id}>
                <Link
                  href={`/contacts?section=vendors&job=${j.id}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 hover:bg-muted/40"
                  title="Open this order to follow up"
                >
                  <span className="font-medium">{j.vendor}</span>
                  <span className="min-w-0 truncate text-xs text-muted-foreground">
                    {[j.po_number, j.description].filter(Boolean).join(' · ')}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {w.due_on || j.promised_on ? `promised ${day(w.due_on ?? j.promised_on)}` : ''}
                  </span>
                  <ToneBadge tone={w.tier === 'OVERDUE' ? 'bad' : 'warn'}>
                    {w.tier === 'OVERDUE'
                      ? left != null
                        ? `${Math.abs(left)} day${Math.abs(left) === 1 ? '' : 's'} late`
                        : 'Late'
                      : left != null
                        ? left <= 0
                          ? 'due today'
                          : `due in ${left} day${left === 1 ? '' : 's'}`
                        : 'Due soon'}
                  </ToneBadge>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
