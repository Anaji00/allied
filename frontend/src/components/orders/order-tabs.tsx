'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, RefreshCw } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { DetailPanel } from '@/components/data/detail-panel';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { fetchInboxFeed, fetchReconciliationHistory, matchVendorInvoice, pollMailboxNow, simulateInboundEmail } from '@/lib/api';
import { money } from '@/lib/format';
import { useHealth } from '@/lib/queries';
import { useSuppliers } from './use-orders';

/* ------------------------------------------------------------------ emails */

type FeedRow = {
  email_id?: string;
  customer_name: string;
  customer_po_number?: string;
  sales_order_number?: string;
  purchase_orders?: Record<string, string> | string[];
  line_items_count?: number;
  status?: string;
  customer_acknowledgment_email?: string;
};

const poText = (pos: FeedRow['purchase_orders']) =>
  Array.isArray(pos) ? pos.join(', ') : pos ? Object.entries(pos).map(([v, p]) => `${v} ${p}`).join(', ') : '';

const feedCol = columnHelper<FeedRow>();
const feedColumns = feedCol.columns([
  feedCol.accessor('customer_name', { header: 'Customer' }),
  feedCol.accessor('customer_po_number', { header: 'Customer PO', cell: (c) => <span className="font-mono text-xs">{c.getValue() || '–'}</span> }),
  feedCol.accessor('sales_order_number', { header: 'Sales order', cell: (c) => <span className="font-mono text-xs">{c.getValue() || '–'}</span> }),
  feedCol.accessor('purchase_orders', { header: 'Supplier POs', cell: (c) => poText(c.getValue()) || <span className="text-muted-foreground">From stock</span> }),
  feedCol.accessor('line_items_count', { header: 'Lines', meta: { align: 'right' } }),
  feedCol.accessor('status', {
    header: 'Result',
    cell: (c) =>
      c.getValue() === 'PROCESSED_ORDER_CREATED' ? <ToneBadge tone="ok">Order created</ToneBadge> : <ToneBadge>{c.getValue() || '–'}</ToneBadge>,
  }),
]);

export function OrderEmails() {
  const { hasRole } = useAuth();
  const { data: health } = useHealth();
  const client = useQueryClient();
  const feed = useQuery({ queryKey: ['orders', 'emails'], queryFn: () => fetchInboxFeed(50) as Promise<FeedRow[]>, refetchInterval: 60_000 });
  const [open, setOpen] = useState<FeedRow | null>(null);

  const poll = useMutation({
    mutationFn: () => pollMailboxNow(),
    onSuccess: () => {
      toast.success('Mailbox checked');
      void client.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e) => toast.error('Couldn’t check the mailbox', { description: e.message }),
  });

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">Customer POs that arrive by email become a sales order and supplier POs automatically.</p>
        {hasRole('OPERATOR') && (
          <Button variant="outline" size="sm" className="ml-auto" onClick={() => poll.mutate()} disabled={poll.isPending}>
            {poll.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Check mailbox now
          </Button>
        )}
      </div>
      {feed.isPending ? (
        <RowsSkeleton />
      ) : feed.isError ? (
        <ErrorState error={feed.error} onRetry={() => void feed.refetch()} />
      ) : (
        <DataTable
          label="Order emails"
          columns={feedColumns}
          data={feed.data ?? []}
          getRowId={(r) => r.email_id || `${r.customer_po_number}-${r.sales_order_number}`}
          onSelect={(r) => r.customer_acknowledgment_email && setOpen(r)}
          empty={<EmptyState title="No order emails yet">Emails from customers with a PO attached show up here once read.</EmptyState>}
        />
      )}
      <DetailPanel open={Boolean(open)} onClose={() => setOpen(null)} title={`Reply to ${open?.customer_name ?? ''}`} description="The order acknowledgment the system wrote">
        <pre className="font-sans text-sm whitespace-pre-wrap">{open?.customer_acknowledgment_email}</pre>
      </DetailPanel>
      {health?.simulation_mode && hasRole('OPERATOR') && <SampleEmail />}
    </div>
  );
}

/** Practice mode only: the sample runs the real pipeline, against simulated Sage. */
function SampleEmail() {
  const client = useQueryClient();
  const [form, setForm] = useState({
    sender_email: 'purchasing@owenscorning.com',
    sender_name: 'Mark Richardson (Owens Corning)',
    subject: 'PO-9925: Clad Wear Plates for Chute #4',
    body_text: 'Please stage 4 units of PLATE-300XL-38 for immediate delivery to Edmonton Plant. PO Ref: OC-9925.',
  });
  const run = useMutation({
    mutationFn: () => simulateInboundEmail(form),
    onSuccess: (res: { sales_order_number?: string }) => {
      toast.success(`Order created: ${res.sales_order_number}`);
      void client.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e) => toast.error('The sample email failed', { description: e.message }),
  });
  const field = (k: keyof typeof form) => ({ value: form[k], onChange: (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value }) });

  return (
    <details className="rounded-lg border bg-card p-4">
      <summary className="cursor-pointer text-sm font-medium">Try a sample order email (practice mode)</summary>
      <form
        className="mt-3 grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          run.mutate();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">From (email)</span>
            <Input type="email" required {...field('sender_email')} />
          </label>
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">From (name)</span>
            <Input required {...field('sender_name')} />
          </label>
        </div>
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium">Subject</span>
          <Input required {...field('subject')} />
        </label>
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium">Message</span>
          <Textarea rows={3} required {...field('body_text')} />
        </label>
        <Button type="submit" size="sm" className="justify-self-start" disabled={run.isPending}>
          {run.isPending && <Loader2 className="animate-spin" />}
          Process sample email
        </Button>
      </form>
    </details>
  );
}

/* ------------------------------------------------------------------- bills */

type Bill = {
  reconciliation_id?: string;
  invoice_number: string;
  vendor_name: string;
  po_number: string;
  authorized_po_total?: number | string;
  actual_billed_total?: number | string;
  cost_variance_amount?: number | string;
  reconciliation_status: string;
  dispute_email_draft?: string | null;
};

const BILL_RESULT: Record<string, { label: string; tone: 'ok' | 'bad' | 'warn' }> = {
  RECONCILED_APPROVED: { label: 'Cleared to pay', tone: 'ok' },
  DISCREPANCY_PRICE: { label: 'Price differs', tone: 'bad' },
  DISCREPANCY_QTY: { label: 'Quantity differs', tone: 'bad' },
  DISCREPANCY_FREIGHT: { label: 'Freight differs', tone: 'warn' },
  UNMATCHED_NO_PO: { label: 'No matching PO', tone: 'bad' },
};

const billCol = columnHelper<Bill>();
const billColumns = billCol.columns([
  billCol.accessor('invoice_number', {
    header: 'Bill',
    cell: (c) => (
      <span className="grid">
        <span className="font-mono text-xs">{c.getValue()}</span>
        <span className="text-xs text-muted-foreground">{c.row.original.vendor_name}</span>
      </span>
    ),
  }),
  billCol.accessor('po_number', { header: 'Our PO', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
  billCol.accessor('authorized_po_total', { header: 'Ordered', meta: { align: 'right' }, cell: (c) => money(c.getValue()) }),
  billCol.accessor('actual_billed_total', { header: 'Billed', meta: { align: 'right' }, cell: (c) => money(c.getValue()) }),
  billCol.accessor('cost_variance_amount', {
    header: 'Difference',
    meta: { align: 'right' },
    cell: (c) => {
      const d = Number(c.getValue() ?? 0);
      return d > 0 ? <span className="font-medium text-bad">+{money(d)}</span> : money(0);
    },
  }),
  billCol.accessor('reconciliation_status', {
    header: 'Result',
    cell: (c) => {
      const r = BILL_RESULT[c.getValue()] ?? { label: c.getValue(), tone: 'warn' as const };
      return <ToneBadge tone={r.tone}>{r.label}</ToneBadge>;
    },
  }),
]);

export function SupplierBills() {
  const { hasRole } = useAuth();
  const bills = useQuery({ queryKey: ['orders', 'bills'], queryFn: () => fetchReconciliationHistory(50) as Promise<Bill[]>, refetchInterval: 60_000 });
  const [open, setOpen] = useState<Bill | null>(null);

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">
        Each bill is checked against our PO and what arrived. Price rises and surprise freight are flagged, and a dispute email is drafted.
      </p>
      {bills.isPending ? (
        <RowsSkeleton />
      ) : bills.isError ? (
        <ErrorState error={bills.error} onRetry={() => void bills.refetch()} />
      ) : (
        <DataTable
          label="Supplier bills"
          columns={billColumns}
          data={bills.data ?? []}
          getRowId={(b) => b.reconciliation_id || b.invoice_number}
          selectedId={open ? open.reconciliation_id || open.invoice_number : null}
          onSelect={(b) => setOpen(b)}
          empty={<EmptyState title="No bills checked yet">Bills read from email or checked below appear here.</EmptyState>}
        />
      )}
      <DetailPanel open={Boolean(open)} onClose={() => setOpen(null)} title={`Bill ${open?.invoice_number ?? ''}`} description={open?.vendor_name}>
        {open?.dispute_email_draft ? (
          <div className="grid gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">Dispute email (draft, not sent)</h3>
            <pre className="font-sans text-sm whitespace-pre-wrap">{open.dispute_email_draft}</pre>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">This bill matched; there is nothing to dispute.</p>
        )}
      </DetailPanel>
      {hasRole('OPERATOR') && <CheckBill />}
    </div>
  );
}

function CheckBill() {
  const client = useQueryClient();
  const { data: suppliers } = useSuppliers();
  const empty = { invoice_number: '', vendor_name: '', po_number: '', item_id: '', quantity_billed: '', unit_cost_billed: '', freight_billed: '0', total_amount_billed: '' };
  const [form, setForm] = useState(empty);
  const check = useMutation({
    mutationFn: () => {
      const qty = Number(form.quantity_billed);
      const unit = Number(form.unit_cost_billed);
      return matchVendorInvoice({
        invoice_number: form.invoice_number,
        vendor_name: form.vendor_name,
        po_number: form.po_number,
        lines: [{ item_id: form.item_id, quantity_billed: qty, unit_cost_billed: unit, total_amount: qty * unit }],
        freight_billed: Number(form.freight_billed) || 0,
        total_amount_billed: Number(form.total_amount_billed),
      });
    },
    onSuccess: (res: { reconciliation_status: string }) => {
      const r = BILL_RESULT[res.reconciliation_status];
      if (res.reconciliation_status === 'RECONCILED_APPROVED') toast.success('Bill matches the PO. Cleared to pay.');
      else toast.warning(`Bill checked: ${r?.label ?? res.reconciliation_status}`, { description: 'See the list for the difference.' });
      setForm(empty);
      void client.invalidateQueries({ queryKey: ['orders', 'bills'] });
    },
    onError: (e) => toast.error('Couldn’t check the bill', { description: e.message }),
  });
  const field = (k: keyof typeof empty) => ({ value: form[k], onChange: (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value }) });

  return (
    <details className="rounded-lg border bg-card p-4">
      <summary className="cursor-pointer text-sm font-medium">Check a supplier bill</summary>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          check.mutate();
        }}
      >
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium">Supplier</span>
          <select
            required
            {...field('vendor_name')}
            className="h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <option value="">{suppliers ? 'Choose a supplier' : 'Loading suppliers…'}</option>
            {(suppliers ?? [])
              .filter((s) => s.vendor_id !== 'STOCK')
              .map((s) => (
                <option key={s.vendor_id} value={s.vendor_id}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
        <Labeled label="Bill number" {...field('invoice_number')} />
        <Labeled label="Our PO number" {...field('po_number')} />
        <Labeled label="Part number" {...field('item_id')} />
        <Labeled label="Quantity" type="number" min="1" step="1" {...field('quantity_billed')} />
        <Labeled label="Unit price ($)" type="number" step="0.01" {...field('unit_cost_billed')} />
        <Labeled label="Freight ($)" type="number" step="0.01" {...field('freight_billed')} />
        <Labeled label="Bill total ($)" type="number" step="0.01" {...field('total_amount_billed')} />
        <Button type="submit" size="sm" className="justify-self-start" disabled={check.isPending}>
          {check.isPending && <Loader2 className="animate-spin" />}
          Check bill
        </Button>
      </form>
    </details>
  );
}

function Labeled({ label, ...props }: { label: string } & React.ComponentProps<typeof Input>) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="font-medium">{label}</span>
      <Input required {...props} />
    </label>
  );
}

/* --------------------------------------------------------------- suppliers */

export function Suppliers() {
  const { data, isPending, error, refetch } = useSuppliers();
  if (isPending) return <RowsSkeleton />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {(data ?? []).map((s) => (
        <article key={s.vendor_id} className="grid content-start gap-2 rounded-lg border bg-card p-4">
          <header className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold">{s.name}</h3>
            <span className="ml-auto font-mono text-xs text-muted-foreground">{s.vendor_id}</span>
          </header>
          {s.category && <p className="text-sm text-muted-foreground">{s.category}</p>}
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-[13px]">
            {s.lead_time_days != null && (
              <>
                <dt className="text-muted-foreground">Lead time</dt>
                <dd className="tabular-nums">{s.lead_time_days} days</dd>
              </>
            )}
            {s.moq && (
              <>
                <dt className="text-muted-foreground">Minimum order</dt>
                <dd>{s.moq}</dd>
              </>
            )}
            {s.freight_terms && (
              <>
                <dt className="text-muted-foreground">Freight</dt>
                <dd>{s.freight_terms}</dd>
              </>
            )}
            {s.contact_email && (
              <>
                <dt className="text-muted-foreground">Orders to</dt>
                <dd className="break-all">{s.contact_email}</dd>
              </>
            )}
          </dl>
          {s.blind_ship_rules && (
            <p className="rounded-md bg-warn-soft px-2.5 py-1.5 text-xs text-warn">
              <span className="font-medium">Blind ship:</span> {s.blind_ship_rules}
            </p>
          )}
        </article>
      ))}
    </div>
  );
}
