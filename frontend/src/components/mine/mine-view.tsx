'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { statusOf, waitText, type Ball } from '@/components/data/ball';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { authFetch } from '@/lib/api';
import { plain, since } from '@/lib/format';

type Row = Record<string, unknown>;
type Mine = {
  ball: Ball[];
  mentions: Row[];
  notes: Row[];
  rfqs: Row[];
  vendor_jobs: Row[];
  drafts_waiting: number;
};

const str = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
const first = (r: Row, keys: string[]) => keys.map((k) => str(r[k])).find(Boolean) ?? '';

/** Where an item lives, from its kind and reference. */
function hrefFor(type: string, ref: string) {
  switch (type) {
    case 'order':
      return `/orders?order=${encodeURIComponent(ref)}`;
    case 'rfq':
      return `/rfqs?id=${encodeURIComponent(ref)}`;
    case 'vendor_job':
      return `/vendors?job=${encodeURIComponent(ref)}`;
    case 'shipment':
      return `/shipments?id=${encodeURIComponent(ref)}`;
    case 'milestone':
      return `/projects?milestone=${encodeURIComponent(ref)}`;
    case 'board_item':
      return ref ? `/?item=${encodeURIComponent(ref)}` : '/';
    default:
      return '/';
  }
}
const KIND: Record<string, string> = {
  order: 'Order',
  rfq: 'Quote request',
  vendor_job: 'Vendor job',
  shipment: 'Shipment',
  milestone: 'Milestone',
  board_item: 'Board item',
};

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  if (!count) return null;
  return (
    <section className="grid gap-2">
      <h2 className="flex items-baseline gap-2 text-sm font-semibold">
        {title} <span className="text-xs font-normal text-muted-foreground tabular-nums">{count}</span>
      </h2>
      <ul className="divide-y overflow-hidden rounded-2xl border bg-card">{children}</ul>
    </section>
  );
}

function Line({ href, title, meta, tail }: { href: string; title: string; meta?: string; tail?: React.ReactNode }) {
  return (
    <li>
      <Link href={href} className="grid gap-0.5 px-4 py-3 text-[13px] transition-colors hover:bg-muted/50">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{title}</span>
          {tail}
        </span>
        {meta && <span className="line-clamp-2 text-xs text-muted-foreground">{meta}</span>}
      </Link>
    </li>
  );
}

/** Everything that is waiting on me, in one calm list. Sections that are empty are left out. */
export function MineView() {
  const q = useQuery({
    queryKey: ['mine'],
    queryFn: async (): Promise<Mine | null> => {
      const res = await authFetch('/api/v1/workspace/my/items', { cache: 'no-store' });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`Couldn’t load your items (${res.status}).`);
      return (await res.json()) as Mine;
    },
    refetchInterval: 60_000,
    retry: false,
  });

  if (q.isPending) return <RowsSkeleton rows={5} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!q.data) return <EmptyState title="Not available yet">Your own list shows up here once it is switched on.</EmptyState>;
  const m = q.data;
  const total = m.ball.length + m.mentions.length + m.notes.length + m.rfqs.length + m.vendor_jobs.length + (m.drafts_waiting || 0);

  return (
    <div className="mx-auto grid w-full max-w-3xl gap-6">
      <div className="grid gap-0.5">
        <h2 className="text-xl font-semibold tracking-tight sm:text-2xl">{total === 0 ? 'Nothing on your plate' : 'On your plate'}</h2>
        <p className="text-sm text-muted-foreground">
          {total === 0 ? 'No one is waiting on you right now.' : `${total} ${total === 1 ? 'thing is' : 'things are'} waiting on you.`}
        </p>
      </div>

      {m.drafts_waiting > 0 && (
        <Link
          href="/inbox?show=approvals"
          className="flex items-center gap-2 rounded-2xl border bg-card px-4 py-3 text-sm transition-colors hover:bg-muted/50"
        >
          <span className="text-2xl font-semibold tabular-nums">{m.drafts_waiting}</span>
          <span className="text-muted-foreground">{m.drafts_waiting === 1 ? 'draft is' : 'drafts are'} waiting for your OK</span>
        </Link>
      )}

      <Section title="It is waiting on you" count={m.ball.length}>
        {m.ball.map((b) => (
          <Line
            key={`${b.item_type}-${b.item_ref}`}
            href={hrefFor(b.item_type, b.item_ref)}
            title={b.label || `${KIND[b.item_type] ?? b.item_type} ${b.item_ref}`}
            meta={`${waitText(b)}${b.note ? ` · waiting for ${b.note}` : ''}`}
            tail={
              <>
                <ToneBadge tone={statusOf(b).tone}>{statusOf(b).label}</ToneBadge>
                {b.overdue && <ToneBadge tone="bad">Late</ToneBadge>}
              </>
            }
          />
        ))}
      </Section>

      <Section title="Mentioned you" count={m.mentions.length}>
        {m.mentions.map((r, i) => (
          <Line
            key={`m${i}`}
            href={hrefFor(str(r.item_type), str(r.item_ref))}
            title={`${first(r, ['author_name', 'author_email']) || 'Someone'} · ${KIND[str(r.item_type)] ?? 'Item'} ${str(r.item_ref)}`}
            meta={first(r, ['body', 'text'])}
            tail={r.created_at ? <span className="text-xs text-muted-foreground">{since(str(r.created_at))}</span> : undefined}
          />
        ))}
      </Section>

      <Section title="Notes to read" count={m.notes.length}>
        {m.notes.map((r, i) => (
          <Line
            key={`n${i}`}
            href="/"
            title={first(r, ['item_name', 'title', 'item_id']) || 'Note from management'}
            meta={first(r, ['body', 'text'])}
            tail={r.urgent ? <ToneBadge tone="bad">Urgent</ToneBadge> : undefined}
          />
        ))}
      </Section>

      <Section title="Your quote requests" count={m.rfqs.length}>
        {m.rfqs.map((r, i) => {
          const ref = first(r, ['id', 'number']);
          return (
            <Line
              key={`r${i}`}
              href={ref ? `/rfqs?id=${encodeURIComponent(ref)}` : '/rfqs'}
              title={[first(r, ['number']), first(r, ['customer'])].filter(Boolean).join(' · ') || 'Quote request'}
              meta={plain(first(r, ['subject', 'status']))}
            />
          );
        })}
      </Section>

      <Section title="Your vendor orders" count={m.vendor_jobs.length}>
        {m.vendor_jobs.map((r, i) => {
          const ref = first(r, ['id', 'job_id', 'po_number']);
          return (
            <Line
              key={`v${i}`}
              href={ref ? `/vendors?job=${encodeURIComponent(ref)}` : '/vendors'}
              title={[first(r, ['vendor']), first(r, ['po_number', 'project'])].filter(Boolean).join(' · ') || 'Vendor order'}
              meta={plain(first(r, ['state', 'status']))}
            />
          );
        })}
      </Section>
    </div>
  );
}
