'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { money } from '@/lib/format';

type Pipe = {
  pipeline?: string | null;
  tracking_no?: string | null;
  carrier?: string | null;
  quote_options?: unknown;
  freight_actual?: string | number | null;
  delivered_at?: string | null;
};
type Parsed = {
  quote_options?: { carrier?: string; price?: string | number; days?: number | string; note?: string }[];
  bol_numbers?: string[];
  events?: { date?: string; what?: string }[];
  ambiguous_dates?: string[];
  check?: string | null;
};
type Delivered = {
  variance?: { flag?: boolean; percent?: number | string; amount?: number | string; note?: string };
  payments_now_due?: unknown[];
};

const PIPES = [
  ['US_OFFICE_PARCEL', 'A parcel from the US office (UPS)'],
  ['US_MOUNTAIN_MOVERS', 'Mountain Movers truck'],
  ['CN_FREIGHT_FORWARDER', 'China freight forwarder'],
] as const;

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T | null> {
  const res = await authFetch(`/api/v1/pipelines${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  if (res.status === 404) return null;
  const out = (await res.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (!res.ok)
    throw new Error(
      typeof (out as { detail?: string } | null)?.detail === 'string'
        ? (out as { detail: string }).detail
        : `That didn’t work (${res.status}).`,
    );
  return out;
}

/**
 * How this shipment travels: a parcel with a UPS number, a Mountain Movers truck with quote options and a bill of lading, or the China
 * forwarder. Delivery confirmed (managers) adds the freight to the sales order and prepares the invoice.
 */
export function PipelineCard({ shipmentId }: { shipmentId: number }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ['pipeline', shipmentId],
    queryFn: () => call<Pipe>(`/shipment/${shipmentId}`),
    enabled: hasRole('OPERATOR'),
    retry: false,
  });
  const [form, setForm] = useState<{ pipeline: string; tracking_no: string; carrier: string } | null>(null);
  const [paste, setPaste] = useState('');
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [delivering, setDelivering] = useState(false);
  const [done, setDone] = useState<Delivered | null>(null);
  const [d, setD] = useState({ sales_order_number: '', freight_actual: '', freight_quoted: '' });

  const save = useMutation({
    mutationFn: async () =>
      call(`/shipment/${shipmentId}`, 'PUT', {
        pipeline: v.pipeline || undefined,
        tracking_no: v.tracking_no.trim() || undefined,
        carrier: v.carrier.trim() || undefined,
      }),
    onSuccess: () => {
      toast.success('Saved');
      setForm(null);
      void client.invalidateQueries({ queryKey: ['pipeline', shipmentId] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  const parse = useMutation({
    mutationFn: () => call<Parsed>('/parse/mountain-movers', 'POST', { text: paste }),
    onSuccess: (o) => setParsed(o),
    onError: (e) => toast.error('It could not be read', { description: e.message }),
  });
  const deliver = useMutation({
    mutationFn: () =>
      call<Delivered>(`/shipment/${shipmentId}/delivered`, 'POST', {
        sales_order_number: d.sales_order_number.trim(),
        freight_actual: d.freight_actual.trim(),
        freight_quoted: d.freight_quoted.trim() || undefined,
        po_ids: [],
      }),
    onSuccess: (o) => {
      setDone(o);
      toast.success('Delivery recorded.');
      void client.invalidateQueries({ queryKey: ['pipeline', shipmentId] });
    },
    onError: (e) => toast.error('Delivery was not recorded', { description: e.message }),
  });

  if (!hasRole('OPERATOR') || !q.data) return null;
  const cur = q.data;
  const v = form ?? { pipeline: cur.pipeline ?? '', tracking_no: cur.tracking_no ?? '', carrier: cur.carrier ?? '' };
  const set = (patch: Partial<typeof v>) => setForm({ ...v, ...patch });

  return (
    <section className="grid gap-3 rounded-md border p-3 text-[13px]" aria-label="How it travels">
      <h3 className="text-sm font-semibold">How it travels</h3>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Route">
          <NativeSelect value={v.pipeline} onChange={(e) => set({ pipeline: e.target.value })} className="w-64">
            <option value="">Choose…</option>
            {PIPES.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </NativeSelect>
        </Field>
        {v.pipeline === 'US_OFFICE_PARCEL' && (
          <Field label="UPS tracking number">
            <Input value={v.tracking_no} onChange={(e) => set({ tracking_no: e.target.value })} placeholder="1Z…" className="w-56" />
          </Field>
        )}
        <Field label="Carrier">
          <Input value={v.carrier} onChange={(e) => set({ carrier: e.target.value })} className="w-40" />
        </Field>
        <Button size="sm" disabled={!form || save.isPending} onClick={() => save.mutate()}>
          {save.isPending && <Loader2 className="animate-spin" />} Save
        </Button>
      </div>

      {v.pipeline === 'US_MOUNTAIN_MOVERS' && (
        <div className="grid gap-2">
          <Field label="Paste the Mountain Movers email or quote">
            <Textarea rows={4} value={paste} onChange={(e) => setPaste(e.target.value)} />
          </Field>
          <Button
            size="sm"
            variant="outline"
            className="justify-self-start"
            disabled={!paste.trim() || parse.isPending}
            onClick={() => parse.mutate()}
          >
            Read it
          </Button>
          {parsed && (
            <div className="grid gap-1 rounded-md bg-muted/40 p-2 text-xs">
              {(parsed.quote_options ?? []).map((o, i) => (
                <p key={i}>
                  Option {i + 1}: {o.carrier ?? 'truck'} {o.price != null ? money(o.price) : ''} {o.days ? `, ${o.days} days` : ''}{' '}
                  {o.note ?? ''}
                </p>
              ))}
              {(parsed.bol_numbers ?? []).length > 0 && <p>Bill of lading: {parsed.bol_numbers!.join(', ')}</p>}
              {(parsed.ambiguous_dates ?? []).length > 0 && (
                <p className="text-warn">These dates could be read two ways: {parsed.ambiguous_dates!.join(', ')}. Check them.</p>
              )}
              {parsed.check && <p className="text-warn">Check against the original: {parsed.check}</p>}
            </div>
          )}
        </div>
      )}

      {hasRole('MANAGER') && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setDelivering(true)}>
            Delivery confirmed…
          </Button>
          <span className="text-xs text-muted-foreground">Adds the freight to the sales order and prepares the invoice.</span>
        </div>
      )}
      {done?.variance && (
        <p className={done.variance.flag ? 'rounded-md bg-warn-soft px-3 py-2 text-warn' : 'text-muted-foreground'}>
          {done.variance.note ?? (done.variance.flag ? 'The freight cost differs from the quote.' : 'The freight matches the quote.')}
        </p>
      )}

      {delivering && (
        <Dialog open onOpenChange={(o) => !o && setDelivering(false)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Delivery confirmed</DialogTitle>
              <DialogDescription>
                Say what the freight actually cost. It is added to the sales order and the invoice is prepared. Nothing is sent to the
                customer.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-3">
              <Field label="Sales order number">
                <Input
                  value={d.sales_order_number}
                  onChange={(e) => setD({ ...d, sales_order_number: e.target.value })}
                  placeholder="SO-12889"
                />
              </Field>
              <Field label="Freight actually paid">
                <Input value={d.freight_actual} onChange={(e) => setD({ ...d, freight_actual: e.target.value })} inputMode="decimal" />
              </Field>
              <Field label="Freight that was quoted (optional)">
                <Input value={d.freight_quoted} onChange={(e) => setD({ ...d, freight_quoted: e.target.value })} inputMode="decimal" />
              </Field>
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setDelivering(false)}>
                Cancel
              </Button>
              <Button
                disabled={!d.sales_order_number.trim() || !d.freight_actual.trim() || deliver.isPending}
                onClick={() => deliver.mutate(undefined, { onSuccess: () => setDelivering(false) })}
              >
                {deliver.isPending && <Loader2 className="animate-spin" />}
                Confirm delivery
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </section>
  );
}
