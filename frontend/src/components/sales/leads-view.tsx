'use client';

import { useEffect, useMemo, useState } from 'react';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Copy, ExternalLink, Loader2 } from 'lucide-react';
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
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { api, ok } from '@/lib/api/client';
import { generateLeadOutreach } from '@/lib/api';
import { money, words } from '@/lib/format';
import { cn } from '@/lib/utils';

type Account = {
  account_id: string;
  plant_name: string;
  company: string;
  industry: string;
  location: string;
  key_contact: string;
  contact_title: string;
  estimated_annual_spend: number;
  primary_wear_bottlenecks: string[];
  status: string;
};

const STATUS_TONE: Record<string, Tone> = { ACTIVE: 'ok', QUALIFIED: 'info', QUOTED: 'info', PROSPECT: 'neutral' };
const INDUSTRIES = ['All', 'Asphalt', 'Glass', 'Cement', 'Gypsum'] as const;
const TABS = ['accounts', 'prospects', 'savings', 'upgrade'] as const;

export function LeadsView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('accounts'));
  return (
    <div className="grid gap-4">
      <SectionTabs
        label="Sales sections"
        value={tab}
        onChange={(t) => void setTab(t === 'accounts' ? null : t)}
        tabs={[
          ['accounts', 'Target accounts'],
          ['prospects', 'Find prospects'],
          ['savings', 'Savings calculator'],
          ['upgrade', 'Upgrade proposal'],
        ]}
      />
      {tab === 'accounts' && <Accounts />}
      {tab === 'savings' && <SavingsCalculator />}
      {tab === 'prospects' && <Prospects />}
      {tab === 'upgrade' && <UpgradeProposal />}
    </div>
  );
}

const col = columnHelper<Account>();

function Accounts() {
  const wide = useMediaQuery('(min-width: 1280px)');
  const [industry, setIndustry] = useQueryState('industry', parseAsStringLiteral(INDUSTRIES).withDefault('All'));
  const [id, setId] = useQueryState('account');
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['leads', 'targets'],
    queryFn: async () => (await ok(api.GET('/api/v1/leads/targets'))) as Account[],
  });
  const list = useMemo(() => data ?? [], [data]);
  const inIndustry = (a: Account, i: string) => i === 'All' || a.industry.toLowerCase().includes(i.toLowerCase());
  const rows = list.filter((a) => inIndustry(a, industry));
  const index = rows.findIndex((a) => a.account_id === id);
  const selected = list.find((a) => a.account_id === id);
  const open = (a?: Account) => void setId(a ? a.account_id : null);
  useListNav(rows, index, open);

  const columns = col.columns([
    col.accessor('company', {
      header: 'Company',
      meta: { className: 'max-w-64 truncate' },
      cell: (c) => (
        <span className="grid">
          <span className="truncate font-medium">{c.getValue()}</span>
          <span className="truncate text-[11px] text-muted-foreground">
            {c.row.original.plant_name} · {c.row.original.location}
          </span>
        </span>
      ),
    }),
    col.accessor('key_contact', {
      header: 'Contact',
      cell: (c) => (
        <span className="grid">
          <span>{c.getValue()}</span>
          <span className="text-[11px] text-muted-foreground">{c.row.original.contact_title}</span>
        </span>
      ),
    }),
    col.accessor('primary_wear_bottlenecks', { header: 'Parts that wear out', meta: { className: 'max-w-64 truncate' }, cell: (c) => c.getValue().join(', ') || '–' }),
    col.accessor('estimated_annual_spend', { header: 'Est. spend / yr', meta: { align: 'right' }, cell: (c) => money(c.getValue()) }),
    col.accessor('status', { header: 'Status', cell: (c) => <ToneBadge tone={STATUS_TONE[c.getValue()] ?? 'neutral'}>{words(c.getValue())}</ToneBadge> }),
  ]);

  return (
    <div className="grid gap-3">
      <FilterChips
        label="Industry"
        value={industry}
        onChange={(v) => {
          void setIndustry(v === 'All' ? null : v);
          open(undefined);
        }}
        options={INDUSTRIES.map((i) => ({ value: i, label: i === 'All' ? 'All industries' : i, count: data ? list.filter((a) => inIndustry(a, i)).length : undefined }))}
      />
      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="min-w-0">
          {isPending ? (
            <RowsSkeleton rows={6} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Target accounts"
              columns={columns}
              data={rows}
              getRowId={(a) => a.account_id}
              recordName={(a) => recordKey('account', a.account_id)}
              selectedId={id}
              onSelect={open}
              minWidth={820}
              empty={<EmptyState title={list.length ? 'No accounts in this industry' : 'No target accounts yet'} />}
            />
          )}
        </div>
        <DetailPanel open={Boolean(selected)} onClose={() => open(undefined)} recordName={selected ? recordKey('account', selected.account_id) : undefined} title={selected?.company ?? ''} description={selected ? `${selected.plant_name} · ${selected.location}` : undefined}>
          {selected && <AccountDetail key={selected.account_id} account={selected} />}
        </DetailPanel>
      </div>
    </div>
  );
}

function AccountDetail({ account: a }: { account: Account }) {
  const { hasRole } = useAuth();
  const canAct = hasRole('OPERATOR');
  const [draft, setDraft] = useState('');
  const [asking, setAsking] = useState(false);
  const outreach = useMutation({
    mutationFn: async () =>
      (await generateLeadOutreach({ name: a.key_contact, title: a.contact_title, company: a.company, industry: a.industry })) as { email_body: string },
    onSuccess: (res) => setDraft(res.email_body),
    onError: (e) => toast.error('No draft this time', { description: e.message }),
  });

  return (
    <div className="grid gap-4">
      <ToneBadge tone={STATUS_TONE[a.status] ?? 'neutral'} className="justify-self-start">
        {words(a.status)}
      </ToneBadge>
      <Facts
        items={[
          ['Contact', `${a.key_contact}, ${a.contact_title}`],
          ['Industry', a.industry],
          ['Est. spend / yr', money(a.estimated_annual_spend)],
          ['Wears out', a.primary_wear_bottlenecks.join(', ') || '–'],
        ]}
      />

      {canAct ? (
        <>
          <div className="grid gap-2 rounded-md border p-3">
            <PanelHeading>Introduction email</PanelHeading>
            {draft ? (
              <>
                <Textarea rows={10} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Email draft" />
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => void copy(draft)}>
                    <Copy /> Copy text
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => outreach.mutate()} disabled={outreach.isPending}>
                    {outreach.isPending && <Loader2 className="animate-spin" />}
                    Write another
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">Edit it, then copy it into your own email. Nothing is sent from here.</p>
              </>
            ) : (
              <Button size="sm" variant="outline" className="justify-self-start" onClick={() => outreach.mutate()} disabled={outreach.isPending}>
                {outreach.isPending && <Loader2 className="animate-spin" />}
                {outreach.isPending ? 'Writing…' : `Draft an email to ${a.key_contact}`}
              </Button>
            )}
          </div>
          <div className="grid gap-2 rounded-md border p-3">
            <PanelHeading>Drawings and specs</PanelHeading>
            <p className="text-xs text-muted-foreground">Send the contact a link to the spec form, so engineering can quote to their exact sizes.</p>
            <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setAsking(true)}>
              Ask for drawings…
            </Button>
          </div>
          <AskForSpecsDialog open={asking} onOpenChange={setAsking} contact={a.key_contact} />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Drafting emails and asking for drawings need an operator account.</p>
      )}
    </div>
  );
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success('Copied');
  } catch {
    toast.error('Couldn’t copy; select the text and copy it instead');
  }
}

function AskForSpecsDialog({ open, onOpenChange, contact }: { open: boolean; onOpenChange: (open: boolean) => void; contact: string }) {
  const [email, setEmail] = useState('');
  const [item, setItem] = useState('');
  const ask = useMutation({
    mutationFn: async () =>
      (await ok(api.POST('/api/v1/leads/crm-form-request', { body: { lead_email: email.trim(), pulse_id: Number(item) || 0 } }))) as {
        status: string;
        delivery_detail?: string;
      },
    onSuccess: (res) => {
      if (res.status === 'sent') toast.success(`Spec form sent to ${email.trim()}`);
      else if (res.status === 'drafted') toast.info('Kept as a draft, not sent', { description: res.delivery_detail ?? 'Outgoing mail isn’t set up.' });
      else toast.error('The email failed', { description: res.delivery_detail });
      setEmail('');
      setItem('');
      onOpenChange(false);
    },
    onError: (e) => toast.error('The request wasn’t made', { description: e.message }),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Ask {contact} for drawings?</DialogTitle>
          <DialogDescription>
            This emails a link to the spec form. If outgoing mail isn’t set up it’s kept as a draft instead. With a monday item,
            a note is added to that item too.
          </DialogDescription>
        </DialogHeader>
        <form
          id="ask-specs"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            ask.mutate();
          }}
        >
          <Field label="Their email address">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Field label="monday item number" hint="Optional: the lead’s item on the sales board.">
            <Input inputMode="numeric" pattern="[0-9]*" value={item} onChange={(e) => setItem(e.target.value)} />
          </Field>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="ask-specs" disabled={!email.trim() || ask.isPending}>
            {ask.isPending && <Loader2 className="animate-spin" />}
            Send the form
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const ROI_DEFAULTS = {
  equipment_type: 'Hammermill Screen',
  current_material: 'AR400 Carbon Steel',
  allied_upgrade_material: 'Allied 300XL Chromium Carbide Overlay',
  current_part_cost: '1200',
  allied_part_cost: '2800',
  current_lifespan_months: '3',
  allied_lifespan_months: '12',
  replacement_labor_cost: '2500',
  downtime_cost_per_hour: '1500',
  downtime_hours_per_swap: '6',
};
type RoiKey = keyof typeof ROI_DEFAULTS;
const ROI_FIELDS: [RoiKey, string, boolean?][] = [
  ['current_material', 'Their material now', true],
  ['allied_upgrade_material', 'Allied material', true],
  ['current_part_cost', 'Their part costs ($)'],
  ['allied_part_cost', 'Allied part costs ($)'],
  ['current_lifespan_months', 'Theirs lasts (months)'],
  ['allied_lifespan_months', 'Allied lasts (months)'],
  ['replacement_labor_cost', 'Labour and crane per swap ($)'],
  ['downtime_cost_per_hour', 'Downtime per hour ($)'],
  ['downtime_hours_per_swap', 'Downtime hours per swap'],
];
const TEXT_KEYS = new Set(['equipment_type', ...ROI_FIELDS.filter(([, , text]) => text).map(([k]) => k)]);
const EQUIPMENT = ['Hammermill Screen', 'Slurry Material Hose', 'Screw Conveyor Flight', 'Chute Impact Liner'];

type Roi = {
  net_annual_savings: number;
  savings_percentage: number;
  lifespan_multiplier: number;
  payback_period_days: number;
  current_annual_tco: number;
  allied_annual_tco: number;
  executive_summary: string;
};

function SavingsCalculator() {
  const [form, setForm] = useState(ROI_DEFAULTS);
  const [debounced, setDebounced] = useState(ROI_DEFAULTS);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(form), 300);
    return () => clearTimeout(t);
  }, [form]);

  const body = Object.fromEntries(Object.entries(debounced).map(([k, v]) => [k, TEXT_KEYS.has(k) ? v : Number(v) || 0]));
  const { data: roi, error, isFetching } = useQuery({
    queryKey: ['leads', 'roi', body],
    queryFn: async () => (await ok(api.POST('/api/v1/leads/roi-calculator', { body: body as never }))) as Roi,
    placeholderData: keepPreviousData,
  });

  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <section className="grid gap-3 rounded-lg border bg-card p-4">
        <h2 className="text-sm font-semibold">The customer’s numbers</h2>
        <Field label="Part">
          <NativeSelect value={form.equipment_type} onChange={(e) => setForm({ ...form, equipment_type: e.target.value })}>
            {EQUIPMENT.map((e) => (
              <option key={e}>{e}</option>
            ))}
          </NativeSelect>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          {ROI_FIELDS.map(([key, label, text]) => (
            <Field key={key} label={label}>
              <Input
                type={text ? 'text' : 'number'}
                inputMode={text ? undefined : 'decimal'}
                min={text ? undefined : '0'}
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </Field>
          ))}
        </div>
      </section>

      <section className={cn('grid gap-4 rounded-lg border bg-card p-4 transition-opacity', isFetching && 'opacity-70')} aria-live="polite">
        <h2 className="text-sm font-semibold">What they save with Allied, per year</h2>
        {error ? (
          <p role="alert" className="text-sm text-bad">
            {error.message}
          </p>
        ) : !roi ? (
          <RowsSkeleton rows={3} />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Big label="Saved per year" value={money(roi.net_annual_savings)} good />
              <Big label="Lower running cost" value={`${roi.savings_percentage}%`} />
              <Big label="Lasts longer" value={`${roi.lifespan_multiplier}×`} />
              <Big label="Pays for itself in" value={`${roi.payback_period_days} days`} />
            </div>
            <Facts
              items={[
                ['Yearly cost today', money(roi.current_annual_tco)],
                ['Yearly cost with Allied', money(roi.allied_annual_tco)],
              ]}
            />
            {roi.executive_summary && (
              <div className="grid gap-1.5 rounded-md bg-muted/50 p-3">
                <PanelHeading>For the customer</PanelHeading>
                <p className="text-sm text-pretty">{roi.executive_summary}</p>
                <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => void copy(roi.executive_summary)}>
                  <Copy /> Copy
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function Big({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="grid gap-0.5 rounded-md border p-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={cn('text-xl font-semibold tabular-nums', good && 'text-ok')}>{value}</span>
    </div>
  );
}

function UpgradeProposal() {
  const { hasRole } = useAuth();
  const [part, setPart] = useState('');
  const [material, setMaterial] = useState('A36 Carbon Steel');
  const proposal = useMutation({
    mutationFn: async () =>
      (await ok(api.POST('/api/v1/leads/oem-upgrade', { body: { current_part: part.trim(), oem_material: material.trim() } }))) as {
        recommended_upgrade: string;
        proposal_text: string;
      },
    onError: (e) => toast.error('Couldn’t write the proposal', { description: e.message }),
  });
  const [text, setText] = useState('');

  if (!hasRole('OPERATOR')) return <p className="text-sm text-muted-foreground">Writing upgrade proposals needs an operator account.</p>;

  return (
    <div className="grid max-w-2xl gap-4">
      <p className="text-sm text-muted-foreground">
        When a customer asks for a part in standard steel, propose the longer-lasting Allied material instead.
      </p>
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          proposal.mutate(undefined, { onSuccess: (res) => setText(res.proposal_text) });
        }}
      >
        <Field label="Part they asked for">
          <Input value={part} onChange={(e) => setPart(e.target.value)} placeholder="Chute liner" required />
        </Field>
        <Field label="In this material">
          <Input value={material} onChange={(e) => setMaterial(e.target.value)} required />
        </Field>
        <Button type="submit" disabled={!part.trim() || !material.trim() || proposal.isPending}>
          {proposal.isPending && <Loader2 className="animate-spin" />}
          Write proposal
        </Button>
      </form>
      {proposal.data && (
        <div className="grid gap-2 rounded-lg border bg-card p-4">
          <p className="text-sm">
            Recommend <span className="font-medium">{proposal.data.recommended_upgrade}</span>.
          </p>
          <Textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} aria-label="Proposal text" />
          <Button size="sm" variant="outline" className="justify-self-start" onClick={() => void copy(text)}>
            <Copy /> Copy text
          </Button>
        </div>
      )}
    </div>
  );
}

type Prospect = { name: string; title: string; company: string; snippet: string; profile_url: string };
const pcol = columnHelper<Prospect>();
const prospectColumns = pcol.columns([
  pcol.accessor('name', {
    header: 'Person',
    cell: (c) => (
      <span className="grid">
        <span className="font-medium">{c.getValue() || 'Name not shown'}</span>
        <span className="text-[11px] text-muted-foreground">{c.row.original.title}</span>
      </span>
    ),
  }),
  pcol.accessor('company', { header: 'Company', meta: { className: 'max-w-48 truncate' }, cell: (c) => c.getValue() || '–' }),
  pcol.accessor('snippet', { header: 'From their profile', meta: { className: 'max-w-96 truncate' } }),
  pcol.accessor('profile_url', {
    header: '',
    cell: (c) =>
      /^https:\/\//.test(c.getValue()) ? (
        <a href={c.getValue()} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs underline underline-offset-2">
          Profile <ExternalLink className="size-3" />
        </a>
      ) : null,
  }),
]);

const list = (v: string) =>
  v
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

function Prospects() {
  const { hasRole } = useAuth();
  const [titles, setTitles] = useState('Maintenance Manager, Reliability Engineer');
  const [industries, setIndustries] = useState('Cement');
  const [location, setLocation] = useState('');
  const search = useMutation({
    mutationFn: async () =>
      (await ok(
        api.POST('/api/v1/leads/scrape', {
          body: { job_titles: list(titles), industries: list(industries), location: location.trim(), max_results: 15 },
        }),
      )) as Prospect[],
  });

  if (!hasRole('OPERATOR')) return <EmptyState title="Searching for prospects needs an operator account" />;
  const ready = list(titles).length > 0 && list(industries).length > 0 && location.trim();

  return (
    <div className="grid gap-4">
      <form
        className="grid gap-3 rounded-lg border bg-card p-4 sm:grid-cols-[1fr_1fr_14rem_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) search.mutate();
        }}
      >
        <Field label="Job titles" hint="Separate with commas.">
          <Input value={titles} onChange={(e) => setTitles(e.target.value)} />
        </Field>
        <Field label="Industries" hint="Separate with commas.">
          <Input value={industries} onChange={(e) => setIndustries(e.target.value)} />
        </Field>
        <Field label="Where" hint="City, state or country.">
          <Input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Texas" />
        </Field>
        <Button type="submit" disabled={!ready || search.isPending} className="sm:mb-5">
          {search.isPending && <Loader2 className="animate-spin" />}
          Search
        </Button>
      </form>
      <p className="text-xs text-muted-foreground">
        Searches public LinkedIn profiles through the web. Results are only what the search found, with no email addresses; check a
        profile before reaching out.
      </p>
      {search.isPending ? (
        <RowsSkeleton rows={5} />
      ) : search.error ? (
        <ErrorState error={search.error} onRetry={() => search.mutate()} />
      ) : search.data ? (
        <DataTable
          label="Prospects"
          columns={prospectColumns}
          data={search.data}
          getRowId={(p) => p.profile_url || `${p.name}-${p.company}`}
          minWidth={720}
          empty={<EmptyState title="Nobody matched">Try other job titles, or a wider area.</EmptyState>}
        />
      ) : null}
    </div>
  );
}
