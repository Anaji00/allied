'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, Loader2 } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { FilterChips } from '@/components/data/filters';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { openDocument } from '@/lib/assistant-api';
import { since } from '@/lib/format';

type State = 'QUEUED' | 'READY' | 'FAILED' | 'PRINTED';
type Job = {
  sales_order_number: string;
  customer_po_number?: string | null;
  status: State;
  attempts: number;
  last_error?: string | null;
  ready?: boolean;
  created_at?: string | null;
  ready_at?: string | null;
  printed_at?: string | null;
  printed_by?: string | null;
  download?: string | null;
};
type Queue = { counts: Partial<Record<State, number>>; jobs: Job[] };

const LABEL: Record<State, { label: string; tone: Tone }> = {
  QUEUED: { label: 'Being made', tone: 'neutral' },
  READY: { label: 'Ready to print', tone: 'ok' },
  FAILED: { label: 'Needs a person', tone: 'bad' },
  PRINTED: { label: 'Printed', tone: 'neutral' },
};
const FILTERS = ['READY', 'QUEUED', 'FAILED', 'PRINTED'] as const;

async function problem(res: Response) {
  const detail = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`;
}

/** One shared query, so the tab can show the "needs a person" count too. */
export function usePackingQueue(state?: State) {
  return useQuery({
    queryKey: ['packing-slips', state ?? 'all'],
    queryFn: async (): Promise<Queue | null> => {
      const res = await authFetch(`/api/v1/packing-slips/queue${state ? `?state=${state}` : ''}`, { cache: 'no-store' });
      // Until the database update is applied the route fails; show "not available yet" rather than an error.
      if (res.status === 404 || res.status >= 500) return null;
      if (!res.ok) throw new Error(await problem(res));
      return (await res.json()) as Queue;
    },
    refetchInterval: 30_000,
    retry: false,
  });
}

const col = columnHelper<Job>();

export function PackingSlips() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [state, setState] = useState<State>('READY');
  const queue = usePackingQueue(state);
  const counts = usePackingQueue().data?.counts ?? queue.data?.counts ?? {};

  const act = useMutation({
    mutationFn: async ({ so, what }: { so: string; what: 'retry' | 'printed' }) => {
      const res = await authFetch(`/api/v1/packing-slips/${encodeURIComponent(so)}/${what}`, { method: 'POST' });
      if (!res.ok) throw new Error(await problem(res));
    },
    onSuccess: (_r, v) => {
      toast.success(v.what === 'retry' ? 'Trying again' : 'Marked as printed');
      void client.invalidateQueries({ queryKey: ['packing-slips'] });
    },
    onError: (e) => toast.error('That didn’t go through', { description: e.message }),
  });
  const open = useMutation({
    mutationFn: (href: string) => openDocument(href),
    onError: (e) => toast.error('Couldn’t open the packing slip', { description: e.message }),
  });

  const columns = col.columns([
    col.accessor('customer_po_number', {
      header: 'Customer PO',
      cell: (c) => (
        <span className="grid">
          <span className="font-mono text-xs">{c.getValue() || '–'}</span>
          <span className="text-xs text-muted-foreground">Our order {c.row.original.sales_order_number}</span>
        </span>
      ),
    }),
    col.accessor('status', {
      header: 'State',
      cell: (c) => {
        const s = LABEL[c.getValue()];
        const j = c.row.original;
        return (
          <span className="grid gap-0.5">
            <ToneBadge tone={s.tone}>{s.label}</ToneBadge>
            {j.status === 'FAILED' && j.last_error && (
              <span className="line-clamp-2 max-w-72 text-xs text-bad" title={j.last_error}>
                {j.last_error}
              </span>
            )}
            {j.status === 'QUEUED' && j.attempts > 0 && <span className="text-xs text-muted-foreground">Try {j.attempts}, will try again</span>}
          </span>
        );
      },
    }),
    col.accessor('created_at', { header: 'Queued', cell: (c) => (c.getValue() ? since(c.getValue()!) : '–') }),
    col.display({
      id: 'actions',
      header: '',
      meta: { align: 'right' },
      cell: ({ row }) => {
        const j = row.original;
        return (
          <span className="flex justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
            {j.download && (j.status === 'READY' || j.status === 'PRINTED') && (
              <Button size="sm" variant="outline" disabled={open.isPending} onClick={() => open.mutate(j.download!)}>
                <Download /> Open
              </Button>
            )}
            {hasRole('OPERATOR') && j.status === 'READY' && (
              <Button size="sm" disabled={act.isPending} onClick={() => act.mutate({ so: j.sales_order_number, what: 'printed' })}>
                Mark printed
              </Button>
            )}
            {hasRole('OPERATOR') && j.status === 'FAILED' && (
              <Button size="sm" disabled={act.isPending} onClick={() => act.mutate({ so: j.sales_order_number, what: 'retry' })}>
                {act.isPending && <Loader2 className="animate-spin" />} Try again
              </Button>
            )}
          </span>
        );
      },
    }),
  ]);

  if (queue.isPending) return <RowsSkeleton />;
  if (queue.isError)
    return <EmptyState title="Couldn’t load packing slips">{queue.error.message}</EmptyState>;
  if (queue.data === null)
    return (
      <EmptyState title="Not available yet">
        Packing slips will be made and queued here for each new order once the system update is switched on.
      </EmptyState>
    );

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">
        Each new order gets its packing slip made right away. If one can’t be made, it tries again, then asks for a person.
      </p>
      <FilterChips
        label="Show"
        value={state}
        onChange={setState}
        options={FILTERS.map((f) => ({
          value: f,
          label: LABEL[f].label,
          count: counts[f],
          tone: f === 'FAILED' ? ('bad' as const) : undefined,
        }))}
      />
      <DataTable
        label="Packing slips"
        columns={columns}
        data={queue.data.jobs}
        getRowId={(j) => j.sales_order_number}
        minWidth={640}
        empty={<EmptyState title="Nothing here">No packing slips in this state.</EmptyState>}
      />
    </div>
  );
}
