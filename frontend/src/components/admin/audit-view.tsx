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
import { authFetch } from '@/lib/api';
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
  /** A plain sentence from the server, where it has one. */
  text?: string | null;
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
  MONDAY_EVENT: 'monday changes',
  TEST_RESOURCE: 'Test entries',
  SAGE_PO_CSV: 'Sage purchase order files',
  SAGE_SO_CSV: 'Sage sales order files',
  ITEM_BALL: 'Who is waiting on whom',
  ITEM_UPDATE: 'Comments and updates',
};

function tone(action: string): Tone {
  if (/FLAG|NCR|HALT|REJECT|FAIL|DISABLE/.test(action)) return 'bad';
  if (/APPROV|EXPORT|CREATE|RESOLV|DISPATCH/.test(action)) return 'ok';
  return 'info';
}

const nameOf = (email: string | null) =>
  email
    ? email
        .split('@')[0]
        .replace(/[._]+/g, ' ')
        .replace(/\d+$/, '')
        .replace(/\b\w/g, (c) => c.toUpperCase())
        .trim() || email
    : 'The system';

/** One plain sentence for a row: the server's own where it sends one, otherwise who, what and which record. Never raw data. */
const SECURITY = /refresh (refused|reuse)/i;
const isSecurity = (e: Entry) => SECURITY.test(`${e.text ?? ''} ${e.detail ?? ''} ${e.action ?? ''}`);

function sentence(e: Entry) {
  const raw = `${e.text ?? ''} ${e.detail ?? ''} ${e.action ?? ''}`;
  if (/refresh reuse/i.test(raw))
    return 'A sign-in token was used twice; every session of this person was ended. Usually a stale browser tab.';
  if (/refresh refused/i.test(raw))
    return 'A sign-in renewal was refused because that sign-in had already ended. Usually a stale browser tab.';
  if (e.text) return e.text;
  const what = KIND[(e.resource_type ?? '').toUpperCase()] ?? words(e.resource_type);
  return `${nameOf(e.actor_email)}: ${words(e.action).toLowerCase()}, ${what.toLowerCase()}${e.resource_id ? ` ${e.resource_id}` : ''}.`;
}
const looksLikeData = (t: string | null | undefined) => Boolean(t && /^\s*[{[]/.test(t));

const col = columnHelper<Entry>();
const columns = col.columns([
  col.accessor('timestamp', { header: 'When', cell: (c) => <span className="whitespace-nowrap">{dateTime(c.getValue())}</span> }),
  col.accessor('action', {
    header: 'What happened',
    meta: { className: 'max-w-[44rem]' },
    cell: (c) => (
      <span className="flex items-start gap-2">
        <ToneBadge tone={tone(c.getValue())}>{words(c.getValue())}</ToneBadge>
        <span className="text-[13px] text-pretty">{sentence(c.row.original)}</span>
      </span>
    ),
  }),
  col.accessor('actor_email', { header: 'Who', meta: { className: 'max-w-56 truncate' }, cell: (c) => nameOf(c.getValue()) }),
]);

export function AuditView() {
  const { hasRole } = useAuth();
  const wide = useMediaQuery('(min-width: 1280px)');
  const [kind, setKind] = useQueryState('kind', parseAsString.withDefault(''));
  const [action, setAction] = useQueryState('action', parseAsString.withDefault(''));
  const [startDate, setStartDate] = useQueryState('from', parseAsString.withDefault(''));
  const [endDate, setEndDate] = useQueryState('to', parseAsString.withDefault(''));
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''));
  const [page, setPage] = useQueryState('page', parseAsInteger.withDefault(1));
  const [id, setId] = useQueryState('entry');
  const [draft, setDraft] = useState(q);
  const [exporting, setExporting] = useState(false);
  const [showSecurity, setShowSecurity] = useState(false);

  useEffect(() => {
    if (draft === q) return undefined;
    const t = setTimeout(() => {
      void setQ(draft || null);
      void setPage(null);
    }, 300);
    return () => clearTimeout(t);
  }, [draft, q, setQ, setPage]);

  const buildParams = (limit: number, offset: number) => {
    const p = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (kind) p.set('resource_type', kind);
    if (action) p.set('action', action);
    if (startDate) p.set('start_date', startDate);
    if (endDate) p.set('end_date', endDate);
    if (q) p.set('q', q);
    return p.toString();
  };

  const { data, isPending, isFetching, error, refetch } = useQuery({
    queryKey: ['audit', kind, action, startDate, endDate, q, page],
    queryFn: async () => {
      const res = await authFetch(`/api/v1/audit?${buildParams(PAGE, (page - 1) * PAGE)}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`Couldn’t load the audit trail (${res.status}).`);
      return (await res.json()) as Page;
    },
    placeholderData: keepPreviousData,
    enabled: hasRole('MANAGER'),
  });
  // Sign-in security events (a renewal refused, or a token used twice) are kept out of the default list so the history stays readable.
  // They are never lost: the toggle beside the filters shows them with a plain sentence.
  const rows = (data?.entries ?? []).filter((e) => showSecurity || !isSecurity(e));
  const total = data?.total ?? 0;
  const index = rows.findIndex((e) => e.event_id === id);
  const selected = rows[index];
  const open = (e?: Entry) => void setId(e ? e.event_id : null);
  useListNav(rows, index, open);

  async function downloadJson() {
    setExporting(true);
    try {
      const res = await authFetch(`/api/v1/audit?${buildParams(500, 0)}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`Couldn’t export (${res.status}).`);
      const full = (await res.json()) as Page;
      const blob = new Blob([JSON.stringify(full.entries, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `allied_audit_${new Date().toISOString().slice(0, 10)}${kind ? `_${kind.toLowerCase()}` : ''}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } finally {
      setExporting(false);
    }
  }

  if (!hasRole('MANAGER')) return <EmptyState title="Managers only">The audit trail is open to managers and admins.</EmptyState>;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Order, person, or words in the details"
            aria-label="Search the audit trail"
            className="pl-8"
          />
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
              {KIND[t.toUpperCase()] ?? words(t)}
            </option>
          ))}
        </NativeSelect>
        <Input
          type="date"
          aria-label="From date"
          value={startDate}
          onChange={(e) => {
            void setStartDate(e.target.value || null);
            void setPage(null);
          }}
          className="w-auto"
        />
        <Input
          type="date"
          aria-label="To date"
          value={endDate}
          onChange={(e) => {
            void setEndDate(e.target.value || null);
            void setPage(null);
          }}
          className="w-auto"
        />
        <Input
          value={action}
          onChange={(e) => {
            void setAction(e.target.value.toUpperCase() || null);
            void setPage(null);
          }}
          placeholder="Action (e.g. LOGIN)"
          aria-label="Filter by action"
          className="w-36"
        />
        <label className="inline-flex items-center gap-1.5 text-[13px]">
          <input type="checkbox" className="size-4" checked={showSecurity} onChange={(e) => setShowSecurity(e.target.checked)} />
          Show sign-in security events
        </label>
        <Button variant="outline" size="sm" className="ml-auto" disabled={!rows.length || exporting} onClick={() => void downloadJson()}>
          <Download /> {exporting ? 'Exporting…' : 'Export filtered'}
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
        <DetailPanel
          open={Boolean(selected)}
          onClose={() => open(undefined)}
          title={selected ? words(selected.action) : ''}
          description={selected ? dateTime(selected.timestamp) : undefined}
        >
          {selected && (
            <div className="grid gap-4">
              <Facts
                items={[
                  ['Record', KIND[(selected.resource_type ?? '').toUpperCase()] ?? words(selected.resource_type)],
                  [
                    'Reference',
                    selected.resource_id ? (
                      <span key="r" className="font-mono text-xs">
                        {selected.resource_id}
                      </span>
                    ) : (
                      '–'
                    ),
                  ],
                  ['Who', selected.actor_email || '–'],
                  ['Role', selected.actor_role ? words(selected.actor_role) : '–'],
                  ['Site', selected.facility || '–'],
                  [
                    'Entry',
                    <span key="e" className="font-mono text-xs">
                      {selected.event_id}
                    </span>,
                  ],
                ]}
              />
              <p className="text-[13px]">{sentence(selected)}</p>
              {selected.detail &&
                (looksLikeData(selected.detail) ? (
                  <details className="text-xs text-muted-foreground">
                    <summary className="cursor-pointer">Technical details</summary>
                    <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted/50 p-2 whitespace-pre-wrap">{selected.detail}</pre>
                  </details>
                ) : (
                  <p className="text-[13px] whitespace-pre-wrap">{selected.detail}</p>
                ))}
            </div>
          )}
        </DetailPanel>
      </div>
    </div>
  );
}
