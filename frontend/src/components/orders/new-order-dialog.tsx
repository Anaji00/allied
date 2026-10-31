'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus, Trash2 } from 'lucide-react';
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
import { Textarea } from '@/components/ui/textarea';
import { api, ok } from '@/lib/api/client';
import { useHealth } from '@/lib/queries';

type Line = { text: string; qty: string; price: string };
type Preview = {
  customer_resolved: string;
  customer_confidence: number;
  matched_items: { raw_text: string; matched_sku: string; description: string; confidence: number; preferred_vendor: string }[];
  vendor_po_splits: Record<string, string[]>;
};

const EMPTY_LINE: Line = { text: '', qty: '1', price: '' };

/** Check first (a dry run that writes nothing), then create from the checked version. */
export type OrderPrefill = { customer?: string; part?: string; price?: string };

export function NewOrderDialog({
  open,
  onOpenChange,
  onCreated,
  initial,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (so: string) => void;
  /** Starting values, e.g. from a wear quote. Read once, when the dialog mounts. */
  initial?: OrderPrefill;
}) {
  const client = useQueryClient();
  const { data: health } = useHealth();
  const [customer, setCustomer] = useState(initial?.customer ?? '');
  const [po, setPo] = useState('');
  const [shipTo, setShipTo] = useState('');
  const [lines, setLines] = useState<Line[]>([
    initial?.part ? { text: initial.part, qty: '1', price: initial.price ?? '' } : { ...EMPTY_LINE },
  ]);
  const [preview, setPreview] = useState<{ key: string; data: Preview } | null>(null);

  const payload = () => ({
    customer_name: customer.trim(),
    customer_po_number: po.trim(),
    shipping_address: shipTo.trim(),
    line_items: lines.map((l) => ({ raw_item_text: l.text.trim(), quantity: l.qty, unit_price: l.price })),
  });
  const key = JSON.stringify(payload());
  const checked = preview?.key === key;

  const check = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/orders/dry-run', { body: payload() as never }))) as unknown as Preview,
    onSuccess: (data) => setPreview({ key, data }),
    onError: (e) => toast.error('Couldn’t check the order', { description: e.message }),
  });
  const create = useMutation({
    mutationFn: async () =>
      (await ok(api.POST('/api/v1/orders/process', { body: payload() as never }))) as { sales_order_number: string; messages?: string[] },
    onSuccess: (res) => {
      toast.success(`Created sales order ${res.sales_order_number}`, { description: res.messages?.[0] });
      void client.invalidateQueries({ queryKey: ['orders'] });
      reset();
      onOpenChange(false);
      onCreated(res.sales_order_number);
    },
    onError: (e) => toast.error('The order wasn’t created', { description: e.message }),
  });

  function reset() {
    setCustomer('');
    setPo('');
    setShipTo('');
    setLines([{ ...EMPTY_LINE }]);
    setPreview(null);
  }

  const setLine = (i: number, patch: Partial<Line>) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const ready = customer.trim() && po.trim() && shipTo.trim() && lines.every((l) => l.text.trim() && Number(l.qty) > 0 && l.price !== '');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New order</DialogTitle>
          <DialogDescription>
            Check it first: parts are matched to the catalogue and split by supplier, and nothing is written.{' '}
            {health?.simulation_mode
              ? 'Sage is in practice mode, so creating the order writes nothing to Sage.'
              : 'Creating it writes the sales order and supplier POs to Sage.'}
          </DialogDescription>
        </DialogHeader>

        <form
          id="new-order"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (checked) create.mutate();
            else check.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">Customer</span>
              <Input value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="e.g. Owens Corning Edmonton" required />
            </label>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">Customer PO</span>
              <Input value={po} onChange={(e) => setPo(e.target.value)} required />
            </label>
          </div>
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Ship to</span>
            <Textarea rows={2} value={shipTo} onChange={(e) => setShipTo(e.target.value)} required />
          </label>

          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">Lines</legend>
            {lines.map((line, i) => (
              <div key={i} className="grid grid-cols-[minmax(0,1fr)_4.5rem_6.5rem_auto] items-center gap-2">
                <Input
                  aria-label={`Line ${i + 1} part or description`}
                  placeholder="Part number or description"
                  value={line.text}
                  onChange={(e) => setLine(i, { text: e.target.value })}
                  required
                />
                <Input aria-label={`Line ${i + 1} quantity`} type="number" min="1" step="any" value={line.qty} onChange={(e) => setLine(i, { qty: e.target.value })} required />
                <Input aria-label={`Line ${i + 1} unit price`} type="number" min="0" step="0.01" placeholder="Price" value={line.price} onChange={(e) => setLine(i, { price: e.target.value })} required />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove line ${i + 1}`}
                  disabled={lines.length === 1}
                  onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))}
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
            <Button type="button" variant="ghost" size="sm" className="justify-self-start" onClick={() => setLines((ls) => [...ls, { ...EMPTY_LINE }])}>
              <Plus /> Add line
            </Button>
          </fieldset>
        </form>

        {preview && (
          <div className="grid gap-2 rounded-md border bg-muted/40 p-3 text-[13px]">
            <p>
              Customer matched to <span className="font-medium">{preview.data.customer_resolved}</span>{' '}
              <Confidence value={preview.data.customer_confidence} />
            </p>
            <ul className="grid gap-1">
              {preview.data.matched_items.map((m, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-muted-foreground">{m.raw_text}</span>→
                  <span className="font-mono text-xs">{m.matched_sku}</span>
                  <span className="text-xs text-muted-foreground">{m.preferred_vendor}</span>
                  <Confidence value={m.confidence} />
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground">
              Supplier POs:{' '}
              {Object.keys(preview.data.vendor_po_splits).filter((v) => v !== 'STOCK').join(', ') || 'none, all from stock'}
            </p>
            {!checked && <p className="text-warn">The order changed since this check. Check it again before creating it.</p>}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          {checked ? (
            <Button type="submit" form="new-order" disabled={create.isPending}>
              {create.isPending && <Loader2 className="animate-spin" />}
              Create order
            </Button>
          ) : (
            <Button type="submit" form="new-order" disabled={!ready || check.isPending}>
              {check.isPending && <Loader2 className="animate-spin" />}
              Check order
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Confidence({ value }: { value: number }) {
  const n = Math.round(value <= 1 ? value * 100 : value);
  return <ToneBadge tone={n >= 90 ? 'ok' : n >= 70 ? 'warn' : 'bad'}>{n}% match</ToneBadge>;
}
