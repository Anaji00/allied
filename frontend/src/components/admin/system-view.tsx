'use client';

import { useState } from 'react';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Check, Loader2, RefreshCw, X } from 'lucide-react';
import { CarrierPrices } from './carrier-prices';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { Field, FilterChips, NativeSelect, PanelHeading, SectionTabs } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch, pollMailboxNow, simulateMondayStatus } from '@/lib/api';
import { api, ok } from '@/lib/api/client';
import { dateTime, since, words } from '@/lib/format';
import { useAutomationStatus, useHealth } from '@/lib/queries';
import { cn } from '@/lib/utils';

const TABS = ['health', 'automation', 'monday', 'carriers', 'fedex'] as const;

export function SystemView() {
  const { hasRole } = useAuth();
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('health'));
  return (
    <div className="grid gap-4">
      <SectionTabs
        label="System sections"
        value={tab}
        onChange={(t) => void setTab(t === 'health' ? null : t)}
        tabs={[
          ['health', 'Health'],
          ['automation', 'What runs by itself'],
          ['monday', 'monday.com link'],
          ['carriers', 'Carrier prices'],
          ...(hasRole('MANAGER') ? ([['fedex', 'FedEx']] as const) : []),
        ]}
      />
      {tab === 'carriers' && <CarrierPrices />}
      {tab === 'fedex' && hasRole('MANAGER') && <FedEx />}
      {tab === 'health' && <Health />}
      {tab === 'automation' && <Automation />}
      {tab === 'monday' && <Monday />}
    </div>
  );
}

function Card({
  title,
  description,
  badge,
  children,
  className,
}: {
  title: string;
  description?: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('grid content-start gap-3 rounded-lg border bg-card p-4', className)}>
      <div className="flex items-start gap-2">
        <div className="grid gap-0.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {description && <p className="text-xs text-muted-foreground">{description}</p>}
        </div>
        {badge && <span className="ml-auto">{badge}</span>}
      </div>
      {children}
    </section>
  );
}

/* ----------------------------------------------------------------- fedex */

type FedExStatus = { enabled: boolean; env?: string; ready: boolean; missing?: string[]; detail?: string };

function FedEx() {
  const status = useQuery({
    queryKey: ['fedex', 'status'],
    queryFn: async (): Promise<FedExStatus | null> => {
      const res = await authFetch('/api/v1/fedex/status', { cache: 'no-store' });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`Couldn’t read the FedEx setup (${res.status}).`);
      return res.json();
    },
  });
  const check = useMutation({
    mutationFn: async () => {
      const res = await authFetch('/api/v1/fedex/check', { method: 'POST' });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `The check failed (${res.status}).`);
      return body as { ok: boolean; detail?: string };
    },
    onSuccess: (r) => (r.ok ? toast.success(r.detail || 'Connected to FedEx') : toast.warning('FedEx isn’t connected', { description: r.detail })),
    onError: (e) => toast.error('The check failed', { description: e.message }),
  });

  if (status.isPending) return <RowsSkeleton rows={3} />;
  if (status.isError) return <ErrorState error={status.error} onRetry={() => void status.refetch()} />;
  const s = status.data;
  if (!s)
    return (
      <Card title="FedEx" description="Live shipping prices.">
        <p className="text-sm text-muted-foreground">Not available yet.</p>
      </Card>
    );

  return (
    <Card
      title="FedEx"
      description="Live shipping prices. This only looks up prices; it never books or buys anything."
      badge={<ToneBadge tone={s.ready ? 'ok' : 'neutral'}>{s.ready ? 'Ready' : s.enabled ? 'Not set up' : 'Off'}</ToneBadge>}
      className="max-w-xl"
    >
      <Facts items={[['Mode', s.env === 'production' ? 'Live account' : s.env ? 'Test account (sandbox)' : '–']]} />
      {s.detail && <p className="text-sm text-muted-foreground">{s.detail}</p>}
      {!!s.missing?.length && (
        <div className="text-sm">
          <p className="font-medium">Still needed in the settings file:</p>
          <ul className="mt-1 list-inside list-disc font-mono text-xs text-muted-foreground">
            {s.missing.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      <Button variant="outline" size="sm" className="justify-self-start" disabled={check.isPending} onClick={() => check.mutate()}>
        {check.isPending && <Loader2 className="animate-spin" />}
        Check connection
      </Button>
    </Card>
  );
}

/* ---------------------------------------------------------------- health */

const OK_STATES = ['connected', 'ready', 'healthy', 'ok'];
const stateBadge = (value?: string) =>
  !value ? (
    <ToneBadge>Unknown</ToneBadge>
  ) : OK_STATES.includes(value.toLowerCase()) ? (
    <ToneBadge tone="ok">Working</ToneBadge>
  ) : (
    <ToneBadge tone="bad">{words(value)}</ToneBadge>
  );
const mode = (m?: string) =>
  m === 'simulation' ? 'Practice mode (no live writes)' : m === 'live_odbc' || m === 'live_com' ? 'Live' : m || '–';

function Health() {
  const { data, error, refetch, isFetching, dataUpdatedAt } = useHealth();
  const c: any = (data as any)?.components ?? {};
  if (!data && error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (!data) return <RowsSkeleton rows={5} />;
  const healthy = String((data as any).status).toLowerCase() === 'healthy';

  return (
    <div className="grid gap-4">
      <div className={cn('flex flex-wrap items-center gap-3 rounded-lg p-3 text-sm', healthy ? 'bg-ok-soft' : 'bg-warn-soft')}>
        <span className={cn('font-medium', healthy ? 'text-ok' : 'text-warn')}>
          {healthy ? 'Everything is working' : 'Something needs a look'}
        </span>
        <span className="text-muted-foreground">
          {(data as any).simulation_mode
            ? 'Sage is in practice mode: files are made, but nothing is written into Sage automatically.'
            : 'Sage is connected live.'}
        </span>
        <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          {error ? (
            <span className="text-bad">Last check failed; showing the reading from {since(new Date(dataUpdatedAt).toISOString())}</span>
          ) : (
            `Checked ${since(new Date(dataUpdatedAt).toISOString())}`
          )}
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? <Loader2 className="animate-spin" /> : <RefreshCw />} Check now
          </Button>
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ['Parts in the catalogue', c.in_memory_catalogs?.items_loaded],
          ['Customers', c.in_memory_catalogs?.customers_loaded],
          ['Suppliers', c.in_memory_catalogs?.vendors_loaded],
          ['Other part names', c.in_memory_catalogs?.aliases_loaded],
        ].map(([label, value]) => (
          <div key={label} className="grid gap-0.5 rounded-lg border bg-card p-3">
            <span className="text-xs text-muted-foreground">{label}</span>
            <span className="text-xl font-semibold tabular-nums">{value ?? '–'}</span>
          </div>
        ))}
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card
          title="Reading from Sage"
          description="Parts, customers and order history. Read-only."
          badge={stateBadge(c.sage_odbc_reader?.status)}
        >
          <Facts
            items={[
              ['Mode', mode(c.sage_odbc_reader?.mode)],
              ['Data source', c.sage_odbc_reader?.dsn ?? '–'],
            ]}
          />
        </Card>
        <Card
          title="Writing to Sage"
          description="Sales orders and the matching supplier POs."
          badge={stateBadge(c.sage_com_sdk_writer?.status)}
        >
          <Facts
            items={[
              ['Mode', mode(c.sage_com_sdk_writer?.mode)],
              ['Orders written since start-up', String(c.sage_com_sdk_writer?.transactions_executed ?? '–')],
            ]}
          />
        </Card>
        <Card title="Order safeguards" description="Rules every order is checked against.">
          <Facts
            items={[
              [
                'Lowest allowed margin',
                c.compliance_rules?.min_gross_margin_pct != null ? `${c.compliance_rules.min_gross_margin_pct}%` : '–',
              ],
              ['Supplier hidden on packing slips', c.compliance_rules?.blind_shipping_default ? 'Yes, by default' : 'No'],
            ]}
          />
        </Card>
        <Card title="Service" description="The engine behind every screen.">
          <Facts
            items={[
              ['Name', (data as any).service ?? '–'],
              ['Environment', (data as any).environment ?? '–'],
              ['Reported', dateTime((data as any).timestamp)],
            ]}
          />
        </Card>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ automation */

type Link = { key: string; name: string; live: boolean; detail: string; needs: string | null; saves_minutes: number };
type Worker = {
  key: string;
  name: string;
  running: boolean;
  last_at: string | null;
  error: string | null;
  detail?: string;
  steps: { key: string; name: string; at: string | null; error: string | null }[];
};
type Status = {
  links: Link[];
  links_live: number;
  links_total: number;
  baseline_minutes_per_order: number;
  hand_minutes_remaining: number;
  busy_work_removed_pct: number;
  review_minutes: number;
  mailbox: { enabled: boolean; detail: string };
  workers?: Worker[];
};
type Mail = {
  message_id: string;
  category: string;
  reference?: string;
  to: string[];
  subject: string;
  body: string;
  created_at: string;
  status: string;
  detail?: string;
  redirected?: boolean;
};

const MAIL_STATUS: Record<string, { label: string; tone: Tone }> = {
  SENT: { label: 'Sent', tone: 'ok' },
  DRAFTED: { label: 'Draft only', tone: 'neutral' },
  FAILED: { label: 'Failed', tone: 'bad' },
};

const mcol = columnHelper<Mail>();
const mailColumns = mcol.columns([
  mcol.accessor('created_at', { header: 'When', cell: (c) => <span className="whitespace-nowrap">{dateTime(c.getValue())}</span> }),
  mcol.accessor('category', { header: 'Kind', cell: (c) => words(c.getValue()) }),
  mcol.accessor('subject', { header: 'Subject', meta: { className: 'max-w-80 truncate' } }),
  mcol.accessor('to', { header: 'To', meta: { className: 'max-w-56 truncate' }, cell: (c) => c.getValue()?.join(', ') || '–' }),
  mcol.accessor('status', {
    header: 'State',
    cell: (c) => {
      const s = MAIL_STATUS[c.getValue()] ?? { label: words(c.getValue()), tone: 'neutral' as Tone };
      return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
    },
  }),
]);

function Automation() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const { data, error, refetch } = useAutomationStatus() as { data?: Status; error: Error | null; refetch: () => unknown };
  const outbox = useQuery({
    queryKey: ['automation', 'outbox'],
    queryFn: async () =>
      ((await ok(api.GET('/api/v1/automation/outbox', { params: { query: { limit: 50 } } }))) as { messages: Mail[] }).messages,
    refetchInterval: 60_000,
  });
  const [mail, setMail] = useState<Mail | null>(null);
  const poll = useMutation({
    mutationFn: async () => (await pollMailboxNow()) as { ok: boolean; detail: string; ingested: number; orders_created: number },
    onSuccess: (r) => {
      if (r.ok) toast.success(`Mailbox checked: ${r.ingested} message(s), ${r.orders_created} order(s)`);
      else toast.warning('The mailbox wasn’t checked', { description: r.detail });
      void client.invalidateQueries({ queryKey: ['automation'] });
    },
    onError: (e) => toast.error('The mailbox wasn’t checked', { description: e.message }),
  });

  if (!data && error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (!data) return <RowsSkeleton rows={6} />;

  return (
    <div className="grid gap-4">
      <div className="grid gap-2 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-2xl font-semibold tabular-nums">{data.busy_work_removed_pct}%</span>
          <span className="text-sm text-muted-foreground">
            of the busy work on an order is automatic. {data.hand_minutes_remaining} of {data.baseline_minutes_per_order} minutes are still
            by hand, including the {data.review_minutes}-minute review that always stays with a person.
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label={`${data.busy_work_removed_pct}% automatic`}>
          <div className="h-full rounded-full bg-ok" style={{ width: `${data.busy_work_removed_pct}%` }} />
        </div>
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card title="Connections" description={`${data.links_live} of ${data.links_total} live`}>
          <ul className="grid gap-2">
            {data.links.map((l) => (
              <li key={l.key} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-[13px]">
                {l.live ? (
                  <Check className="mt-0.5 size-4 text-ok" aria-label="Live" />
                ) : (
                  <X className="mt-0.5 size-4 text-muted-foreground" aria-label="Not live" />
                )}
                <span className="font-medium">{l.name}</span>
                <span />
                <span className="text-muted-foreground">
                  {l.detail}
                  {l.needs && <span className="block text-warn">Needs: {l.needs}</span>}
                </span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Background workers" description="The loops that read boards, check schedules and retry stuck events.">
          <ul className="grid gap-3">
            {(data.workers ?? []).map((w) => (
              <li key={w.key} className="grid gap-1 text-[13px]">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{w.name}</span>
                  <ToneBadge tone={w.error ? 'bad' : w.running ? 'ok' : 'neutral'} className="ml-auto">
                    {w.error ? 'Last run failed' : w.running ? 'Running' : 'Stopped'}
                  </ToneBadge>
                </div>
                <span className="text-xs text-muted-foreground">
                  {w.detail ?? (w.last_at ? `Last worked ${since(w.last_at)}` : 'Hasn’t run yet')}
                </span>
                {w.error && (
                  <span className="line-clamp-3 text-xs break-words text-bad" title={w.error}>
                    {w.error}
                  </span>
                )}
                {w.steps.length > 0 && (
                  <details className="text-xs">
                    <summary className="cursor-pointer text-muted-foreground">Each step</summary>
                    <ul className="mt-1 grid gap-0.5">
                      {w.steps.map((s) => (
                        <li key={s.key} className="flex gap-2">
                          <span className="flex-1">{s.name}</span>
                          <span className={s.error ? 'text-bad' : 'text-muted-foreground'}>
                            {s.error ? 'failed' : s.at ? since(s.at) : 'not yet'}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <PanelHeading>Mail the system has written</PanelHeading>
          {hasRole('OPERATOR') && (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={poll.isPending || !data.mailbox.enabled}
              title={data.mailbox.enabled ? undefined : data.mailbox.detail}
              onClick={() => poll.mutate()}
            >
              {poll.isPending && <Loader2 className="animate-spin" />}
              Check the order mailbox now
            </Button>
          )}
        </div>
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]">
          {outbox.isPending ? (
            <RowsSkeleton rows={4} />
          ) : outbox.error ? (
            <ErrorState error={outbox.error} onRetry={() => void outbox.refetch()} />
          ) : (
            <DataTable
              label="Outbox"
              columns={mailColumns}
              data={outbox.data ?? []}
              getRowId={(m) => m.message_id}
              selectedId={mail?.message_id}
              onSelect={(m) => setMail(mail?.message_id === m.message_id ? null : m)}
              minWidth={720}
              empty={
                <EmptyState title="Nothing written yet">Disputes, purchase orders and requests the system writes appear here.</EmptyState>
              }
            />
          )}
          <DetailPanel
            open={Boolean(mail)}
            onClose={() => setMail(null)}
            title={mail?.subject ?? ''}
            description={mail ? dateTime(mail.created_at) : undefined}
          >
            {mail && (
              <div className="grid gap-3 text-[13px]">
                <Facts
                  items={[
                    ['To', mail.to?.join(', ') || '–'],
                    ['State', MAIL_STATUS[mail.status]?.label ?? mail.status],
                    ['About', mail.reference || '–'],
                    ...(mail.detail ? ([['Why', mail.detail]] as [string, string][]) : []),
                  ]}
                />
                {mail.redirected && <p className="text-xs text-warn">Test mode: this went to the test address, not the person named.</p>}
                <pre className="rounded-md bg-muted/50 p-3 font-sans whitespace-pre-wrap">{mail.body}</pre>
              </div>
            )}
          </DetailPanel>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- monday */

type Event = {
  id: number;
  status: string;
  action: string | null;
  label: string | null;
  item_id: string | null;
  item_name: string | null;
  attempts: number;
  last_error: string | null;
  received_at: string | null;
  processed_at: string | null;
  result?: unknown;
};
type Setup = {
  checks: { key: string; label: string; ok: boolean; hint: string }[];
  routing: { status_column: string; create_order_labels: string[]; release_po_labels: string[]; dispatch_labels: string[] };
  api_version: string;
  worker_running: boolean;
};

const EVENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  FAILED: { label: 'Needs a person', tone: 'bad' },
  DEAD: { label: 'Gave up', tone: 'bad' },
  RETRY: { label: 'Retrying', tone: 'warn' },
  PENDING: { label: 'Waiting', tone: 'neutral' },
  PROCESSING: { label: 'Running', tone: 'info' },
  DONE: { label: 'Done', tone: 'ok' },
  IGNORED: { label: 'Ignored', tone: 'neutral' },
};
const FILTERS = ['', 'FAILED', 'DEAD', 'RETRY', 'DONE', 'IGNORED'] as const;

const ecol = columnHelper<Event>();
const eventColumns = ecol.columns([
  ecol.accessor('id', { header: '#', cell: (c) => <span className="tabular-nums">{c.getValue()}</span> }),
  ecol.accessor('received_at', { header: 'Received', cell: (c) => <span className="whitespace-nowrap">{dateTime(c.getValue())}</span> }),
  ecol.accessor('item_name', {
    header: 'Item',
    meta: { className: 'max-w-72 truncate' },
    cell: (c) => c.getValue() || (c.row.original.item_id ? `Item ${c.row.original.item_id}` : '–'),
  }),
  ecol.accessor('label', { header: 'Change', cell: (c) => c.getValue() || words(c.row.original.action) || '–' }),
  ecol.accessor('status', {
    header: 'Outcome',
    cell: (c) => {
      const s = EVENT_STATUS[c.getValue()] ?? { label: words(c.getValue()), tone: 'neutral' as Tone };
      return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
    },
  }),
  ecol.accessor('attempts', { header: 'Tries', meta: { align: 'right' } }),
]);

function Monday() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [status, setStatus] = useState<(typeof FILTERS)[number]>('');
  const [open, setOpen] = useState<number | null>(null);
  const [testing, setTesting] = useState(false);
  const events = useQuery({
    queryKey: ['monday', 'events', status],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/webhooks/monday/events', { params: { query: { limit: 100, status: status || undefined } } }))) as {
        stats: Record<string, number>;
        events: Event[];
      },
    refetchInterval: 30_000,
  });
  const setup = useQuery({
    queryKey: ['monday', 'setup'],
    queryFn: async () => (await ok(api.GET('/api/v1/webhooks/monday/setup'))) as Setup,
  });
  const replay = useMutation({
    mutationFn: async (id: number) =>
      ok(api.POST('/api/v1/webhooks/monday/events/{event_id}/replay', { params: { path: { event_id: id } } })),
    onSuccess: (_r, id) => {
      toast.success(`Event #${id} is running again`);
      void client.invalidateQueries({ queryKey: ['monday'] });
      void client.invalidateQueries({ queryKey: ['inbox'] });
    },
    onError: (e) => toast.error('The event didn’t replay', { description: e.message }),
  });

  const rows = events.data?.events ?? [];
  const stats = events.data?.stats ?? {};
  const selected = rows.find((e) => e.id === open);
  const live = setup.data?.checks.find((c) => c.key === 'live')?.ok;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={status}
          onChange={(v) => {
            setStatus(v);
            setOpen(null);
          }}
          options={FILTERS.map((f) => ({
            value: f,
            label: f ? EVENT_STATUS[f].label : 'All',
            count: f ? stats[f] : undefined,
            tone: f === 'FAILED' || f === 'DEAD' ? ('bad' as const) : f === 'RETRY' ? ('warn' as const) : undefined,
          }))}
        />
        {hasRole('OPERATOR') && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => setTesting(true)}>
            Send a test event…
          </Button>
        )}
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="grid min-w-0 gap-4">
          {events.isPending ? (
            <RowsSkeleton rows={6} />
          ) : events.error ? (
            <ErrorState error={events.error} onRetry={() => void events.refetch()} />
          ) : (
            <DataTable
              label="monday events"
              columns={eventColumns}
              data={rows}
              getRowId={(e) => String(e.id)}
              selectedId={open != null ? String(open) : null}
              onSelect={(e) => setOpen(open === e.id ? null : e.id)}
              minWidth={720}
              empty={
                <EmptyState title="No events here">Board changes the app picks up are listed here, with what each one did.</EmptyState>
              }
            />
          )}
          <DetailPanel
            open={Boolean(selected)}
            onClose={() => setOpen(null)}
            title={selected ? `Event #${selected.id}` : ''}
            description={selected?.item_name ?? undefined}
          >
            {selected && (
              <div className="grid gap-3 text-[13px]">
                <Facts
                  items={[
                    ['Outcome', EVENT_STATUS[selected.status]?.label ?? selected.status],
                    ['Change', selected.label || words(selected.action) || '–'],
                    ['Item', selected.item_id ?? '–'],
                    ['Tries', String(selected.attempts)],
                    ['Received', dateTime(selected.received_at)],
                    ['Finished', dateTime(selected.processed_at)],
                  ]}
                />
                {selected.last_error && <p className="rounded-md bg-bad-soft p-2 text-bad">{selected.last_error}</p>}
                {['FAILED', 'DEAD', 'IGNORED'].includes(selected.status) && hasRole('OPERATOR') && (
                  <Button size="sm" className="justify-self-start" disabled={replay.isPending} onClick={() => replay.mutate(selected.id)}>
                    {replay.isPending && <Loader2 className="animate-spin" />}
                    Replay after fixing the item
                  </Button>
                )}
              </div>
            )}
          </DetailPanel>
        </div>

        <Card
          title="Setup"
          description="What the link needs, and what it has."
          badge={setup.data && <ToneBadge tone={live ? 'warn' : 'neutral'}>{live ? 'Board writes on' : 'Practice mode'}</ToneBadge>}
        >
          {setup.isPending ? (
            <RowsSkeleton rows={4} />
          ) : setup.error ? (
            <ErrorState error={setup.error} onRetry={() => void setup.refetch()} />
          ) : (
            <>
              <ul className="grid gap-2">
                {setup.data!.checks.map((c) => (
                  <li key={c.key} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-[13px]">
                    {c.ok ? (
                      <Check className="mt-0.5 size-4 text-ok" aria-label="Done" />
                    ) : (
                      <X className="mt-0.5 size-4 text-warn" aria-label="Not done" />
                    )}
                    <span>{c.label}</span>
                    {!c.ok && (
                      <>
                        <span />
                        <span className="text-xs text-muted-foreground">{c.hint}</span>
                      </>
                    )}
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                Retrying failed board changes is {setup.data!.worker_running ? 'running' : 'stopped'}.
              </p>
            </>
          )}
        </Card>
      </div>

      {hasRole('OPERATOR') && <TestEvent open={testing} onOpenChange={setTesting} setup={setup.data} live={Boolean(live)} />}
    </div>
  );
}

function TestEvent({
  open,
  onOpenChange,
  setup,
  live,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  setup?: Setup;
  live: boolean;
}) {
  const client = useQueryClient();
  const labels = setup ? [...setup.routing.create_order_labels, ...setup.routing.release_po_labels, ...setup.routing.dispatch_labels] : [];
  const [item, setItem] = useState('');
  const [name, setName] = useState('');
  const [label, setLabel] = useState('');
  const chosen = labels.includes(label) ? label : (labels[0] ?? '');
  const send = useMutation({
    mutationFn: () => simulateMondayStatus(item, chosen, name || undefined),
    onSuccess: () => {
      toast.success('Test event sent; it appears in the list in a moment');
      onOpenChange(false);
      void client.invalidateQueries({ queryKey: ['monday'] });
    },
    onError: (e) => toast.error('The test event wasn’t sent', { description: e.message }),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send a test event</DialogTitle>
          <DialogDescription>Runs through the same path as a real board change.</DialogDescription>
        </DialogHeader>
        {live && (
          <p role="alert" className="rounded-md bg-bad-soft p-2.5 text-sm text-bad">
            Board writes are on: this acts on the real item, and can create orders or supplier POs.
          </p>
        )}
        <form
          id="test-event"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (item && chosen) send.mutate();
          }}
        >
          <Field label="Item number">
            <Input inputMode="numeric" value={item} onChange={(e) => setItem(e.target.value.replace(/\D/g, ''))} required />
          </Field>
          <Field label="Item name" hint="Customer - PO - Item, the way the board names it.">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Status it changes to">
            <NativeSelect value={chosen} onChange={(e) => setLabel(e.target.value)}>
              {labels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </NativeSelect>
          </Field>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="test-event" variant={live ? 'destructive' : 'default'} disabled={!item || !chosen || send.isPending}>
            {send.isPending && <Loader2 className="animate-spin" />}
            {live ? 'Send it to the real item' : 'Send test event'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
