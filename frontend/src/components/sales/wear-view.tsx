'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, ScanSearch } from 'lucide-react';
import { DataTable, columnHelper, recordKey } from '@/components/data/data-table';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { Field, FilterChips, PanelHeading } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button, buttonVariants } from '@/components/ui/button';
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
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { api, ok } from '@/lib/api/client';
import { day, money } from '@/lib/format';
import { cn } from '@/lib/utils';

type Equipment = {
  equipment_tag: string;
  customer_id: string;
  customer_name: string;
  plant_location: string;
  component_name: string;
  installed_item_id: string;
  install_date: string;
  expected_lifespan_days: number;
  days_in_service: number;
  estimated_wear_pct: number;
  alert_level: 'HEALTHY' | 'WARNING_75_PCT' | 'CRITICAL_REPLACE' | string;
  suggested_replacement_sku: string;
  historical_unit_price: number | string;
  last_service_order: string;
  lifespan_basis?: string;
  lifespan_basis_detail?: string;
};

type Quote = {
  quote_number: string;
  customer_name: string;
  plant_location: string;
  equipment_tag: string;
  component_name: string;
  suggested_sku: string;
  quoted_unit_price: number;
  regular_price: number;
  discount_applied_pct: number;
  target_lead_time_weeks: number;
  urgency_rationale?: string;
};

type Scan = {
  scan_summary: string;
  alerts_dispatched: number;
  healthy_skipped: number;
  alerts: { equipment_tag: string; customer_name: string; alert_level: string; wear_pct: number; quote_number?: string; quoted_price?: number }[];
};

type Level = 'critical' | 'warning' | 'ok';
const LEVEL: Record<Level, { label: string; tone: Tone; bar: string }> = {
  critical: { label: 'Replace now', tone: 'bad', bar: 'bg-bad' },
  warning: { label: 'Reorder soon', tone: 'warn', bar: 'bg-warn' },
  ok: { label: 'Good', tone: 'ok', bar: 'bg-ok' },
};

function level(e: Equipment): Level {
  const pct = Number(e.estimated_wear_pct) || 0;
  if (e.alert_level === 'CRITICAL_REPLACE' || pct >= 95) return 'critical';
  if (e.alert_level === 'WARNING_75_PCT' || pct >= 75) return 'warning';
  return 'ok';
}

const VIEWS = ['worn', 'critical', 'all'] as const;
const col = columnHelper<Equipment>();

export function WearView() {
  const { hasRole } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [view, setView] = useQueryState('show', parseAsStringLiteral(VIEWS).withDefault('worn'));
  const [tag, setTag] = useQueryState('part');
  const [scanning, setScanning] = useState(false);

  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['wear'],
    queryFn: async () => (await ok(api.GET('/api/v1/sales/predictive-wear'))) as unknown as Equipment[],
  });
  const list = useMemo(() => [...(data ?? [])].sort((a, b) => b.estimated_wear_pct - a.estimated_wear_pct), [data]);
  const worn = list.filter((e) => level(e) !== 'ok');
  const critical = list.filter((e) => level(e) === 'critical');
  const rows = view === 'all' ? list : view === 'critical' ? critical : worn;
  const index = rows.findIndex((e) => e.equipment_tag === tag);
  const selected = list.find((e) => e.equipment_tag === tag);

  const open = (e?: Equipment) => void setTag(e ? e.equipment_tag : null);
  useListNav(rows, index, open);

  const columns = col.columns([
    col.accessor('component_name', {
      header: 'Part',
      meta: { className: 'max-w-64 truncate' },
      cell: (c) => (
        <span className="grid">
          <span className="truncate font-medium">{c.getValue()}</span>
          <span className="font-mono text-[11px] text-muted-foreground">{c.row.original.equipment_tag}</span>
        </span>
      ),
    }),
    col.accessor('customer_name', {
      header: 'Customer',
      meta: { className: 'max-w-56 truncate' },
      cell: (c) => (
        <span className="grid">
          <span className="truncate">{c.getValue()}</span>
          <span className="truncate text-[11px] text-muted-foreground">{c.row.original.plant_location}</span>
        </span>
      ),
    }),
    col.accessor('estimated_wear_pct', { header: 'Wear', cell: (c) => <WearBar equipment={c.row.original} /> }),
    col.display({
      id: 'level',
      header: 'State',
      cell: (c) => {
        const l = LEVEL[level(c.row.original)];
        return <ToneBadge tone={l.tone}>{l.label}</ToneBadge>;
      },
    }),
    col.accessor('historical_unit_price', { header: 'Last price', meta: { align: 'right' }, cell: (c) => money(c.getValue()) }),
  ]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={view}
          onChange={(v) => {
            void setView(v === 'worn' ? null : v);
            open(undefined);
          }}
          options={[
            { value: 'worn', label: 'Reorder soon', count: data ? worn.length : undefined, tone: 'warn' },
            { value: 'critical', label: 'Replace now', count: data ? critical.length : undefined, tone: 'bad' },
            { value: 'all', label: 'All parts', count: data ? list.length : undefined },
          ]}
        />
        {hasRole('OPERATOR') && (
          <Button variant="outline" size="sm" className="ml-auto" onClick={() => setScanning(true)} disabled={!worn.length}>
            <ScanSearch /> Quote every worn part
          </Button>
        )}
      </div>

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="grid min-w-0 gap-2">
          {isPending ? (
            <RowsSkeleton rows={6} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Customer parts and wear"
              columns={columns}
              data={rows}
              getRowId={(e) => e.equipment_tag}
              recordName={(e) => recordKey('part', e.equipment_tag)}
              selectedId={tag}
              onSelect={open}
              minWidth={760}
              empty={
                <EmptyState title={list.length ? 'Nothing in this group' : 'No parts are being tracked yet'}>
                  {list.length ? 'Every part here is under 75% worn.' : 'Parts we supply to customer plants show up here.'}
                </EmptyState>
              }
            />
          )}
          {rows.length > 0 && (
            <p className="hidden text-xs text-muted-foreground md:block">
              Quote once a part passes 75%, so the customer can reorder before it fails · <Kbd>J</Kbd>
              <Kbd>K</Kbd> move
            </p>
          )}
        </div>
        <DetailPanel
          open={Boolean(selected)}
          onClose={() => open(undefined)}
          recordName={selected ? recordKey('part', selected.equipment_tag) : undefined}
          title={selected?.component_name ?? ''}
          description={selected ? `${selected.customer_name} · ${selected.plant_location}` : undefined}
        >
          {selected && <WearDetail key={selected.equipment_tag} equipment={selected} />}
        </DetailPanel>
      </div>

      <ScanDialog open={scanning} onOpenChange={setScanning} count={worn.length} />
    </div>
  );
}

function WearBar({ equipment: e }: { equipment: Equipment }) {
  const pct = Number(e.estimated_wear_pct) || 0;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className={cn('block h-full rounded-full', LEVEL[level(e)].bar)} style={{ width: `${Math.min(pct, 100)}%` }} />
      </span>
      <span className="w-9 text-right tabular-nums">{pct.toFixed(0)}%</span>
    </span>
  );
}

function WearDetail({ equipment: e }: { equipment: Equipment }) {
  const [discount, setDiscount] = useState('5');
  const [weeks, setWeeks] = useState('2');
  const quote = useMutation({
    mutationFn: async () =>
      (await ok(
        api.POST('/api/v1/sales/predictive-wear/draft-quote', {
          body: { equipment_tag: e.equipment_tag, apply_volume_discount_pct: Number(discount), target_delivery_weeks: Number(weeks) } as never,
        }),
      )) as Quote,
    onError: (err) => toast.error('Couldn’t work out the quote', { description: err.message }),
  });
  const q = quote.data;
  const orderHref = q
    ? `/orders?${new URLSearchParams({ new: 'true', customer: q.customer_name, part: q.suggested_sku, price: String(q.quoted_unit_price) })}`
    : '';

  return (
    <div className="grid gap-4">
      <div className="grid gap-1.5">
        <div className="flex items-center gap-2">
          <WearBar equipment={e} />
          <ToneBadge tone={LEVEL[level(e)].tone}>{LEVEL[level(e)].label}</ToneBadge>
        </div>
        <p className="text-xs text-muted-foreground">
          {e.days_in_service} of {e.expected_lifespan_days} days of service life.{' '}
          {e.lifespan_basis_detail ?? ''}
        </p>
      </div>
      <Facts
        items={[
          ['Equipment', <span key="t" className="font-mono text-xs">{e.equipment_tag}</span>],
          ['Fitted part', <span key="i" className="font-mono text-xs">{e.installed_item_id}</span>],
          ['Fitted', day(e.install_date)],
          ['Replace with', <span key="s" className="font-mono text-xs">{e.suggested_replacement_sku}</span>],
          ['Last order', e.last_service_order || '–'],
          ['Last price', money(e.historical_unit_price)],
        ]}
      />

      <form
        className="grid gap-3 rounded-md border p-3"
        onSubmit={(ev) => {
          ev.preventDefault();
          quote.mutate();
        }}
      >
        <PanelHeading>Quote a replacement</PanelHeading>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Early reorder discount %">
            <Input type="number" min="0" max="50" step="0.5" value={discount} onChange={(ev) => setDiscount(ev.target.value)} />
          </Field>
          <Field label="Delivery (weeks)">
            <Input type="number" min="1" max="52" value={weeks} onChange={(ev) => setWeeks(ev.target.value)} />
          </Field>
        </div>
        <Button type="submit" size="sm" variant="outline" className="justify-self-start" disabled={quote.isPending}>
          {quote.isPending && <Loader2 className="animate-spin" />}
          Work out the quote
        </Button>
        {q && (
          <div className="grid gap-2 border-t pt-3">
            <Facts
              items={[
                ['Quote', <span key="n" className="font-mono text-xs">{q.quote_number}</span>],
                ['Regular price', money(q.regular_price)],
                ['Discount', `${q.discount_applied_pct}%`],
                ['Quoted price', <span key="p" className="font-semibold">{money(q.quoted_unit_price)}</span>],
                ['Delivery', `${q.target_lead_time_weeks} weeks`],
              ]}
            />
            {q.urgency_rationale && <p className="text-xs text-muted-foreground">{q.urgency_rationale}</p>}
            <p className="text-xs text-muted-foreground">Nothing has been saved or sent. Start an order to take it further.</p>
            <Link href={orderHref} className={cn(buttonVariants({ size: 'sm' }), 'justify-self-start')}>
              Start an order
            </Link>
          </div>
        )}
      </form>
    </div>
  );
}

function ScanDialog({ open, onOpenChange, count }: { open: boolean; onOpenChange: (open: boolean) => void; count: number }) {
  const scan = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/sales/predictive-wear/scan-and-alert', { params: { query: {} } }))) as Scan,
    onError: (e) => toast.error('The scan didn’t run', { description: e.message }),
  });
  const res = scan.data;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) scan.reset();
      }}
    >
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Quote every worn part</DialogTitle>
          <DialogDescription>
            Works out a replacement quote, with a 5% early reorder discount and 3-week delivery, for each of the {count} parts
            past 75% wear. Nothing is saved or sent to customers.
          </DialogDescription>
        </DialogHeader>
        {res && (
          <div className="grid gap-2">
            <p className="text-sm">
              {res.alerts_dispatched} quotes worked out; {res.healthy_skipped} parts are still in good shape.
            </p>
            <ul className="grid divide-y rounded-md border text-[13px]">
              {res.alerts.map((a) => (
                <li key={a.equipment_tag} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                  <span className="font-mono text-xs">{a.equipment_tag}</span>
                  <span className="text-muted-foreground">{a.customer_name}</span>
                  <ToneBadge tone={a.alert_level === 'CRITICAL_REPLACE' ? 'bad' : 'warn'}>{Math.round(a.wear_pct)}% worn</ToneBadge>
                  <span className="ml-auto tabular-nums">{money(a.quoted_price)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {res ? 'Done' : 'Cancel'}
          </Button>
          {!res && (
            <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
              {scan.isPending && <Loader2 className="animate-spin" />}
              Work out the quotes
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
