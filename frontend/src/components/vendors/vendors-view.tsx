'use client';

import { useMemo, useState } from 'react';
import { parseAsBoolean, parseAsInteger, parseAsStringLiteral, useQueryState } from 'nuqs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus, RefreshCw } from 'lucide-react';
import { DataTable, columnHelper, recordKey } from '@/components/data/data-table';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { Field, FilterChips, NativeSelect, PanelHeading, SectionTabs } from '@/components/data/filters';
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
import { dateTime, day, person, since } from '@/lib/format';
import { addVendorJob, fetchVendorJobs, syncVendorBoard, updateVendorJob } from '@/lib/opsApi';
import { cn } from '@/lib/utils';

type Job = {
  id: number;
  vendor: string;
  description?: string | null;
  po_number?: string | null;
  project_item_id?: string | null;
  project_name?: string | null;
  project_due?: string | null;
  owner_email?: string | null;
  mode: string;
  state: string;
  reasons?: string[];
  ordered_on?: string | null;
  requested_on?: string | null;
  acknowledged_on?: string | null;
  quoted_lead_days?: number | null;
  promised_original?: string | null;
  promised_on?: string | null;
  shipped_on?: string | null;
  expected?: string | null;
  expected_basis?: string | null;
  need_by?: string | null;
  need_source?: string | null;
  slack_days?: number | null;
  slips?: number;
  source?: string;
  history?: { at: string; change: string; by: string }[];
};
type Score = {
  vendor: string;
  otd_promise: number | null;
  otd_requested: number | null;
  below_target?: boolean;
  avg_days_late?: number | null;
  lead_median?: number | null;
  lead_p80?: number | null;
  quoted_median?: number | null;
  slower_than_quoted?: boolean;
  slips: number;
  open: number;
};
type Overview = {
  jobs: Job[];
  vendors: Score[];
  counts: Record<string, number>;
  otd_target?: number;
  board_connected?: boolean;
  last_sync?: { at?: string } | null;
};

export const STATE: Record<string, { label: string; tone: Tone }> = {
  late: { label: 'Vendor late', tone: 'bad' },
  will_miss: { label: 'Will make project late', tone: 'bad' },
  at_risk: { label: 'At risk', tone: 'warn' },
  no_ack: { label: 'PO not confirmed', tone: 'warn' },
  checkin: { label: 'Reconfirm date', tone: 'info' },
  no_dates: { label: 'No dates yet', tone: 'neutral' },
  on_track: { label: 'On track', tone: 'ok' },
  shipped: { label: 'Shipped', tone: 'neutral' },
};
const PROBLEMS = ['late', 'will_miss', 'at_risk', 'no_ack'];
const MODES = [
  ['SEA', 'Sea'],
  ['AIR', 'Air'],
  ['TRUCK', 'Truck'],
] as const;
const pct = (v?: number | null) => (v == null ? '–' : `${Math.round(v * 100)}%`);
const slack = (d?: number | null) => (d == null ? '' : d >= 0 ? `${d} days to spare` : `${-d} days too late`);

const SHOW = ['problems', 'all'] as const;
const TABS = ['jobs', 'scores'] as const;

export function VendorsView() {
  const { hasRole } = useAuth();
  const canEdit = hasRole('OPERATOR');
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('jobs'));
  const [shipped, setShipped] = useQueryState('shipped', parseAsBoolean.withDefault(false));
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['vendors', shipped],
    queryFn: async () => (await fetchVendorJobs(shipped)) as Overview,
  });

  return (
    <div className="grid gap-4">
      {data && !data.board_connected && (
        <p className="rounded-md bg-info-soft p-3 text-sm">
          The monday vendor board isn’t connected yet. Jobs added here are tracked now; to read them from monday, set MONDAY_VENDOR_BOARD_ID
          and the VENDOR_…_COLUMN_ID settings.
        </p>
      )}
      <SectionTabs
        label="Vendor sections"
        value={tab}
        onChange={(t) => void setTab(t === 'jobs' ? null : t)}
        tabs={[
          ['jobs', 'Vendor jobs'],
          ['scores', 'Vendor scorecard'],
        ]}
      />
      {isPending ? (
        <RowsSkeleton rows={8} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : tab === 'jobs' ? (
        <Jobs data={data!} canEdit={canEdit} shipped={shipped} setShipped={(v) => void setShipped(v || null)} />
      ) : (
        <Scorecard data={data!} />
      )}
    </div>
  );
}

const col = columnHelper<Job>();

function Jobs({ data, canEdit, shipped, setShipped }: { data: Overview; canEdit: boolean; shipped: boolean; setShipped: (v: boolean) => void }) {
  const client = useQueryClient();
  const { employeeEmail } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [show, setShow] = useQueryState('show', parseAsStringLiteral(SHOW));
  // `job` is the link alerts and autopilot already use.
  const [id, setId] = useQueryState('job', parseAsInteger);
  const [adding, setAdding] = useState(false);

  const jobs = data.jobs;
  const problems = useMemo(() => jobs.filter((j) => PROBLEMS.includes(j.state)), [jobs]);
  const linked = id != null ? jobs.find((j) => j.id === id) : undefined;
  const current = show ?? (linked && !PROBLEMS.includes(linked.state) ? 'all' : problems.length ? 'problems' : 'all');
  const rows = current === 'problems' ? problems : jobs;
  const index = rows.findIndex((j) => j.id === id);
  const open = (j?: Job) => void setId(j ? j.id : null);
  useListNav(rows, index, open);

  const sync = useMutation({
    mutationFn: async () => (await syncVendorBoard()) as { skipped?: string; jobs?: number; added?: number; changed?: number; removed?: number },
    onSuccess: (r) => {
      if (r.skipped) toast.warning('The board wasn’t read', { description: r.skipped });
      else toast.success(`Read ${r.jobs} jobs`, { description: `${r.added} new, ${r.changed} changed, ${r.removed} removed.` });
      void client.invalidateQueries({ queryKey: ['vendors'] });
    },
    onError: (e) => toast.error('Couldn’t read the board', { description: e.message }),
  });

  const c = data.counts ?? {};
  const columns = col.columns([
    col.accessor('vendor', { header: 'Vendor', meta: { className: 'font-medium' } }),
    col.display({
      id: 'what',
      header: 'Making',
      meta: { className: 'max-w-72 truncate' },
      cell: (x) => {
        const j = x.row.original;
        return (
          <span className="grid">
            <span className="truncate">{j.description || j.po_number || `Job ${j.id}`}</span>
            {j.project_name && <span className="truncate text-[11px] text-muted-foreground">{j.project_name}</span>}
          </span>
        );
      },
    }),
    col.accessor('expected', { header: 'Expected', cell: (x) => day(x.getValue()) }),
    col.accessor('need_by', { header: 'Must ship by', cell: (x) => day(x.getValue()) }),
    col.accessor('slack_days', {
      header: 'Spare',
      meta: { align: 'right' },
      cell: (x) => (x.getValue() == null ? '–' : <span className={cn(x.getValue()! < 0 && 'font-semibold text-bad')}>{x.getValue()} d</span>),
    }),
    col.accessor('state', {
      header: 'State',
      cell: (x) => {
        const s = STATE[x.getValue()] ?? { label: x.getValue(), tone: 'neutral' as Tone };
        return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
      },
    }),
    col.accessor('owner_email', { header: 'Owner', cell: (x) => person(x.getValue(), employeeEmail) || '–' }),
  ]);

  const summary = [
    [c.will_miss, 'will make a project late'],
    [c.late, 'vendor late'],
    [c.at_risk, 'at risk'],
    [c.no_ack, 'PO not confirmed'],
  ].filter(([n]) => Number(n) > 0) as [number, string][];

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={current}
          onChange={(v) => void setShow(v)}
          options={[
            { value: 'problems', label: 'Needs action', count: problems.length, tone: 'bad' },
            { value: 'all', label: 'All', count: jobs.length },
          ]}
        />
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input type="checkbox" checked={shipped} onChange={(e) => setShipped(e.target.checked)} />
          Show shipped
        </label>
        {canEdit && (
          <div className="ml-auto flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={sync.isPending} onClick={() => sync.mutate()} title={data.last_sync?.at ? `Last read ${since(data.last_sync.at)}` : undefined}>
              {sync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Read the monday board
            </Button>
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus /> Add a vendor job
            </Button>
          </div>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {summary.length ? summary.map(([n, what]) => `${n} ${what}`).join(' · ') : 'No vendor is putting a project at risk.'} Must ship by = project due date, less
        transit and a buffer.
      </p>

      <div className={cn('grid items-start gap-4', wide && linked && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="grid min-w-0 gap-2">
          <DataTable
            label="Vendor jobs"
            columns={columns}
            data={rows}
            getRowId={(j) => String(j.id)}
            recordName={(j) => recordKey('job', j.id)}
            selectedId={id != null ? String(id) : null}
            onSelect={open}
            minWidth={860}
            empty={
              <EmptyState title={current === 'problems' ? 'No vendor is putting a project at risk' : 'No vendor jobs yet'}>
                Add a vendor PO with its dates, or connect the monday vendor board.
              </EmptyState>
            }
          />
          {rows.length > 0 && (
            <p className="hidden text-xs text-muted-foreground md:block">
              <Kbd>J</Kbd>
              <Kbd>K</Kbd> move · <Kbd>Esc</Kbd> close
            </p>
          )}
          {id != null && !linked && (
            <p role="status" className="text-sm text-warn">
              That vendor job isn’t on the list{shipped ? '' : '; it may have shipped (tick Show shipped)'}.
            </p>
          )}
        </div>
        <DetailPanel open={Boolean(linked)} onClose={() => open(undefined)} recordName={linked ? recordKey('job', linked.id) : undefined} title={linked?.vendor ?? ''} description={linked ? linked.description || linked.po_number || undefined : undefined}>
          {linked && <JobDetail key={`${linked.id}-${linked.history?.length}`} job={linked} canEdit={canEdit} />}
        </DetailPanel>
      </div>
      {canEdit && <AddJob open={adding} onOpenChange={setAdding} onAdded={(jid) => void setId(jid).then(() => setShow('all'))} />}
    </div>
  );
}

function JobDetail({ job: j, canEdit }: { job: Job; canEdit: boolean }) {
  const client = useQueryClient();
  const { employeeEmail } = useAuth();
  const blank = { acknowledged_on: '', promised_on: '', shipped_on: '', need_by: '', mode: j.mode, note: '' };
  const [form, setForm] = useState(blank);
  const changes = Object.fromEntries(Object.entries(form).filter(([k, v]) => v && (k !== 'mode' || v !== j.mode)));
  const save = useMutation({
    mutationFn: () => updateVendorJob(j.id, changes),
    onSuccess: () => {
      toast.success(j.source === 'monday' ? 'Saved here and on monday' : 'Saved');
      setForm({ ...blank, mode: (changes.mode as string) || j.mode });
      void client.invalidateQueries({ queryKey: ['vendors'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  const st = STATE[j.state] ?? { label: j.state, tone: 'neutral' as Tone };
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-1.5">
        <ToneBadge tone={st.tone}>{st.label}</ToneBadge>
        {(j.reasons ?? []).map((r) => (
          <ToneBadge key={r} tone={st.tone === 'ok' ? 'neutral' : st.tone}>
            {r}
          </ToneBadge>
        ))}
      </div>
      <Facts
        items={[
          ['PO', j.po_number ? <span key="po" className="font-mono text-xs">{j.po_number}</span> : '–'],
          ['Project', j.project_name || j.project_item_id || '–'],
          ['PO placed', day(j.ordered_on)],
          ['We asked for', day(j.requested_on)],
          ['Vendor confirmed', day(j.acknowledged_on)],
          ['Quoted lead time', j.quoted_lead_days != null ? `${j.quoted_lead_days} days` : '–'],
          ['First promise', day(j.promised_original)],
          ['Current promise', day(j.promised_on)],
          ['Shipped', day(j.shipped_on)],
          ['Expected', j.expected ? `${day(j.expected)} (${j.expected_basis})` : '–'],
          ['Must ship by', j.need_by ? `${day(j.need_by)} (${j.need_source})` : 'No project due date'],
          ['Spare', slack(j.slack_days) || '–'],
          ['Project due', day(j.project_due)],
          ['By', j.mode],
          ['Owner', person(j.owner_email, employeeEmail) || '–'],
          ['Date moved', j.slips ? `${j.slips}×` : 'Never'],
          ['From', j.source === 'monday' ? 'The monday vendor board' : 'Entered here'],
        ]}
      />

      {canEdit && !j.shipped_on && (
        <form
          className="grid gap-3 rounded-md border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (Object.keys(changes).length) save.mutate();
          }}
        >
          <PanelHeading>Record what the vendor said</PanelHeading>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Confirmed the PO on">
              <Input type="date" value={form.acknowledged_on} onChange={set('acknowledged_on')} />
            </Field>
            <Field label="New promised ship date">
              <Input type="date" value={form.promised_on} onChange={set('promised_on')} />
            </Field>
            <Field label="Shipped on">
              <Input type="date" value={form.shipped_on} onChange={set('shipped_on')} />
            </Field>
            <Field label="Freight">
              <NativeSelect value={form.mode} onChange={set('mode')}>
                {MODES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
          <p className="text-xs text-muted-foreground">A later promised date counts as a slip on the vendor’s score.</p>
          <Field label="Ship-by override" hint="Only if the worked-out date is wrong for this job.">
            <Input type="date" value={form.need_by} onChange={set('need_by')} />
          </Field>
          <Field label="Note">
            <Textarea rows={2} value={form.note} onChange={set('note')} />
          </Field>
          <Button type="submit" size="sm" className="justify-self-start" disabled={!Object.keys(changes).length || save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </form>
      )}

      {(j.history?.length ?? 0) > 0 && (
        <div className="grid gap-2">
          <PanelHeading>History</PanelHeading>
          <ol className="grid gap-3 border-l pl-4">
            {[...(j.history ?? [])].reverse().map((h, i) => (
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

function AddJob({ open, onOpenChange, onAdded }: { open: boolean; onOpenChange: (open: boolean) => void; onAdded: (id: number) => void }) {
  const client = useQueryClient();
  const blank = { vendor: '', description: '', po_number: '', project_item_id: '', mode: 'SEA', ordered_on: '', requested_on: '', quoted_lead_days: '', promised_on: '' };
  const [form, setForm] = useState(blank);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const add = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = Object.fromEntries(Object.entries(form).filter(([, v]) => v !== ''));
      if (payload.quoted_lead_days) payload.quoted_lead_days = Number(payload.quoted_lead_days);
      return (await addVendorJob(payload)) as Job;
    },
    onSuccess: (j) => {
      toast.success(`Added ${j.vendor}`);
      void client.invalidateQueries({ queryKey: ['vendors'] });
      setForm(blank);
      onOpenChange(false);
      onAdded(j.id);
    },
    onError: (e) => toast.error('The job wasn’t added', { description: e.message }),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a vendor job</DialogTitle>
          <DialogDescription>One vendor PO. Link it to a project (its monday item number) so the ship-by date can be worked out.</DialogDescription>
        </DialogHeader>
        <form
          id="add-job"
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (form.vendor.trim()) add.mutate();
          }}
        >
          <Field label="Vendor">
            <Input value={form.vendor} onChange={set('vendor')} required />
          </Field>
          <Field label="What they make">
            <Input value={form.description} onChange={set('description')} />
          </Field>
          <Field label="PO number">
            <Input value={form.po_number} onChange={set('po_number')} />
          </Field>
          <Field label="Project item number">
            <Input inputMode="numeric" value={form.project_item_id} onChange={set('project_item_id')} />
          </Field>
          <Field label="PO placed">
            <Input type="date" value={form.ordered_on} onChange={set('ordered_on')} />
          </Field>
          <Field label="Quoted lead time (days)">
            <Input inputMode="numeric" value={form.quoted_lead_days} onChange={set('quoted_lead_days')} />
          </Field>
          <Field label="We asked for">
            <Input type="date" value={form.requested_on} onChange={set('requested_on')} />
          </Field>
          <Field label="Vendor promised">
            <Input type="date" value={form.promised_on} onChange={set('promised_on')} />
          </Field>
          <Field label="Freight">
            <NativeSelect value={form.mode} onChange={set('mode')}>
              {MODES.map(([v, l]) => (
                <option key={v} value={v}>
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
          <Button type="submit" form="add-job" disabled={!form.vendor.trim() || add.isPending}>
            {add.isPending && <Loader2 className="animate-spin" />}
            Add job
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const scol = columnHelper<Score>();
const scoreColumns = scol.columns([
  scol.accessor('vendor', { header: 'Vendor', meta: { className: 'font-medium' } }),
  scol.accessor('otd_promise', {
    header: 'On time',
    meta: { align: 'right' },
    cell: (c) => <ToneBadge tone={c.getValue() == null ? 'neutral' : c.row.original.below_target ? 'bad' : 'ok'}>{pct(c.getValue())}</ToneBadge>,
  }),
  scol.accessor('otd_requested', { header: 'Met our date', meta: { align: 'right' }, cell: (c) => pct(c.getValue()) }),
  scol.accessor('avg_days_late', { header: 'Avg late', meta: { align: 'right' }, cell: (c) => (c.getValue() ? `${c.getValue()} d` : '–') }),
  scol.accessor('lead_median', {
    header: 'Lead time (usual / 8 in 10)',
    meta: { align: 'right' },
    cell: (c) => (c.getValue() != null ? `${c.getValue()} / ${c.row.original.lead_p80} d` : '–'),
  }),
  scol.accessor('quoted_median', {
    header: 'Quoted',
    meta: { align: 'right' },
    cell: (c) =>
      c.getValue() != null ? (
        <span className="inline-flex items-center gap-1">
          {c.getValue()} d {c.row.original.slower_than_quoted && <ToneBadge tone="warn">slower</ToneBadge>}
        </span>
      ) : (
        '–'
      ),
  }),
  scol.accessor('slips', { header: 'Date moves', meta: { align: 'right' } }),
  scol.accessor('open', { header: 'Open', meta: { align: 'right' } }),
]);

function Scorecard({ data }: { data: Overview }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs text-muted-foreground">On time is judged against the vendor’s first promise. Target {pct(data.otd_target)}.</p>
      <DataTable
        label="Vendor scorecard"
        columns={scoreColumns}
        data={data.vendors ?? []}
        getRowId={(v) => v.vendor}
        minWidth={760}
        empty={<EmptyState title="No scores yet">Scores appear once vendor jobs ship.</EmptyState>}
      />
    </div>
  );
}
