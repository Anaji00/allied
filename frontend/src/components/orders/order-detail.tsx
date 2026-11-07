'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, Loader2, Truck } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
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
import { useAuth } from '@/context/AuthContext';
import { authFetch, dispatchOrder, downloadPackingSlip } from '@/lib/api';
import { api, ok } from '@/lib/api/client';
import { date, dateTime, money, pct } from '@/lib/format';
import { cn } from '@/lib/utils';
import { ARCHETYPE, STATUS, useCompliance, useOrder, type Order } from './use-orders';

const TABS = [
  ['lines', 'Lines'],
  ['quality', 'Mill certs'],
  ['documents', 'Documents'],
] as const;
type Tab = (typeof TABS)[number][0];

export function OrderDetail({ so }: { so: string }) {
  const { data: order, isPending, error, refetch } = useOrder(so);
  const [tab, setTab] = useState<Tab>('lines');

  if (isPending) return <RowsSkeleton rows={5} />;
  if (error || !order) return <ErrorState error={error} onRetry={() => void refetch()} />;

  const floor = order.min_margin_pct ?? 15;
  const thin = Number(order.total_revenue) > 0 && Number(order.gross_margin_pct) < floor;
  const status = STATUS[order.status] ?? { label: order.status, tone: 'neutral' as const };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <ToneBadge tone={status.tone}>{status.label}</ToneBadge>
        {thin && <ToneBadge tone="bad">Margin under {floor}%</ToneBadge>}
        <ToneBadge tone={order.sage_exported_at ? 'ok' : 'neutral'}>
          {order.sage_exported_at ? `In Sage file ${date(order.sage_exported_at)}` : 'Not in a Sage file yet'}
        </ToneBadge>
      </div>

      <div className="grid grid-cols-3 gap-2 rounded-md border p-3 text-center">
        <Figure label="Sale" value={money(order.total_revenue, order.currency)} />
        <Figure label="Cost" value={money(order.total_expense, order.currency)} />
        <Figure label="Margin" value={pct(order.gross_margin_pct)} warn={thin} />
      </div>

      <Facts
        items={[
          ['Customer', order.customer_name],
          ['Customer PO', <span key="po" className="font-mono text-xs">{order.customer_po_number}</span>],
          ['Type', ARCHETYPE[order.archetype ?? ''] ?? order.archetype ?? '–'],
          ['Facility', order.facility],
          ['Terms', order.payment_terms ?? '–'],
          ['Received', dateTime(order.created_at)],
          ...(order.created_by ? ([['By', order.created_by]] as [string, string][]) : []),
          ...(order.monday_item_id ? ([['monday item', order.monday_item_id]] as [string, string][]) : []),
        ]}
      />

      <div role="tablist" aria-label="Order sections" className="flex gap-1 border-b">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            type="button"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              '-mb-px border-b-2 px-3 py-1.5 text-[13px]',
              tab === key ? 'border-foreground font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'lines' && <Lines order={order} />}
      {tab === 'quality' && <Quality order={order} />}
      {tab === 'documents' && <Documents order={order} />}
    </div>
  );
}

function Figure({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="grid min-w-0 gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      {/* A long foreign-currency total wraps rather than running into the next figure. */}
      <span className={cn('text-sm font-semibold tabular-nums [overflow-wrap:anywhere]', warn && 'text-bad')}>{value}</span>
    </div>
  );
}

function Lines({ order }: { order: Order }) {
  const pos = Object.entries(order.purchase_orders ?? {});
  return (
    <div className="grid gap-4">
      <ul className="grid gap-2">
        {order.line_items.map((line, i) => {
          const qty = Number(line.quantity ?? 0);
          return (
            <li key={i} className="grid gap-0.5 rounded-md border p-2.5 text-[13px]">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-xs">{line.item_id || '—'}</span>
                {line.preferred_vendor && <span className="ml-auto text-xs text-muted-foreground">{line.preferred_vendor}</span>}
              </div>
              <span className="text-pretty">{line.description || line.raw_item_text}</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                {qty} {line.uom || 'EA'} × {money(line.unit_price, order.currency)} = {money(qty * Number(line.unit_price ?? 0), order.currency)}
                {line.unit_cost ? ` · cost ${money(line.unit_cost, order.currency)}` : ''}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="grid gap-1.5">
        <h3 className="text-xs font-medium text-muted-foreground">Supplier POs</h3>
        {pos.length ? (
          <Facts items={pos.map(([vendor, po]) => [vendor, <span key={vendor} className="font-mono text-xs">{po}</span>])} />
        ) : (
          <p className="text-sm text-muted-foreground">From stock, no supplier PO.</p>
        )}
      </div>
      {order.shipping_address && (
        <div className="grid gap-1">
          <h3 className="text-xs font-medium text-muted-foreground">Ship to</h3>
          <p className="text-sm whitespace-pre-line">{order.shipping_address}</p>
        </div>
      )}
    </div>
  );
}

type CofC = { cofc_id?: string; issued_date?: string; heat_numbers?: string[]; conformance_statement?: string };

function Quality({ order }: { order: Order }) {
  const { data: bundle, isPending } = useCompliance(order.sales_order_number);
  const heats = bundle?.heat_numbers ?? [];
  const cofc = useMutation({
    mutationFn: async () =>
      (await ok(
        api.GET('/api/v1/compliance/cofc/{sales_order_number}', {
          params: {
            path: { sales_order_number: order.sales_order_number },
            // Always the order's own details: the route falls back to demo values otherwise.
            query: {
              customer_id: order.customer_id,
              customer_name: order.customer_name,
              customer_po_number: order.customer_po_number,
              heat_numbers: heats.join(','),
            },
          },
        }),
      )) as CofC,
    onError: (e) => toast.error('Couldn’t issue the certificate', { description: e.message }),
  });

  if (isPending) return <RowsSkeleton rows={2} />;
  if (!bundle)
    return (
      <p className="text-sm text-muted-foreground">
        No mill-cert record is held for this order. Records are kept in memory and clear when the service restarts.
      </p>
    );

  return (
    <div className="grid gap-3">
      <Facts
        items={[
          ['Heats', heats.length ? <span key="h" className="font-mono text-xs">{heats.join(', ')}</span> : 'None recorded'],
          ['Blind ship', bundle.blind_shipping_enforced ? 'Yes, no supplier marks' : 'No'],
          ...(bundle.drawing_reference && bundle.drawing_reference !== 'N/A'
            ? ([['Drawing', bundle.drawing_reference]] as [string, string][])
            : []),
        ]}
      />
      {heats.length > 0 &&
        (cofc.data ? (
          <div className="grid gap-1.5 rounded-md border p-3 text-sm">
            <p className="font-medium">
              Certificate {cofc.data.cofc_id} · {cofc.data.issued_date}
            </p>
            <p className="text-muted-foreground">{cofc.data.conformance_statement}</p>
          </div>
        ) : (
          <Button variant="outline" size="sm" className="justify-self-start" onClick={() => cofc.mutate()} disabled={cofc.isPending}>
            {cofc.isPending && <Loader2 className="animate-spin" />}
            Issue certificate of conformance
          </Button>
        ))}
    </div>
  );
}

type Rate = { carrier: string; service: string; live?: boolean; transit_days?: number | null; usd?: number | null; cad?: number | null; cny?: number | null };

/** Prices from every carrier that is set up, then the saved rate card. Looks up prices only; nothing is booked. */
function ShippingPrices({ address, currency }: { address?: string | null; currency?: string }) {
  const [weight, setWeight] = useState('');
  const quote = useMutation({
    mutationFn: async () => {
      const qs = new URLSearchParams({ address: address || '', weight_lbs: weight });
      const res = await authFetch(`/api/v1/carriers/quote?${qs}`, { cache: 'no-store' });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `Couldn’t get prices (${res.status}).`);
      return body as Rate[];
    },
  });
  const cur = (currency || 'USD').toUpperCase();
  const price = (r: Rate) => {
    const v = cur === 'CAD' ? r.cad : cur === 'CNY' ? r.cny : r.usd;
    return v == null ? '–' : `${money(v)} ${cur === 'CAD' || cur === 'CNY' ? cur : 'USD'}`;
  };

  return (
    <div className="grid gap-2 rounded-lg border p-3">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (Number(weight) > 0) quote.mutate();
        }}
      >
        <label className="grid gap-1 text-xs">
          <span className="font-medium">Shipping prices</span>
          <Input className="w-32" inputMode="decimal" placeholder="Weight (lbs)" value={weight} onChange={(e) => setWeight(e.target.value.replace(/[^\d.]/g, ''))} />
        </label>
        <Button type="submit" variant="outline" size="sm" disabled={!address || !(Number(weight) > 0) || quote.isPending}>
          {quote.isPending && <Loader2 className="animate-spin" />}
          Get prices
        </Button>
        {!address && <span className="text-xs text-muted-foreground">This order has no shipping address.</span>}
      </form>
      {quote.isError && <p className="text-xs text-bad">{quote.error.message}</p>}
      {quote.data && (
        <ul className="grid gap-1 text-[13px]">
          {quote.data.map((r, i) => (
            <li key={`${r.carrier}-${r.service}-${i}`} className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium">{r.carrier}</span>
              <span className="text-xs text-muted-foreground">{r.service}</span>
              {r.live && <ToneBadge tone="ok">Live</ToneBadge>}
              {r.transit_days != null && <span className="text-xs text-muted-foreground">{r.transit_days} days</span>}
              <span className="ml-auto tabular-nums">{price(r)}</span>
            </li>
          ))}
          {quote.data.length === 0 && <li className="text-muted-foreground">No prices came back.</li>}
          <li className="text-xs text-muted-foreground">Prices only. Nothing is booked or bought here.</li>
        </ul>
      )}
    </div>
  );
}

function Documents({ order }: { order: Order }) {
  const { hasRole } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [carrier, setCarrier] = useState('TForce Freight / Motor Freight');
  const [tracking, setTracking] = useState('');
  const client = useQueryClient();

  const download = useMutation({
    mutationFn: () => downloadPackingSlip(order.sales_order_number),
    onError: (e) => toast.error('Couldn’t download the packing slip', { description: e.message }),
  });
  const dispatch = useMutation({
    mutationFn: () =>
      dispatchOrder(order.sales_order_number, {
        carrier_name: carrier,
        tracking_number: tracking || undefined,
        origin_facility: order.facility,
        currency: order.currency,
      }),
    onSuccess: () => {
      setConfirming(false);
      toast.success(`Dispatched ${order.sales_order_number}`);
      void client.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e) => toast.error('Dispatch didn’t go through', { description: e.message }),
  });

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => download.mutate()}
          disabled={!order.has_packing_slip || download.isPending}
        >
          {download.isPending ? <Loader2 className="animate-spin" /> : <Download />}
          Packing slip (PDF)
        </Button>
        {hasRole('OPERATOR') && (
          <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
            <Truck /> Dispatch…
          </Button>
        )}
      </div>
      {!order.has_packing_slip && (
        <p className="text-xs text-muted-foreground">No packing slip has been made for this order yet. Dispatching makes one.</p>
      )}
      {hasRole('OPERATOR') && <ShippingPrices address={order.shipping_address} currency={order.currency} />}

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Dispatch {order.sales_order_number}?</DialogTitle>
            <DialogDescription>
              This makes the packing list (and customs papers if cross-border), books the carrier and writes the
              customer’s shipping notice. It cannot be undone from here.
            </DialogDescription>
          </DialogHeader>
          <form
            id="dispatch-form"
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              dispatch.mutate();
            }}
          >
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">Carrier</span>
              <Input value={carrier} onChange={(e) => setCarrier(e.target.value)} required />
            </label>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">Tracking or PRO number</span>
              <Input value={tracking} onChange={(e) => setTracking(e.target.value)} placeholder="Optional" />
            </label>
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button type="submit" form="dispatch-form" disabled={dispatch.isPending}>
              {dispatch.isPending && <Loader2 className="animate-spin" />}
              Dispatch order
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
