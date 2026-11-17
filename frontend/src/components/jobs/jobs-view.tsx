'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { FilterChips } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { since } from '@/lib/format';
import { STAGES, jobsCall, stageLabel, type JobRow } from './job-types';

// The server names a role; say it the way people do.
const WHO: Record<string, string> = {
  reviewer: 'A reviewer',
  engineer: 'An engineer',
  manager: 'A manager',
  admin: 'An administrator',
  operator: 'Someone on the team',
};

const col = columnHelper<JobRow>();

const columns = col.columns([
  col.accessor('ref', { header: 'Job', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
  col.accessor('title', { header: 'What', meta: { className: 'max-w-72 truncate' } }),
  col.accessor('customer', {
    header: 'Customer',
    meta: { className: 'max-w-48 truncate', hideBelow: 'xl' },
    cell: (c) => c.getValue() || '–',
  }),
  col.accessor('stage', {
    header: 'Stage',
    cell: (c) => <ToneBadge tone={c.getValue() === 'CLOSED' ? 'neutral' : 'info'}>{stageLabel(c.getValue())}</ToneBadge>,
  }),
  col.display({
    id: 'waiting',
    header: 'Waiting on',
    cell: ({ row }) => {
      const w = row.original.waiting?.[0];
      return w ? (
        <span className="grid">
          <span>{w.what}</span>
          {w.who && <span className="text-xs text-muted-foreground">{WHO[w.who] ?? w.who}</span>}
        </span>
      ) : (
        <span className="text-muted-foreground">Nothing</span>
      );
    },
  }),
  col.accessor('updated_at', { header: 'Updated', meta: { hideBelow: 'xl' }, cell: (c) => (c.getValue() ? since(c.getValue()!) : '–') }),
]);

/** Every job, from the customer's quote request to the shipment, and where each one is stuck. */
export function JobsView() {
  const router = useRouter();
  const [stage, setStage] = useState('all');
  const list = useQuery({
    queryKey: ['jobs', 'list'],
    queryFn: () => jobsCall<{ items: JobRow[]; total: number }>('?limit=200'),
    refetchInterval: 60_000,
    retry: false,
  });

  if (list.isPending) return <RowsSkeleton rows={6} />;
  if (list.isError) return <ErrorState error={list.error} onRetry={() => void list.refetch()} />;
  if (!list.data) return <EmptyState title="Not available yet">Jobs show up here once they are switched on.</EmptyState>;

  const items = list.data.items;
  const shown = stage === 'all' ? items : items.filter((j) => j.stage === stage);
  const count = (s: string) => items.filter((j) => j.stage === s).length;

  return (
    <div className="grid gap-4">
      <FilterChips
        label="Stage"
        value={stage}
        onChange={setStage}
        options={[
          { value: 'all', label: 'All', count: items.length },
          ...STAGES.filter(([k]) => count(k) > 0).map(([k, label]) => ({ value: k, label, count: count(k) })),
        ]}
      />
      <DataTable
        label="Jobs"
        columns={columns}
        data={shown}
        getRowId={(j) => String(j.id)}
        onSelect={(j) => router.push(`/jobs/${encodeURIComponent(j.ref)}`)}
        empty={
          <EmptyState title="No jobs yet">
            Open a quote request and press “Open the job” to start one. It then follows the work to the shipment.
          </EmptyState>
        }
      />
    </div>
  );
}
