'use client';

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';
import { dateTime, money } from '@/lib/format';

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await authFetch(`/api/v1${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  const out = (await res.json().catch(() => null)) as (T & { detail?: unknown }) | null;
  if (res.status === 404) throw new Error('This isn’t switched on yet. Tell your administrator.');
  if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t work (${res.status}).`);
  return out as T;
}

/** One draft email to the vendor about a payment step. It is a draft: a person checks it, adds the wire copy and the proforma, and sends it. */
export function PaymentCopyButton({ trancheId }: { trancheId: number }) {
  const make = useMutation({
    mutationFn: () => call<{ vendor?: string; to_add_by_hand?: string[] }>(`/payments/tranche/${trancheId}/email-draft`, 'POST', {}),
    onSuccess: (r) => {
      const add = (r.to_add_by_hand ?? []).join(' and ');
      toast.success(`Draft email to ${r.vendor ?? 'the vendor'} is ready`, {
        description: `${add ? `Add the ${add} by hand. ` : ''}It is in Email, waiting for your OK. Nothing was sent.`,
      });
    },
    onError: (e) => toast.error('The draft was not made', { description: e.message }),
  });
  return (
    <Button size="xs" variant="outline" disabled={make.isPending} onClick={() => make.mutate()}>
      {make.isPending && <Loader2 className="animate-spin" />} Draft the email to the vendor
    </Button>
  );
}

/** A freight price request for the forwarder, filled from the vendor's quote. It never names the customer. A draft, not sent. */
export function FreightRfqButton({ poId }: { poId: number }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ forwarder_email: '', text: '', origin: '', destination: '' });
  const make = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { forwarder_email: f.forwarder_email.trim(), po_id: poId };
      if (f.text.trim()) body.text = f.text;
      if (f.origin.trim()) body.origin = f.origin.trim();
      if (f.destination.trim()) body.destination = f.destination.trim();
      return call<{ missing?: string[] }>('/shipments/freight-rfq-draft', 'POST', body);
    },
    onSuccess: (r) => {
      const miss = r.missing ?? [];
      toast.success('Draft to the forwarder is ready', {
        description: miss.length
          ? `Not found, left as “(to be filled in)”: ${miss.join(', ')}. It is in Email, waiting for your OK.`
          : 'It is in Email, waiting for your OK. Nothing was sent.',
      });
      setOpen(false);
    },
    onError: (e) => toast.error('The draft was not made', { description: e.message }),
  });
  return (
    <>
      <Button size="xs" variant="outline" onClick={() => setOpen(true)}>
        Freight price request
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Ask the forwarder for a freight price</DialogTitle>
            <DialogDescription>
              Paste the weights and sizes from the vendor’s quote. The draft has no prices, no vendor and no customer name.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <Field label="Forwarder’s email">
              <Input type="email" value={f.forwarder_email} onChange={(e) => setF({ ...f, forwarder_email: e.target.value })} />
            </Field>
            <Field label="From the vendor’s quote (weights, sizes, packages)">
              <Textarea rows={5} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Picked up from">
                <Input value={f.origin} onChange={(e) => setF({ ...f, origin: e.target.value })} placeholder="City, country" />
              </Field>
              <Field label="Delivered to">
                <Input value={f.destination} onChange={(e) => setF({ ...f, destination: e.target.value })} placeholder="City, state" />
              </Field>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button disabled={!f.forwarder_email.trim() || make.isPending} onClick={() => make.mutate()}>
              {make.isPending && <Loader2 className="animate-spin" />} Make the draft
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

type Finding = {
  kind: string;
  item?: string;
  billed?: string | number | null;
  po?: string | number | null;
  variance?: string | number | null;
  text?: string;
};
type DetailLine = {
  item?: string | null;
  description?: string | null;
  bill: { quantity?: string | number | null; unit_cost?: string | number | null; amount?: string | number | null };
  po: { line: number; quantity?: string | number | null; unit_cost?: string | number | null; amount?: string | number | null } | null;
  matched: boolean;
  variance: { quantity?: string | number | null; unit_cost?: string | number | null; amount?: string | number | null } | null;
};
type Detail = { lines: DetailLine[]; po_lines_not_billed?: number[]; freight?: string | number | null; currency?: string | null };
type Bill = {
  reconciliation_id: number;
  detail?: Detail | null;
  invoice_number?: string;
  po_total?: string | number;
  billed_total?: string | number;
  variance?: string | number;
  findings?: Finding[];
  suggested_action?: string;
  checked_at?: string;
};
type Match = { bills: Bill[]; note?: string };

const KIND: Record<string, string> = {
  quantity: 'Quantity differs',
  unit_price: 'Unit price differs',
  line_total: 'Line total differs',
  freight: 'Freight charge',
  unexpected_item: 'Item not on the order',
  other: 'Other',
};

/** The purchase order next to each supplier bill that was checked against it: totals, and each difference the check found. */
export function BillMatch({ poId }: { poId: number }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ['bill-match', poId],
    enabled: open,
    retry: false,
    queryFn: () => call<Match>(`/purchase-orders/${poId}/bill-match`),
  });
  return (
    <div className="grid gap-2">
      <Button size="xs" variant="ghost" className="justify-self-start" onClick={() => setOpen((o) => !o)}>
        {open ? 'Hide the bills' : 'Supplier bills for this order'}
      </Button>
      {open && q.isPending && <p className="text-xs text-muted-foreground">Looking…</p>}
      {open && q.error && <p className="text-xs text-bad">{(q.error as Error).message}</p>}
      {open && q.data && !q.data.bills?.length && (
        <p className="text-xs text-muted-foreground">{q.data.note ?? 'No supplier bill has been checked against this order yet.'}</p>
      )}
      {open &&
        q.data?.bills?.map((b) => (
          <div key={b.reconciliation_id} className="grid gap-1.5 rounded-md border p-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-x-3">
              <span className="font-medium">Bill {b.invoice_number ?? ''}</span>
              <span className="text-xs text-muted-foreground">{b.checked_at ? `checked ${dateTime(b.checked_at)}` : ''}</span>
              <span className="ml-auto tabular-nums">
                Order {money(b.po_total ?? 0)} · Bill {money(b.billed_total ?? 0)}
                {Number(b.variance) ? ` · Difference ${money(b.variance ?? 0)}` : ' · Matches'}
              </span>
            </div>
            {b.detail && b.detail.lines.length > 0 && (
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-xs">
                  <thead className="text-left text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1 font-medium">Item</th>
                      <th className="px-2 py-1 text-right font-medium">Order qty</th>
                      <th className="px-2 py-1 text-right font-medium">Bill qty</th>
                      <th className="px-2 py-1 text-right font-medium">Order price</th>
                      <th className="px-2 py-1 text-right font-medium">Bill price</th>
                      <th className="px-2 py-1 text-right font-medium">Difference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.detail.lines.map((l, i) => {
                      const cur = b.detail?.currency || undefined;
                      const off = !l.matched || Number(l.variance?.amount ?? 0) !== 0;
                      return (
                        <tr key={i} className={`border-t ${off ? 'bg-warn-soft' : ''}`}>
                          <td className="px-2 py-1">
                            {l.item || l.description || 'Line'}
                            {!l.po && <span className="ml-1.5 text-warn">not on the order</span>}
                          </td>
                          <td className="px-2 py-1 text-right tabular-nums">{l.po?.quantity ?? '–'}</td>
                          <td className="px-2 py-1 text-right tabular-nums">{l.bill.quantity ?? '–'}</td>
                          <td className="px-2 py-1 text-right tabular-nums">
                            {l.po ? (cur ? money(l.po.unit_cost, cur) : String(l.po.unit_cost ?? '–')) : '–'}
                          </td>
                          <td className="px-2 py-1 text-right tabular-nums">
                            {cur ? money(l.bill.unit_cost, cur) : String(l.bill.unit_cost ?? '–')}
                          </td>
                          <td className="px-2 py-1 text-right tabular-nums">
                            {l.variance && Number(l.variance.amount ?? 0) !== 0
                              ? cur
                                ? money(l.variance.amount, cur)
                                : String(l.variance.amount)
                              : l.po
                                ? 'Matches'
                                : ''}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {(b.detail.po_lines_not_billed ?? []).length > 0 && (
                  <p className="border-t px-2 py-1 text-xs text-warn">
                    Order lines not on this bill: {(b.detail.po_lines_not_billed ?? []).join(', ')}.
                  </p>
                )}
              </div>
            )}
            {!b.detail && (
              <p className="text-xs text-muted-foreground">
                This bill was checked before its lines were kept, so only the totals and differences show.
              </p>
            )}
            {(b.findings ?? []).length > 0 && (
              <ul className="grid divide-y rounded-md border text-xs">
                {b.findings!.map((x, i) => (
                  <li key={i} className="flex flex-wrap gap-x-3 px-2 py-1">
                    <span className="font-medium">{KIND[x.kind] ?? 'Difference'}</span>
                    <span>{x.item}</span>
                    {x.po != null && x.billed != null && (
                      <span className="ml-auto tabular-nums">
                        order {String(x.po)} · bill {String(x.billed)}
                      </span>
                    )}
                    {!x.item && x.text && <span className="text-muted-foreground">{x.text}</span>}
                  </li>
                ))}
              </ul>
            )}
            {b.suggested_action && <p className="text-xs text-muted-foreground">{b.suggested_action}</p>}
          </div>
        ))}
    </div>
  );
}
