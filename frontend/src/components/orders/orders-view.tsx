'use client';

import { useEffect, useRef, useState } from 'react';
import { parseAsBoolean, parseAsInteger, parseAsString, parseAsStringLiteral, useQueryState, useQueryStates } from 'nuqs';
import { useHotkeys } from 'react-hotkeys-hook';
import { Plus, Search } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { DetailPanel } from '@/components/data/detail-panel';
import { SectionTabs, chipClass } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { date, money, pct } from '@/lib/format';
import { cn } from '@/lib/utils';
import { NewOrderDialog } from './new-order-dialog';
import { OrderDetail } from './order-detail';
import { OrderEmails, SupplierBills, Suppliers } from './order-tabs';
import { ARCHETYPE, PAGE_SIZE, STATUS, useOrders, useSuppliers, type Order, type OrderView } from './use-orders';

const TABS = ['orders', 'emails', 'bills', 'suppliers'] as const;
const TAB_LABEL = { orders: 'All orders', emails: 'Order emails', bills: 'Supplier bills', suppliers: 'Suppliers' };
const VIEWS = ['all', 'attention', 'not_in_sage', 'in_sage'] as const;
const VIEW_LABEL: Record<OrderView, string> = {
  all: 'All',
  attention: 'Needs attention',
  not_in_sage: 'Not in Sage yet',
  in_sage: 'In Sage',
};

export function OrdersView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('orders'));
  return (
    <div className="grid gap-4">
      <SectionTabs label="Orders sections" value={tab} onChange={(t) => void setTab(t === 'orders' ? null : t)} tabs={TABS.map((t) => [t, TAB_LABEL[t]] as const)} />
      {tab === 'orders' && <OrderList />}
      {tab === 'emails' && <OrderEmails />}
      {tab === 'bills' && <SupplierBills />}
      {tab === 'suppliers' && <Suppliers />}
    </div>
  );
}

const col = columnHelper<Order>();

function OrderList() {
  const { hasRole } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''));
  const [view, setView] = useQueryState('view', parseAsStringLiteral(VIEWS).withDefault('all'));
  const [vendor, setVendor] = useQueryState('vendor', parseAsString.withDefault(''));
  const [page, setPage] = useQueryState('page', parseAsInteger.withDefault(1));
  const [so, setSo] = useQueryState('order');
  const [creating, setCreating] = useState(false);
  // Other pages open a filled-in new order with ?new=1&customer=…&part=…&price=…
  const [prefill, setPrefill] = useQueryStates({
    new: parseAsBoolean,
    customer: parseAsString,
    part: parseAsString,
    price: parseAsString,
  });
  const [draft, setDraft] = useState(q);
  const searchRef = useRef<HTMLInputElement>(null);

  // Typing filters after a short pause, not on every key.
  useEffect(() => {
    if (draft === q) return undefined;
    const t = setTimeout(() => {
      void setQ(draft || null);
      void setPage(null);
    }, 300);
    return () => clearTimeout(t);
  }, [draft, q, setQ, setPage]);

  const { data, isPending, isFetching, error, refetch } = useOrders({ q, view, vendor, page });
  const { data: suppliers } = useSuppliers();
  const rows = data?.items ?? [];
  const floor = data?.min_margin_pct ?? 15;
  const total = data?.total ?? 0;
  const index = rows.findIndex((o) => o.sales_order_number === so);

  const columns = col.columns([
    col.accessor('sales_order_number', { header: 'Order', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
    col.accessor('customer_name', { header: 'Customer', meta: { className: 'max-w-64 truncate' } }),
    col.accessor('archetype', { header: 'Type', cell: (c) => ARCHETYPE[c.getValue() ?? ''] ?? '–' }),
    col.accessor('purchase_orders', {
      header: 'Suppliers',
      cell: (c) => Object.keys(c.getValue() ?? {}).join(', ') || <span className="text-muted-foreground">Stock</span>,
    }),
    col.accessor('total_revenue', { header: 'Total', meta: { align: 'right' }, cell: (c) => money(c.getValue(), c.row.original.currency) }),
    col.accessor('gross_margin_pct', {
      header: 'Margin',
      meta: { align: 'right' },
      cell: (c) => {
        const thin = Number(c.row.original.total_revenue) > 0 && Number(c.getValue()) < floor;
        return <span className={cn(thin && 'font-semibold text-bad')}>{pct(c.getValue())}</span>;
      },
    }),
    col.accessor('status', {
      header: 'Status',
      cell: (c) => {
        const s = STATUS[c.getValue()] ?? { label: c.getValue(), tone: 'neutral' as const };
        return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
      },
    }),
    col.accessor('sage_exported_at', { header: 'Sage file', cell: (c) => (c.getValue() ? date(c.getValue()) : '–') }),
    col.accessor('created_at', { header: 'Received', cell: (c) => date(c.getValue()) }),
  ]);

  const open = (o?: Order) => void setSo(o ? o.sales_order_number : null);
  useHotkeys(['j', 'ArrowDown'], () => rows[index + 1] && open(rows[index + 1]), { preventDefault: true }, [rows, index]);
  useHotkeys(['k', 'ArrowUp'], () => (index > 0 ? open(rows[index - 1]) : index === -1 && rows[0] && open(rows[0])), { preventDefault: true }, [rows, index]);
  useHotkeys('escape', () => open(undefined));
  useHotkeys('slash', () => searchRef.current?.focus(), { preventDefault: true });
  useHotkeys('n', () => hasRole('OPERATOR') && setCreating(true), [hasRole]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            type="search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Order, customer PO or customer"
            aria-label="Search orders"
            className="pl-8"
          />
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show">
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              className={chipClass(view === v)}
              onClick={() => {
                void setView(v === 'all' ? null : v);
                void setPage(null);
              }}
            >
              {VIEW_LABEL[v]}
            </button>
          ))}
        </div>
        <select
          aria-label="Supplier"
          value={vendor}
          onChange={(e) => {
            void setVendor(e.target.value || null);
            void setPage(null);
          }}
          className="h-7 rounded-full border bg-card px-3 text-xs text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <option value="">All suppliers</option>
          {(suppliers ?? [])
            .filter((s) => s.vendor_id !== 'STOCK')
            .map((s) => (
              <option key={s.vendor_id} value={s.vendor_id}>
                {s.vendor_id}
              </option>
            ))}
        </select>
        {hasRole('OPERATOR') && (
          <Button size="sm" className="ml-auto" onClick={() => setCreating(true)}>
            <Plus /> New order <Kbd className="bg-transparent text-current opacity-60">N</Kbd>
          </Button>
        )}
      </div>

      <div className={cn('grid items-start gap-4', wide && so && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className={cn('grid min-w-0 gap-2 transition-opacity', isFetching && !isPending && 'opacity-70')}>
          {isPending ? (
            <RowsSkeleton rows={8} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Orders"
              columns={columns}
              data={rows}
              getRowId={(o) => o.sales_order_number}
              selectedId={so}
              onSelect={open}
              minWidth={860}
              empty={
                <EmptyState title={q || view !== 'all' || vendor ? 'No orders match' : 'No orders yet'}>
                  {q || view !== 'all' || vendor
                    ? 'Try a shorter search or another filter.'
                    : 'Orders appear here once an order email is read or an order is created.'}
                </EmptyState>
              }
            />
          )}
          {total > 0 && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="tabular-nums">
                {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
              </span>
              <span className="ml-1 hidden md:inline">
                · Margins under {floor}% in red · <Kbd>J</Kbd>
                <Kbd>K</Kbd> move · <Kbd>/</Kbd> search
              </span>
              <div className="ml-auto flex gap-1">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => void setPage(page - 1 > 1 ? page - 1 : null)}>
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={page * PAGE_SIZE >= total} onClick={() => void setPage(page + 1)}>
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>

        <DetailPanel open={Boolean(so)} onClose={() => open(undefined)} title={<span className="font-mono">{so}</span>} description={rows[index]?.customer_name}>
          {so && <OrderDetail key={so} so={so} />}
        </DetailPanel>
      </div>

      <NewOrderDialog
        key={prefill.new ? 'prefilled' : 'blank'}
        open={creating || Boolean(prefill.new)}
        onOpenChange={(next) => {
          setCreating(next);
          if (!next && prefill.new) void setPrefill(null);
        }}
        initial={prefill.new ? { customer: prefill.customer ?? '', part: prefill.part ?? '', price: prefill.price ?? '' } : undefined}
        onCreated={(created) => void setSo(created)}
      />
    </div>
  );
}
