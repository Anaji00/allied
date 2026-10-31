'use client';

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { api, ApiError, ok } from '@/lib/api/client';
import type { Tone } from '@/components/data/tone-badge';

export type OrderLine = {
  item_id?: string;
  description?: string;
  raw_item_text?: string;
  quantity?: number | string;
  unit_price?: number | string;
  unit_cost?: number | string;
  uom?: string;
  preferred_vendor?: string;
};

export type Order = {
  order_id: string;
  sales_order_number: string;
  customer_po_number: string;
  customer_id: string;
  customer_name: string;
  facility: string;
  currency: string;
  status: string;
  archetype?: string;
  total_revenue: string | number;
  total_expense: string | number;
  gross_margin_pct: string | number;
  purchase_orders: Record<string, string>;
  line_items: OrderLine[];
  shipping_address?: string | null;
  payment_terms?: string | null;
  po_date?: string | null;
  created_at?: string | null;
  created_by?: string | null;
  monday_item_id?: string | null;
  sage_exported_at?: string | null;
  sage_po_exported_at?: string | null;
  internal_notes?: string | null;
  has_packing_slip?: boolean;
  min_margin_pct?: number;
};

export type OrderPage = { total: number; limit: number; offset: number; min_margin_pct: number; items: Order[] };
export type OrderView = 'all' | 'attention' | 'not_in_sage' | 'in_sage';

export const PAGE_SIZE = 50;

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  RECEIVED: { label: 'Received', tone: 'neutral' },
  VALIDATED: { label: 'Checked', tone: 'info' },
  PROCESSING: { label: 'Writing to Sage', tone: 'info' },
  SYNCED_TO_SAGE: { label: 'In Sage', tone: 'ok' },
  VALIDATION_ERROR: { label: 'Failed checks', tone: 'bad' },
  SAGE_ERROR: { label: 'Sage write failed', tone: 'bad' },
};

export const ARCHETYPE: Record<string, string> = {
  STANDARD_STOCK: 'Stock',
  BLIND_DROPSHIP: 'Blind ship',
  BLUEPRINT_ASSEMBLY: 'Assembly',
  CROSS_BORDER: 'Cross-border',
};

export function useOrders({ q, view, vendor, page }: { q: string; view: OrderView; vendor: string; page: number }) {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ['orders', 'list', { q, view, vendor, page }],
    queryFn: async () =>
      (await ok(
        api.GET('/api/v1/orders', {
          params: {
            query: {
              q: q || undefined,
              view,
              vendor: vendor || undefined,
              limit: PAGE_SIZE,
              offset: (page - 1) * PAGE_SIZE,
            },
          },
        }),
      )) as unknown as OrderPage,
    enabled: isAuthenticated,
    // Paging and filtering keep the last page on screen until the next arrives.
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
}

export function useOrder(so: string | null) {
  return useQuery({
    queryKey: ['orders', 'one', so],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/orders/{so_number}', { params: { path: { so_number: so! } } }))) as unknown as Order,
    enabled: Boolean(so),
  });
}

export type ComplianceBundle = { heat_numbers?: string[]; blind_shipping_enforced?: boolean; drawing_reference?: string };

/** Heats and blind-ship status. Held in memory, so it can be missing after a restart. */
export function useCompliance(so: string | null) {
  return useQuery({
    queryKey: ['orders', 'compliance', so],
    queryFn: async () => {
      try {
        return (await ok(
          api.GET('/api/v1/cache/orders/{order_key}/compliance', { params: { path: { order_key: so! } } }),
        )) as ComplianceBundle;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(so),
  });
}

export type Supplier = {
  vendor_id: string;
  name: string;
  category?: string;
  lead_time_days?: number;
  moq?: string;
  freight_terms?: string;
  blind_ship_rules?: string;
  contact_email?: string;
  contract_discount_pct?: number;
};

export function useSuppliers() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ['suppliers'],
    queryFn: async () => Object.values((await ok(api.GET('/api/v1/cache/vendors'))) as Record<string, Supplier>),
    enabled: isAuthenticated,
    staleTime: 10 * 60_000,
  });
}
