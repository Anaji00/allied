'use client';

import { useState } from 'react';
import { parseAsInteger, parseAsStringLiteral, useQueryState } from 'nuqs';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus } from 'lucide-react';
import { DataTable, columnHelper, recordKey } from '@/components/data/data-table';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { Field, FilterChips, NativeSelect, PanelHeading } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { dateTime, money, person, words } from '@/lib/format';
import { addRfq, fetchRfqs, updateRfq } from '@/lib/opsApi';
import { cn } from '@/lib/utils';

type Rfq = {
  id: number;
  number: string;
  customer?: string | null;
  contact_email?: string | null;
  subject?: string | null;
  summary?: string | null;
  items?: { quantity?: number | string; unit?: string; description?: string }[];
  complexity: string;
  status: string;
  state: string;
  reasons?: string[];
  owner_email?: string | null;
  vendor?: string | null;
  received_at?: string | null;
  acknowledged_at?: string | null;
  effective_due_at?: string | null;
  hours_left?: number | null;
  age_business_days?: number;
  paused_hours?: number | null;
  quote_number?: string | null;
  quote_amount?: number | null;
  close_reason?: string | null;
  history?: { at: string; change: string; by: string }[];
};
type Page = {
  rfqs: Rfq[];
  metrics?: {
    open?: number;
    overdue?: number;
    turnaround_median_days?: number | null;
    on_time_pct?: number | null;
    win_rate?: number | null;
    win_rate_by_speed?: Record<string, number | null>;
    open_by_age?: Record<string, number>;
  };
  targets?: { ack_hours: number; simple_days: number; standard_days: number; complex_days: number };
};

const STATUS_LABEL: Record<string, string> = {
  NEW: 'New, not acknowledged',
  ACKNOWLEDGED: 'Acknowledged',
  CLARIFYING: 'Waiting on customer',
  WAITING_VENDOR: 'Waiting on vendor price',
  PRICING: 'Pricing',
  QUOTE_SENT: 'Quote sent',
  WON: 'Won',
  LOST: 'Lost',
  NO_BID: 'Not quoted',
};
export const STATE: Record<string, { label: string; tone: Tone }> = {
  overdue: { label: 'Overdue', tone: 'bad' },
  ack_overdue: { label: 'Not acknowledged', tone: 'bad' },
  due_soon: { label: 'Due soon', tone: 'warn' },
  chase_vendor: { label: 'Chase vendor', tone: 'warn' },
  paused: { label: 'Clock paused', tone: 'info' },
  open: { label: 'On time', tone: 'ok' },
  quoted: { label: 'Quoted', tone: 'info' },
  closed: { label: 'Closed', tone: 'neutral' },
};
const NEXT = ['ACKNOWLEDGED', 'CLARIFYING', 'WAITING_VENDOR', 'PRICING', 'QUOTE_SENT', 'WON', 'LOST', 'NO_BID'];
const CLOSED = ['WON', 'LOST', 'NO_BID'];
const pct = (v?: number | null) => (v == null ? '–' : `${Math.round(v * 100)}%`);
const hoursLeft = (r: Rfq) =>
  r.hours_left == null ? '' : r.hours_left <= 0 ? `${Math.abs(r.hours_left).toFixed(0)} working h late` : `${r.hours_left.toFixed(0)} working h left`;

const SHOW = ['open', 'mine', 'all'] as const;
const col = columnHelper<Rfq>();

export function RfqsView() {
  const { hasRole, employeeEmail } = useAuth();
  const canEdit = hasRole('OPERATOR');
  const wide = useMediaQuery('(min-width: 1280px)');
  const [show, setShow] = useQueryState('show', parseAsStringLiteral(SHOW).withDefault('open'));
  // `id` is the link alerts and autopilot already use.
  const [id, setId] = useQueryState('id', parseAsInteger);
  const [adding, setAdding] = useState(false);
  const { data, isPending, isFetching, error, refetch } = useQuery({
    queryKey: ['rfqs', show],
    queryFn: async () => (await fetchRfqs(show !== 'all', show === 'mine')) as Page,
    placeholderData: keepPreviousData,
  });

  const rows = data?.rfqs ?? [];
  const index = rows.findIndex((r) => r.id === id);
  const selected = rows[index];
  const open = (r?: Rfq) => void setId(r ? r.id : null);
  useListNav(rows, index, open);
  const m = data?.metrics;
  const t = data?.targets;

  const columns = col.columns([
    col.accessor('number', { header: 'RFQ', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
    col.accessor('customer', { header: 'Customer', meta: { className: 'max-w-48 truncate' }, cell: (c) => c.getValue() || 'Unknown' }),
    col.accessor('subject', { header: 'What to quote', meta: { className: 'max-w-72 truncate' }, cell: (c) => c.getValue() || '–' }),
    col.accessor('status', { header: 'Status', cell: (c) => STATUS_LABEL[c.getValue()] ?? words(c.getValue()) }),
    col.accessor('effective_due_at', {
      header: 'Quote due',
      cell: (c) => (
        <span className="grid">
          <span className="whitespace-nowrap">{dateTime(c.getValue())}</span>
          {hoursLeft(c.row.original) && <span className="text-[11px] text-muted-foreground">{hoursLeft(c.row.original)}</span>}
        </span>
      ),
    }),
    col.accessor('state', {
      header: 'State',
      cell: (c) => {
        const s = STATE[c.getValue()] ?? { label: c.getValue(), tone: 'neutral' as Tone };
        return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
      },
    }),
    col.accessor('owner_email', { header: 'Owner', cell: (c) => person(c.getValue(), employeeEmail) || '–' }),
  ]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={show}
          onChange={(v) => {
            void setShow(v === 'open' ? null : v);
            open(undefined);
          }}
          options={[
            { value: 'open', label: 'Open', count: m?.open },
            { value: 'mine', label: 'Mine' },
            { value: 'all', label: 'All, incl. closed' },
          ]}
        />
        {canEdit && (
          <Button size="sm" className="ml-auto" onClick={() => setAdding(true)}>
            <Plus /> Log an RFQ
          </Button>
        )}
      </div>
      {m && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {[
            ['Overdue', String(m.overdue ?? 0), Boolean(m.overdue)],
            ['Median time to quote', m.turnaround_median_days != null ? `${m.turnaround_median_days} d` : '–', false],
            ['Quoted on time', pct(m.on_time_pct), false],
            ['Win rate', pct(m.win_rate), false],
            [
              'Open by age',
              ['0-1 days', '2-3 days', '4-5 days', 'over 5 days'].map((b) => m.open_by_age?.[b] ?? 0).join(' / '),
              false,
            ],
          ].map(([label, value, bad]) => (
            <div key={String(label)} className="grid gap-0.5 rounded-lg border bg-card px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className={cn('text-base font-semibold tabular-nums', bad && 'text-bad')}>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {t && (
        <p className="text-xs text-muted-foreground">
          Acknowledge within {t.ack_hours} working hours. Quote within {t.simple_days} day for a repeat part, {t.standard_days} days standard, or{' '}
          {t.complex_days} days for a new design or vendor pricing.
          {m?.win_rate_by_speed && ` Win rate when quoted within 2 days: ${pct(m.win_rate_by_speed['within 2 days'])}; slower: ${pct(m.win_rate_by_speed.slower)}.`}
          {' '}Open by age counts business days: 0-1 / 2-3 / 4-5 / over 5.
        </p>
      )}

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className={cn('grid min-w-0 gap-2 transition-opacity', isFetching && !isPending && 'opacity-70')}>
          {isPending ? (
            <RowsSkeleton rows={8} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Requests for quote"
              columns={columns}
              data={rows}
              getRowId={(r) => String(r.id)}
              recordName={(r) => recordKey('rfq', r.id)}
              selectedId={id != null ? String(id) : null}
              onSelect={open}
              minWidth={900}
              empty={<EmptyState title="No RFQs here">Quote requests that arrive by email are logged automatically. You can also log one by hand.</EmptyState>}
            />
          )}
          {rows.length > 0 && (
            <p className="hidden text-xs text-muted-foreground md:block">
              <Kbd>J</Kbd>
              <Kbd>K</Kbd> move · <Kbd>Esc</Kbd> close
            </p>
          )}
          {id != null && data && !selected && (
            <p role="status" className="text-sm text-warn">
              That RFQ isn’t in this list{show === 'all' ? '' : '; it may be closed (choose All)'}.
            </p>
          )}
        </div>
        <DetailPanel open={Boolean(selected)} onClose={() => open(undefined)} recordName={selected ? recordKey('rfq', selected.id) : undefined} title={selected ? <span className="font-mono">{selected.number}</span> : ''} description={selected?.customer ?? undefined}>
          {selected && <RfqDetail key={`${selected.id}-${selected.history?.length}`} rfq={selected} canEdit={canEdit} />}
        </DetailPanel>
      </div>
      {canEdit && (
        <AddRfq
          open={adding}
          onOpenChange={setAdding}
          onAdded={(rid) => {
            void setShow(null);
            void setId(rid);
          }}
        />
      )}
    </div>
  );
}

function RfqDetail({ rfq: r, canEdit }: { rfq: Rfq; canEdit: boolean }) {
  const client = useQueryClient();
  const { employeeEmail } = useAuth();
  const blank = { status: '', reason: '', owner_email: '', vendor: '', quote_number: '', quote_amount: '', note: '' };
  const [form, setForm] = useState(blank);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const changes = Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim()));
  const needsReason = ['LOST', 'NO_BID'].includes(form.status);
  const save = useMutation({
    mutationFn: () => updateRfq(r.id, changes),
    onSuccess: () => {
      toast.success('Saved');
      setForm(blank);
      void client.invalidateQueries({ queryKey: ['rfqs'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  const st = STATE[r.state] ?? { label: r.state, tone: 'neutral' as Tone };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-1.5">
        <ToneBadge tone={st.tone}>{st.label}</ToneBadge>
        {(r.reasons ?? []).map((x) => (
          <ToneBadge key={x} tone={st.tone === 'ok' ? 'neutral' : st.tone}>
            {x}
          </ToneBadge>
        ))}
      </div>
      {r.subject && <p className="text-sm font-medium">{r.subject}</p>}
      {r.summary && <p className="text-[13px] whitespace-pre-wrap text-muted-foreground">{r.summary}</p>}
      {(r.items?.length ?? 0) > 0 && (
        <ul className="grid list-disc gap-0.5 pl-5 text-[13px]">
          {r.items!.map((i, n) => (
            <li key={n}>
              {i.quantity} {i.unit} {i.description}
            </li>
          ))}
        </ul>
      )}
      <Facts
        items={[
          ['Status', STATUS_LABEL[r.status] ?? words(r.status)],
          ['Contact', r.contact_email || '–'],
          ['Owner', person(r.owner_email, employeeEmail) || '–'],
          ['Kind', words(r.complexity)],
          ['Received', dateTime(r.received_at)],
          ['Acknowledged', r.acknowledged_at ? dateTime(r.acknowledged_at) : 'Not yet'],
          ['Quote due', `${dateTime(r.effective_due_at)}${hoursLeft(r) ? ` (${hoursLeft(r)})` : ''}`],
          ['Vendor asked', r.vendor || '–'],
          ['Quote', [r.quote_number, r.quote_amount != null ? money(r.quote_amount) : null].filter(Boolean).join(' · ') || '–'],
          ['Age', `${r.age_business_days ?? 0} business days`],
          ...(r.paused_hours ? ([['Paused (waiting on customer)', `${r.paused_hours.toFixed(1)} working h`]] as [string, string][]) : []),
          ...(r.close_reason ? ([['Closed', `${STATUS_LABEL[r.status] ?? r.status}: ${r.close_reason}`]] as [string, string][]) : []),
        ]}
      />

      {canEdit && !CLOSED.includes(r.status) && (
        <form
          className="grid gap-3 rounded-md border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (Object.keys(changes).length && (!needsReason || form.reason.trim())) save.mutate();
          }}
        >
          <PanelHeading>Move it along</PanelHeading>
          <Field label="Status">
            <NativeSelect value={form.status} onChange={set('status')}>
              <option value="">Keep: {STATUS_LABEL[r.status] ?? r.status}</option>
              {NEXT.filter((s) => s !== r.status).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {needsReason && (
            <Field label="Why?" hint="Price, lead time, spec, no response… The win-rate report learns from this.">
              <Input value={form.reason} onChange={set('reason')} required />
            </Field>
          )}
          {form.status === 'WAITING_VENDOR' && (
            <Field label="Vendor asked for a price">
              <Input value={form.vendor} onChange={set('vendor')} />
            </Field>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Quote number">
              <Input value={form.quote_number} onChange={set('quote_number')} />
            </Field>
            <Field label="Quote total (USD)">
              <Input inputMode="decimal" value={form.quote_amount} onChange={set('quote_amount')} />
            </Field>
          </div>
          <Field label="Owner">
            <Input type="email" placeholder={r.owner_email || 'name@alliedweldingsystems.com'} value={form.owner_email} onChange={set('owner_email')} />
          </Field>
          <Field label="Note">
            <Textarea rows={2} value={form.note} onChange={set('note')} />
          </Field>
          <Button type="submit" size="sm" className="justify-self-start" disabled={!Object.keys(changes).length || (needsReason && !form.reason.trim()) || save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </form>
      )}

      {(r.history?.length ?? 0) > 0 && (
        <div className="grid gap-2">
          <PanelHeading>History</PanelHeading>
          <ol className="grid gap-3 border-l pl-4">
            {[...(r.history ?? [])].reverse().map((h, i) => (
              <li key={`${h.at}-${i}`} className="relative grid gap-0.5 text-[13px]">
                <span className="absolute top-1.5 -left-[21px] size-2 rounded-full bg-muted-foreground/60" aria-hidden />
                <span className="font-medium">{h.change}</span>
                <span className="text-xs text-muted-foreground">
                  {dateTime(h.at)} · {h.by}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function AddRfq({ open, onOpenChange, onAdded }: { open: boolean; onOpenChange: (open: boolean) => void; onAdded: (id: number) => void }) {
  const client = useQueryClient();
  const blank = { customer: '', contact_email: '', subject: '', summary: '', complexity: 'STANDARD', owner_email: '' };
  const [form, setForm] = useState(blank);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const add = useMutation({
    mutationFn: async () => (await addRfq({ ...form, owner_email: form.owner_email || null, contact_email: form.contact_email || null })) as Rfq,
    onSuccess: (r) => {
      toast.success(`Logged ${r.number}`);
      void client.invalidateQueries({ queryKey: ['rfqs'] });
      setForm(blank);
      onOpenChange(false);
      onAdded(r.id);
    },
    onError: (e) => toast.error('The RFQ wasn’t logged', { description: e.message }),
  });
  const ready = form.customer.trim() || form.subject.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Log an RFQ</DialogTitle>
          <DialogDescription>For requests that came by phone or in person. Emailed ones are logged automatically.</DialogDescription>
        </DialogHeader>
        <form
          id="add-rfq"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) add.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Customer">
              <Input value={form.customer} onChange={set('customer')} />
            </Field>
            <Field label="Contact email">
              <Input type="email" value={form.contact_email} onChange={set('contact_email')} />
            </Field>
          </div>
          <Field label="What to quote">
            <Input value={form.subject} onChange={set('subject')} />
          </Field>
          <Field label="Details">
            <Textarea rows={3} value={form.summary} onChange={set('summary')} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Kind of quote" hint="Sets the deadline.">
              <NativeSelect value={form.complexity} onChange={set('complexity')}>
                <option value="SIMPLE">Repeat or catalogue part (1 day)</option>
                <option value="STANDARD">Standard (2 days)</option>
                <option value="COMPLEX">New design or vendor pricing (5 days)</option>
              </NativeSelect>
            </Field>
            <Field label="Owner">
              <Input type="email" value={form.owner_email} onChange={set('owner_email')} placeholder="Who will quote it" />
            </Field>
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="add-rfq" disabled={!ready || add.isPending}>
            {add.isPending && <Loader2 className="animate-spin" />}
            Log RFQ
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
