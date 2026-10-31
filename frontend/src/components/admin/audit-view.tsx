'use client';

import { useEffect, useState } from 'react';
import { parseAsInteger, parseAsString, useQueryState } from 'nuqs';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Download, Search } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { NativeSelect } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { api, ok } from '@/lib/api/client';
import { dateTime, words } from '@/lib/format';
import { cn } from '@/lib/utils';

type Entry = {
  event_id: string;
  timestamp: string | null;
  actor_email: string | null;
  actor_role: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  facility: string | null;
  detail: string | null;
};
type Page = { total: number; entries: Entry[]; resource_types: string[] };

const PAGE = 50;

// Plain names for the record kinds staff see most; anything else is spelled out from its code.
const KIND: Record<string, string> = {
  ORDER: 'Orders',
  RECONCILIATION: 'Supplier bill checks',
  EXCEPTION: 'Problems',
  SAGE_EXPORT: 'Sage files',
  COMPLIANCE_NCR: 'Quality holds',
  LOGISTICS_DISPATCH: 'Shipping and customs',
  USER: 'User accounts',
  MONDAY_EVENT: 'monday events',
};

function tone(action: string): Tone {
  if (/FLAG|NCR|HALT|REJECT|FAIL|DISABLE/.test(action)) return 'bad';
  if (/APPROV|EXPORT|CREATE|RESOLV|DISPATCH/.test(action)) return 'ok';
  return 'info';
}

const col = columnHelper<Entry>();
const columns = col.columns([
  col.accessor('timestamp', { header: 'When', cell: (c) => <span className="whitespace-nowrap">{dateTime(c.getValue())}</span> }),
  col.accessor('action', { header: 'What happened', cell: (c) => <ToneBadge tone={tone(c.getValue())}>{words(c.getValue())}</ToneBadge> }),
  col.accessor('resource_type', {
    header: 'Record',
    cell: (c) => (
      <span className="grid">
        <span>{KIND[c.getValue()] ?? words(c.getValue())}</span>
        {c.row.original.resource_id && <span className="font-mono text-[11px] text-muted-foreground">{c.row.original.resource_id}</span>}
      </span>
    ),
  }),
  col.accessor('actor_email', { header: 'Who', meta: { className: 'max-w-56 truncate' }, cell: (c) => c.getValue() || '–' }),
  col.accessor('detail', { header: 'Details', meta: { className: 'max-w-[28rem] truncate' }, cell: (c) => c.getValue() || '' }),
]);

export function AuditView() {
  const { hasRole } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [kind, setKind] = useQueryState('kind', parseAsString.withDefault(''));
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''));
  const [page, setPage] = useQueryState('page', parseAsInteger.withDefault(1));
  const [id, setId] = useQueryState('entry');
  const [draft, setDraft] = useState(q);

  useEffect(() => {
    if (draft === q) return undefined;
    const t = setTimeout(() => {
      void setQ(draft || null);
      void setPage(null);
    }, 300);
    return () => clearTimeout(t);
  }, [draft, q, setQ, setPage]);

  const { data, isPending, isFetching, error, refetch } = useQuery({
    queryKey: ['audit', kind, q, page],
    queryFn: async () =>
      (await ok(
        api.GET('/api/v1/audit', {
          params: { query: { resource_type: kind || undefined, q: q || undefined, limit: PAGE, offset: (page - 1) * PAGE } },
        }),
      )) as Page,
    placeholderData: keepPreviousData,
    enabled: hasRole('MANAGER'),
  });
  const rows = data?.entries ?? [];
  const total = data?.total ?? 0;
  const index = rows.findIndex((e) => e.event_id === id);
  const selected = rows[index];
  const open = (e?: Entry) => void setId(e ? e.event_id : null);
  useListNav(rows, index, open);

  function downloadJson() {
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `allied_audit_${new Date().toISOString().slice(0, 10)}${kind ? `_${kind.toLowerCase()}` : ''}_p${page}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  if (!hasRole('MANAGER')) return <EmptyState title="Managers only">The audit trail is open to managers and admins.</EmptyState>;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Order, person, or words in the details" aria-label="Search the audit trail" className="pl-8" />
        </div>
        <NativeSelect
          aria-label="Kind of record"
          value={kind}
          onChange={(e) => {
            void setKind(e.target.value || null);
            void setPage(null);
          }}
          className="w-auto"
        >
          <option value="">Every kind of record</option>
          {(data?.resource_types ?? []).map((t) => (
            <option key={t} value={t}>
              {KIND[t] ?? words(t)}
            </option>
          ))}
        </NativeSelect>
        <Button variant="outline" size="sm" className="ml-auto" disabled={!rows.length} onClick={downloadJson}>
          <Download /> Download this page
        </Button>
      </div>

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_24rem]')}>
        <div className={cn('grid min-w-0 gap-2 transition-opacity', isFetching && !isPending && 'opacity-70')}>
          {isPending ? (
            <RowsSkeleton rows={8} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Audit trail"
              columns={columns}
              data={rows}
              getRowId={(e) => e.event_id}
              selectedId={id}
              onSelect={open}
              minWidth={900}
              empty={<EmptyState title="Nothing matches">Try a shorter search or another kind of record.</EmptyState>}
            />
          )}
          {total > 0 && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="tabular-nums">
                {(page - 1) * PAGE + 1}–{Math.min(page * PAGE, total)} of {total}
              </span>
              <span className="ml-1 hidden md:inline">· entries can’t be changed or deleted</span>
              <div className="ml-auto flex gap-1">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => void setPage(page - 1 > 1 ? page - 1 : null)}>
                  Newer
                </Button>
                <Button variant="outline" size="sm" disabled={page * PAGE >= total} onClick={() => void setPage(page + 1)}>
                  Older
                </Button>
              </div>
            </div>
          )}
        </div>
        <DetailPanel open={Boolean(selected)} onClose={() => open(undefined)} title={selected ? words(selected.action) : ''} description={selected ? dateTime(selected.timestamp) : undefined}>
          {selected && (
            <div className="grid gap-4">
              <Facts
                items={[
                  ['Record', KIND[selected.resource_type] ?? words(selected.resource_type)],
                  ['Reference', selected.resource_id ? <span key="r" className="font-mono text-xs">{selected.resource_id}</span> : '–'],
                  ['Who', selected.actor_email || '–'],
                  ['Role', selected.actor_role ? words(selected.actor_role) : '–'],
                  ['Site', selected.facility || '–'],
                  ['Entry', <span key="e" className="font-mono text-xs">{selected.event_id}</span>],
                ]}
              />
              {selected.detail && <p className="text-[13px] whitespace-pre-wrap">{selected.detail}</p>}
            </div>
          )}
        </DetailPanel>
      </div>
    </div>
  );
}
