'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { authFetch } from '@/lib/api';
import { fetchVendorJobs } from '@/lib/opsApi';
import { cn } from '@/lib/utils';

type Vendors = { counts?: Record<string, number>; otd_target?: number; jobs?: { status?: string | null }[] };
type OrderRows = {
  min_margin_pct?: number;
  items?: { total_expense?: string | number | null; gross_margin_pct?: string | number | null }[];
};
type Rfqs = { metrics?: { overdue?: number; win_rate?: number | null; open_by_age?: Record<string, number> } };
type Book = { count: number };

async function getJson<T>(path: string): Promise<T | null> {
  const res = await authFetch(path, { cache: 'no-store' }).catch(() => null);
  return res?.ok ? ((await res.json()) as T) : null;
}

// undefined while still loading (shown as …), null when the server cannot say (shown as –).
type Tile = { label: string; value: number | string | null | undefined; note: string; href: string; bad?: boolean };

/**
 * One page of what is late or stuck, so the owner sees it in seconds. Each tile opens the list behind it.
 * Tiles that need data the app does not have yet say so instead of showing a made-up number.
 */
export function LateUrgent() {
  const vendors = useQuery({
    queryKey: ['late', 'vendors'],
    queryFn: async () => (await fetchVendorJobs(false).catch(() => null)) as Vendors | null,
    retry: false,
    refetchInterval: 120_000,
  }).data;
  const rfqs = useQuery({
    queryKey: ['late', 'rfqs'],
    queryFn: () => getJson<Rfqs>('/api/v1/rfqs?open_only=true&limit=1'),
    retry: false,
    refetchInterval: 120_000,
  }).data;
  const everyJob = useQuery({
    queryKey: ['late', 'vendors-all'],
    queryFn: async () => (await fetchVendorJobs(true).catch(() => null)) as Vendors | null,
    retry: false,
    refetchInterval: 120_000,
  }).data;
  const orders = useQuery({
    queryKey: ['late', 'orders'],
    queryFn: () => getJson<OrderRows>('/api/v1/orders?limit=200'),
    retry: false,
    refetchInterval: 120_000,
  }).data;
  const waiting = useQuery({
    queryKey: ['late', 'jobs'],
    queryFn: () => getJson<Book>('/api/v1/book?area=jobs&late=true&limit=1'),
    retry: false,
    refetchInterval: 120_000,
  }).data;

  const c = vendors?.counts;
  const awaitingInvoice = everyJob?.jobs
    ? everyJob.jobs.filter((j) => /awaiting invoice/i.test(j.status ?? '')).length
    : everyJob === undefined
      ? undefined
      : null;
  // Margin needs the cost, which only managers receive, and an order with no cost entered is not a margin of 100%.
  const costed = (orders?.items ?? []).filter((o) => Number(o.total_expense) > 0 && o.gross_margin_pct != null);
  const underFloor =
    orders === undefined
      ? undefined
      : orders && costed.length
        ? costed.filter((o) => Number(o.gross_margin_pct) < (orders.min_margin_pct ?? 15)).length
        : null;
  const old = rfqs?.metrics?.open_by_age;
  const quotesOld = old ? Object.entries(old).reduce((n, [k, v]) => (/^(8|9|1\d|2|3|over|>)/i.test(k) ? n + v : n), 0) : null;
  const win = rfqs?.metrics?.win_rate;
  const tiles: Tile[] = [
    {
      label: 'Orders late or slipping',
      value: c ? (c.late ?? 0) + (c.will_miss ?? 0) : vendors === undefined ? undefined : null,
      note: 'Vendor late, or will make the project late',
      href: '/orders',
      bad: true,
    },
    {
      label: 'Jobs waiting on a person',
      value: waiting ? waiting.count : waiting === undefined ? undefined : null,
      note: 'Stuck for more than two days',
      href: '/jobs',
      bad: true,
    },
    {
      label: 'Quotes with no answer',
      value: quotesOld ?? rfqs?.metrics?.overdue ?? (rfqs === undefined ? undefined : null),
      note: win != null ? `Win rate ${Math.round(win <= 1 ? win * 100 : win)}%` : 'Out for over a week',
      href: '/orders?tab=rfqs',
    },
    {
      label: 'Vendor POs not confirmed or late',
      value: c ? (c.no_ack ?? 0) + (c.late ?? 0) : vendors === undefined ? undefined : null,
      note: 'Chase these first',
      href: '/contacts',
      bad: true,
    },
    {
      label: 'Orders under the lowest margin',
      value: underFloor,
      note:
        underFloor === null ? 'Needs costs entered (managers only)' : `Below ${orders?.min_margin_pct ?? 15}%, orders with a cost entered`,
      href: '/orders',
      bad: true,
    },
    {
      label: 'Waiting for the vendor’s final invoice',
      value: awaitingInvoice,
      note: 'Delivered, but we cannot bill the customer until it arrives',
      href: '/boards',
    },
  ];
  return (
    <div className="grid min-w-0 gap-3">
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Late and urgent">
        {tiles.map((t) => (
          <li key={t.label}>
            <Link href={t.href} className="grid gap-1 rounded-lg border bg-card p-4 hover:bg-muted/40">
              <span className="text-xs text-muted-foreground">{t.label}</span>
              <span className={cn('text-3xl font-semibold tabular-nums', t.bad && Number(t.value) > 0 && 'text-bad')}>
                {t.value === undefined ? '…' : (t.value ?? '–')}
              </span>
              <span className="text-xs text-muted-foreground">{t.note}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
