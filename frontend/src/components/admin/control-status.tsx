'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { localIso } from '@/lib/format';
import { cn } from '@/lib/utils';

type Ship = {
  status?: string;
  eta_current?: string | null;
  quiet_days?: number | null;
  quiet_flag?: string | null;
  forwarder_label?: string | null;
};
type Counts = {
  email_drafts_waiting?: number;
  drafts_waiting?: number;
  need_action?: number;
};
type Backups = { summary?: { state?: string; text?: string } | null };
type Attention = {
  quiet_shipments?: { count: number };
  payments_due?: { count: number } | null;
  gates_waiting?: { count: number };
  drawings_unverified?: { count: number };
  drafts_waiting?: number;
  backups?: { state?: string; text?: string };
};

async function getJson<T>(path: string): Promise<T | null> {
  const res = await authFetch(path, { cache: 'no-store' }).catch(() => null);
  return res?.ok ? ((await res.json()) as T) : null;
}

const day = (d: Date) => localIso(d);

type Tile = {
  title: string;
  value: number | string | null;
  line: string;
  href: string;
  tone?: 'bad' | 'warn';
};

/**
 * The Control room at a glance: what needs attention now (each box opens the screen that fixes it) and a live picture of freight.
 * Boxes whose numbers the server cannot give yet are left out rather than shown as zero.
 */
export function ControlStatus() {
  const { hasRole } = useAuth();
  const ships = useQuery({
    queryKey: ['control', 'shipments'],
    queryFn: async () => (await getJson<{ shipments: Ship[] }>('/api/v1/shipments'))?.shipments ?? null,
    refetchInterval: 120_000,
    retry: false,
  }).data;
  const counts = useQuery({
    queryKey: ['me', 'counts'],
    queryFn: () => getJson<Counts>('/api/v1/me/counts'),
    refetchInterval: 60_000,
    retry: false,
  }).data;
  const due = useQuery({
    queryKey: ['control', 'payments-due'],
    queryFn: async () => {
      const out = await getJson<unknown>('/api/v1/payments/due');
      return Array.isArray((out as { due?: unknown[] } | null)?.due)
        ? (out as { due: unknown[] }).due.length
        : Array.isArray(out)
          ? out.length
          : Array.isArray((out as { items?: unknown[] } | null)?.items)
            ? (out as { items: unknown[] }).items.length
            : null;
    },
    enabled: hasRole('MANAGER'),
    retry: false,
  }).data;
  const backups = useQuery({
    queryKey: ['control', 'backups'],
    queryFn: () => getJson<Backups>('/api/v1/health/backups'),
    enabled: hasRole('MANAGER'),
    retry: false,
  }).data;

  // One call gives every count. The separate calls above are the fallback for a server that does not have it yet.
  const one = useQuery({
    queryKey: ['control', 'attention'],
    queryFn: () => getJson<Attention>('/api/v1/control/attention'),
    refetchInterval: 90_000,
    retry: false,
  }).data;

  const today = new Date();
  const weekEnd = day(new Date(today.getTime() + 7 * 86_400_000));
  const open = (ships ?? []).filter((s) => !/deliver|finish|closed|arrived/i.test(s.status ?? ''));
  const quiet = open.filter((s) => s.quiet_flag || (s.quiet_days ?? 0) >= 7);
  const arriving = open.filter((s) => s.eta_current && s.eta_current >= day(today) && s.eta_current <= weekEnd);
  const noDate = open.filter((s) => !s.eta_current);

  const fromOne: Tile[] = one
    ? [
        {
          title: 'Shipments with no news',
          value: one.quiet_shipments?.count ?? 0,
          line: 'No update for a week or more. Ask the forwarder.',
          href: '/logistics',
          tone: one.quiet_shipments?.count ? ('warn' as const) : undefined,
        },
        ...(one.payments_due
          ? [
              {
                title: 'Vendor payments due',
                value: one.payments_due.count,
                line: 'Payment parts that are due and not yet paid.',
                href: '/boards?board=jobs',
                tone: one.payments_due.count ? ('warn' as const) : undefined,
              },
            ]
          : []),
        {
          title: 'Engineer checks waiting',
          value: one.gates_waiting?.count ?? 0,
          line: 'Vendor drawings or photos waiting for an engineer.',
          href: '/boards?board=jobs',
          tone: one.gates_waiting?.count ? ('warn' as const) : undefined,
        },
        {
          title: 'Drawings not yet verified',
          value: one.drawings_unverified?.count ?? 0,
          line: 'Not safe to send to a vendor until verified.',
          href: '/boards?board=jobs',
        },
        {
          title: 'Emails waiting for OK',
          value: one.drafts_waiting ?? 0,
          line: 'Drafts a person still has to approve.',
          href: '/secretary/mail?filter=drafts',
        },
        ...(one.backups?.state && one.backups.state !== 'ok'
          ? [
              {
                title: 'Backups',
                value: one.backups.state === 'failed' ? 'Failed' : 'No backup yet',
                line: one.backups.text ?? 'Look at the backups on the System page.',
                href: '/health',
                tone: one.backups.state === 'failed' ? ('bad' as const) : undefined,
              },
            ]
          : []),
      ]
    : [];
  // Every box always shows, so two people see the same overview. A number the server cannot give yet says so in grey.
  const placeholders: Tile[] = [
    {
      title: 'Shipments with no news',
      value: '–',
      line: 'Not available yet.',
      href: '/logistics',
    },
    {
      title: 'Engineer checks waiting',
      value: '–',
      line: 'Not available yet.',
      href: '/boards?board=jobs',
    },
    {
      title: 'Emails waiting for OK',
      value: '–',
      line: 'Not available yet.',
      href: '/secretary/mail?filter=drafts',
    },
  ];
  const attention: Tile[] = one
    ? fromOne
    : [
        ...(ships
          ? [
              {
                title: 'Shipments with no news',
                value: quiet.length,
                line: 'No update for a week or more. Ask the forwarder.',
                href: '/logistics',
                tone: quiet.length ? ('warn' as const) : undefined,
              },
            ]
          : []),
        ...(due !== undefined && due !== null
          ? [
              {
                title: 'Vendor payments due',
                value: due,
                line: 'Steps that are due and not yet paid.',
                href: '/boards?board=jobs',
                tone: due ? ('warn' as const) : undefined,
              },
            ]
          : []),
        ...(counts
          ? [
              {
                title: 'Emails waiting for OK',
                value: counts.email_drafts_waiting ?? counts.drafts_waiting ?? 0,
                line: 'Drafts a person still has to approve.',
                href: '/secretary/mail?filter=drafts',
              },
            ]
          : []),
        ...(backups?.summary?.state && backups.summary.state !== 'ok'
          ? [
              {
                title: 'Backups',
                value: backups.summary.state === 'failed' ? 'Failed' : 'No backup yet',
                line: backups.summary.text ?? 'Look at the backups on the System page.',
                href: '/health',
                tone: backups.summary.state === 'failed' ? ('bad' as const) : undefined,
              },
            ]
          : []),
      ];

  return (
    <div className="grid gap-5">
      {
        <section className="grid gap-2" aria-label="Needs attention now">
          <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Needs attention now</h2>
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {(attention.length ? attention : placeholders).map((t) => (
              <li key={t.title}>
                <Link href={t.href} className="grid h-full gap-1 rounded-lg border bg-card p-3 hover:bg-muted/40">
                  <span className="text-xs text-muted-foreground">{t.title}</span>
                  <span
                    className={cn('text-2xl font-semibold tabular-nums', t.tone === 'bad' && 'text-bad', t.tone === 'warn' && 'text-warn')}
                  >
                    {t.value}
                  </span>
                  <span className="text-xs text-muted-foreground">{t.line}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      }
      {ships && (
        <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Freight and shipping">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">Freight and shipping</h2>
            <Link href="/logistics" className="ml-auto text-xs underline underline-offset-2">
              Open the shipments
            </Link>
          </div>
          <p className="text-[13px]">
            {open.length} on the way · {arriving.length} arriving in the next 7 days · {quiet.length} quiet
            {noDate.length ? ` · ${noDate.length} with no arrival date` : ''}
          </p>
          {quiet.length > 0 && (
            <ul className="grid gap-0.5 text-xs text-muted-foreground">
              {quiet.slice(0, 4).map((s, i) => (
                <li key={i}>
                  {s.quiet_flag ?? `Quiet ${s.quiet_days} days: ask the forwarder`}
                  {s.forwarder_label ? ` (${s.forwarder_label})` : ''}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
