'use client';

import { useEffect, useRef, useState } from 'react';
import { parseAsBoolean, parseAsInteger, parseAsString, parseAsStringLiteral, useQueryState, useQueryStates } from 'nuqs';
import { useQueryClient } from '@tanstack/react-query';
import { useHotkeys } from 'react-hotkeys-hook';
import { Plus, Printer, Search } from 'lucide-react';
import { toast } from 'sonner';
import { DownloadMenu } from '@/components/data/download-menu';
import { CommentCount, useCommentCounts } from '@/components/data/updates-panel';
import { SavedViews } from '@/components/data/saved-views';
import { DataTable, columnHelper, recordKey } from '@/components/data/data-table';
import { DetailPanel } from '@/components/data/detail-panel';
import { chipClass } from '@/components/data/filters';
import { TabsWithMore } from '@/components/data/tabs-more';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useAuth } from '@/context/AuthContext';
import { useListNav } from '@/hooks/use-list-nav';
import { useMediaQuery } from '@/hooks/use-media-query';
import { openDocument } from '@/lib/assistant-api';
import { date, money, pct, kindWord } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useQuery } from '@tanstack/react-query';
import { authFetch } from '@/lib/api';
import { NeedsYou } from './needs-you';
import { NewOrderDialog } from './new-order-dialog';
import { OrderDetail } from './order-detail';
import { PackingSlips, usePackingQueue } from './packing-slips';
import { OrderEmails, SupplierBills, Suppliers } from './order-tabs';
import {
  ARCHETYPE,
  PAGE_SIZE,
  STATUS,
  complianceQuery,
  orderQuery,
  useOrders,
  useSuppliers,
  type Order,
  type OrderView,
} from './use-orders';

const TABS = ['orders', 'slips', 'emails', 'bills', 'suppliers'] as const;
const TAB_LABEL = { orders: 'All orders', slips: 'Packing slips', emails: 'Order emails', bills: 'Supplier bills', suppliers: 'Suppliers' };
const VIEWS = ['all', 'attention', 'not_in_sage', 'in_sage'] as const;
const VIEW_LABEL: Record<OrderView, string> = {
  all: 'All',
  attention: 'Needs attention',
  not_in_sage: 'Not in a Sage file yet',
  in_sage: 'In a Sage file',
};

export function OrdersView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('orders'));
  const failed = usePackingQueue().data?.counts?.FAILED ?? 0;
  // One call says what in each section needs a person, so nothing waits out of sight.
  const attention = useQuery({
    queryKey: ['orders', 'attention'],
    queryFn: async (): Promise<{ emails_unread: number; bills_to_check: number } | null> => {
      const res = await authFetch('/api/v1/orders/attention', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? await res.json() : null;
    },
    refetchInterval: 60_000,
    retry: false,
  }).data;
  const emails = attention?.emails_unread ?? 0;
  const bills = attention?.bills_to_check ?? 0;
  const label = (t: (typeof TABS)[number]) =>
    t === 'slips' && failed > 0
      ? `Packing slips (${failed} need a person)`
      : t === 'emails' && emails > 0
        ? `Order emails (${emails})`
        : t === 'bills' && bills > 0
          ? `Supplier bills (${bills})`
          : TAB_LABEL[t];
  const hidden = emails + bills;
  return (
    <div className="grid gap-4">
      <NeedsYou />
      <TabsWithMore
        label="Orders sections"
        show={2}
        moreHint={hidden > 0 ? `${hidden} need you` : undefined}
        value={tab}
        onChange={(t) => void setTab(t === 'orders' ? null : t)}
        tabs={TABS.map((t) => [t, label(t)] as const)}
      />
      {tab === 'orders' && <OrderList />}
      {tab === 'slips' && <PackingSlips />}
      {tab === 'emails' && <OrderEmails />}
      {tab === 'bills' && <SupplierBills />}
      {tab === 'suppliers' && <Suppliers />}
    </div>
  );
}

const col = columnHelper<Order>();

/** Order totals in each order's own currency; mixed currencies are never added together. */
function totalsByCurrency(rows: Order[]) {
  const sums: Record<string, number> = {};
  rows.forEach((o) => {
    sums[o.currency] = (sums[o.currency] ?? 0) + Number(o.total_revenue || 0);
  });
  return Object.entries(sums)
    .map(([currency, sum]) => money(sum, currency))
    .join(' · ');
}
const averageMargin = (rows: Order[]) => {
  const priced = rows.filter((o) => Number(o.total_revenue) > 0);
  return priced.length ? pct(priced.reduce((n, o) => n + Number(o.gross_margin_pct || 0), 0) / priced.length) : '–';
};

function OrderList() {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''));
  const [view, setView] = useQueryState('view', parseAsStringLiteral(VIEWS).withDefault('all'));
  const [vendor, setVendor] = useQueryState('vendor', parseAsString.withDefault(''));
  const [page, setPage] = useQueryState('page', parseAsInteger.withDefault(1));
  const [so, setSo] = useQueryState('order');
  const [creating, setCreating] = useState(false);
  // Other pages open a filled-in new order with ?new=true&customer=…&part=…&price=…
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
  const comments = useCommentCounts(
    'order',
    rows.map((o) => o.sales_order_number),
  ).data;

  const columns = col.columns([
    col.accessor('sales_order_number', {
      header: 'Order',
      cell: (c) => (
        <span className="whitespace-nowrap">
          <span className="font-mono text-xs">{c.getValue()}</span>
          <CommentCount n={comments?.[c.getValue()]} />
        </span>
      ),
    }),
    col.display({
      id: 'print',
      header: 'Packing slip',
      cell: ({ row }) => {
        const o = row.original;
        if (!o.has_packing_slip) return <span className="text-xs text-muted-foreground">None yet</span>;
        return (
          <Button
            size="sm"
            variant="outline"
            onClick={(e) => {
              e.stopPropagation();
              openDocument(`/api/v1/orders/${encodeURIComponent(o.sales_order_number)}/packing-slip`).catch((err: Error) =>
                toast.error('Couldn’t open the packing slip', { description: err.message }),
              );
            }}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Printer /> Print
          </Button>
        );
      },
    }),
    col.accessor('customer_name', { header: 'Customer', meta: { className: 'max-w-32 truncate' } }),
    col.accessor('archetype', { header: 'Type', meta: { hideBelow: '2xl' }, cell: (c) => ARCHETYPE[c.getValue() ?? ''] ?? '–' }),
    col.accessor('purchase_orders', {
      header: 'Suppliers',
      meta: { hideBelow: 'xl' },
      cell: (c) => Object.keys(c.getValue() ?? {}).join(', ') || <span className="text-muted-foreground">Stock</span>,
    }),
    col.accessor('total_revenue', {
      header: 'Total',
      meta: { align: 'right', footer: totalsByCurrency },
      cell: (c) => money(c.getValue(), c.row.original.currency),
    }),
    col.accessor('gross_margin_pct', {
      header: 'Margin',
      meta: { align: 'right', footer: (rows) => `avg ${averageMargin(rows)}` },
      cell: (c) => {
        const thin = Number(c.row.original.total_revenue) > 0 && Number(c.getValue()) < floor;
        return <span className={cn(thin && 'font-semibold text-bad')}>{pct(c.getValue())}</span>;
      },
    }),
    col.accessor('status', {
      header: 'Status',
      cell: (c) => {
        const s = STATUS[c.getValue()] ?? { label: kindWord(c.getValue()), tone: 'neutral' as const };
        const word = (c.row.original as Order & { status_word?: string }).status_word;
        const sage = (c.row.original as Order & { sage?: { paid_status?: string; amount_due?: string | number } | null }).sage;
        const paid =
          sage?.paid_status === 'PAID'
            ? 'Paid in Sage'
            : sage?.paid_status === 'PART_PAID'
              ? 'Part paid in Sage'
              : sage?.paid_status === 'UNPAID'
                ? 'Unpaid in Sage'
                : '';
        return (
          <span className="grid gap-0.5">
            <ToneBadge tone={s.tone}>{word || s.label}</ToneBadge>
            {paid && (
              <span className="text-xs text-muted-foreground">
                {paid}
                {sage?.paid_status !== 'PAID' && sage?.amount_due != null
                  ? ` · ${money(sage.amount_due, c.row.original.currency)} due`
                  : ''}
              </span>
            )}
          </span>
        );
      },
    }),
    col.accessor('sage_exported_at', {
      header: 'Sage file',
      meta: { hideBelow: '2xl' },
      cell: (c) => (c.getValue() ? date(c.getValue()) : '–'),
    }),
    col.accessor('created_at', { header: 'Received', meta: { hideBelow: '2xl' }, cell: (c) => date(c.getValue()) }),
  ]);

  const open = (o?: Order) => void setSo(o ? o.sales_order_number : null);
  useListNav(rows, index, open);
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
        <div className="ml-auto flex items-center gap-1">
          <SavedViews screen="orders" />
          {hasRole('OPERATOR') && <DownloadMenu list="orders" />}
          {hasRole('OPERATOR') && (
            <Button size="sm" className="ml-1" onClick={() => setCreating(true)}>
              <Plus /> New order <Kbd className="bg-transparent text-current opacity-60">N</Kbd>
            </Button>
          )}
        </div>
      </div>

      <div className={cn('grid items-start gap-4', wide && so && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className={cn('grid min-w-0 gap-2 transition-opacity', isFetching && !isPending && 'opacity-70')}>
          {isPending ? (
            <RowsSkeleton rows={8} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <>
              {/* On a phone each order is a small card instead of a table that scrolls sideways. */}
              <ul className="grid gap-2 pb-20 md:hidden md:pb-0" aria-label="Orders">
                {rows.map((o) => {
                  const st = STATUS[o.status] ?? { label: o.status, tone: 'neutral' as const };
                  return (
                    <li key={o.sales_order_number}>
                      <button
                        type="button"
                        onClick={() => open(o)}
                        className="grid w-full gap-1 rounded-lg border bg-card p-3 text-left text-[13px] active:bg-muted/50"
                      >
                        <span className="flex items-center gap-2">
                          <span className="font-medium">{o.sales_order_number}</span>
                          <ToneBadge tone={st.tone}>{st.label}</ToneBadge>
                          <span className="ml-auto tabular-nums">{money(o.total_revenue, o.currency)}</span>
                        </span>
                        <span className="truncate text-muted-foreground">{o.customer_name}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              <div className="max-md:hidden">
                <DataTable
                  label="Orders"
                  columns={columns}
                  data={rows}
                  getRowId={(o) => o.sales_order_number}
                  recordName={(o) => recordKey('order', o.sales_order_number)}
                  onPrefetch={(o) => {
                    void client.prefetchQuery(orderQuery(o.sales_order_number));
                    void client.prefetchQuery(complianceQuery(o.sales_order_number));
                  }}
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
              </div>
            </>
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

        <DetailPanel
          open={Boolean(so)}
          onClose={() => open(undefined)}
          recordName={so ? recordKey('order', so) : undefined}
          title={<span className="font-mono">{so}</span>}
          description={rows[index]?.customer_name}
        >
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
