'use client';

import { useMemo, useState } from 'react';
import { parseAsString, parseAsStringLiteral, useQueryState } from 'nuqs';
import { Plus, Search } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { DetailPanel } from '@/components/data/detail-panel';
import { FilterChips, SectionTabs } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { date } from '@/lib/format';
import { cn } from '@/lib/utils';
import { MtrCard, NcrDetail, RegisterCertDialog } from './quality-parts';
import { NCR_STATUS, useMTR, useNCRs, type NCR } from './use-quality';

const TABS = ['holds', 'certificates'] as const;
const STATUSES = ['QUARANTINED', 'REJECTED', 'CONCESSION_APPROVED', 'ALL'] as const;

export function QualityView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('holds'));
  const [heat, setHeat] = useQueryState('heat', parseAsString.withDefault(''));
  const lookUp = (h: string) => {
    void setHeat(h);
    void setTab('certificates');
  };
  return (
    <div className="grid gap-4">
      <SectionTabs
        label="Quality sections"
        value={tab}
        onChange={(t) => void setTab(t === 'holds' ? null : t)}
        tabs={[
          ['holds', 'Quality holds'],
          ['certificates', 'Mill certificates'],
        ]}
      />
      {tab === 'holds' ? <Holds onLookUp={lookUp} /> : <Certificates heat={heat} setHeat={(h) => void setHeat(h || null)} />}
    </div>
  );
}

const col = columnHelper<NCR>();

function Holds({ onLookUp }: { onLookUp: (heat: string) => void }) {
  const wide = useMediaQuery('(min-width: 1280px)');
  const [status, setStatus] = useQueryState('status', parseAsStringLiteral(STATUSES).withDefault('QUARANTINED'));
  const [id, setId] = useQueryState('ncr');
  const { data, isPending, error, refetch } = useNCRs();
  const list = useMemo(() => data ?? [], [data]);
  const count = (s: string) => list.filter((n) => n.status === s).length;
  const rows = status === 'ALL' ? list : list.filter((n) => n.status === status);
  const index = rows.findIndex((n) => n.ncr_id === id);
  const selected = list.find((n) => n.ncr_id === id);

  const open = (n?: NCR) => void setId(n ? n.ncr_id : null);
  useListNav(rows, index, open);

  const columns = col.columns([
    col.accessor('heat_number', { header: 'Heat', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
    col.accessor('item_id', { header: 'Part', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
    col.accessor('supplier', { header: 'Supplier', meta: { className: 'max-w-48 truncate' } }),
    col.accessor('failure_reasons', {
      header: 'Why',
      meta: { className: 'max-w-80 truncate' },
      cell: (c) => c.getValue()[0] ?? '–',
    }),
    col.accessor('status', {
      header: 'Status',
      cell: (c) => {
        const s = NCR_STATUS[c.getValue()] ?? { label: c.getValue(), tone: 'neutral' as const };
        return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
      },
    }),
    col.accessor('created_at', { header: 'Raised', cell: (c) => date(c.getValue()) }),
  ]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={status}
          onChange={(v) => {
            void setStatus(v === 'QUARANTINED' ? null : v);
            open(undefined);
          }}
          options={[
            { value: 'QUARANTINED', label: 'On hold', count: data ? count('QUARANTINED') : undefined, tone: 'bad' },
            { value: 'REJECTED', label: 'Rejected', count: data ? count('REJECTED') : undefined },
            { value: 'CONCESSION_APPROVED', label: 'Concessions', count: data ? count('CONCESSION_APPROVED') : undefined },
            { value: 'ALL', label: 'All', count: data ? list.length : undefined },
          ]}
        />
        <p className="text-xs text-muted-foreground sm:ml-auto">Lots that failed a chemistry or hardness limit. Each needs a decision.</p>
      </div>

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="grid min-w-0 gap-2">
          {isPending ? (
            <RowsSkeleton rows={6} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Quality holds"
              columns={columns}
              data={rows}
              getRowId={(n) => n.ncr_id}
              selectedId={id}
              onSelect={open}
              minWidth={760}
              empty={
                <EmptyState title={status === 'QUARANTINED' ? 'Nothing is on hold' : 'No records here'}>
                  {status === 'QUARANTINED' && 'A lot goes on hold when its mill certificate is out of limits.'}
                </EmptyState>
              }
            />
          )}
          {rows.length > 0 && (
            <p className="hidden text-xs text-muted-foreground md:block">
              <Kbd>J</Kbd>
              <Kbd>K</Kbd> move · <Kbd>Esc</Kbd> close
            </p>
          )}
        </div>
        <DetailPanel
          open={Boolean(id)}
          onClose={() => open(undefined)}
          title={selected ? `Heat ${selected.heat_number}` : (id ?? '')}
          description={id ?? undefined}
        >
          {id && <NcrDetail key={id} id={id} onLookUp={onLookUp} />}
        </DetailPanel>
      </div>
    </div>
  );
}

function Certificates({ heat, setHeat }: { heat: string; setHeat: (heat: string) => void }) {
  const { hasRole } = useAuth();
  const [draft, setDraft] = useState(heat);
  const [registering, setRegistering] = useState(false);
  const { data: mtr, isFetching, error, refetch } = useMTR(heat.trim());

  return (
    <div className="grid max-w-3xl gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <form
          className="flex w-full gap-2 sm:w-auto"
          onSubmit={(e) => {
            e.preventDefault();
            setHeat(draft.trim());
          }}
        >
          <div className="relative flex-1 sm:w-72">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Heat number, e.g. HT-300XL-8819"
              aria-label="Heat number"
              className="pl-8"
            />
          </div>
          <Button type="submit" variant="outline" size="sm" className="h-8" disabled={!draft.trim()}>
            Look up
          </Button>
        </form>
        {hasRole('OPERATOR') && (
          <Button size="sm" className="sm:ml-auto" onClick={() => setRegistering(true)}>
            <Plus /> Register a certificate
          </Button>
        )}
      </div>

      {!heat ? (
        <EmptyState title="Look up a heat number">
          See a mill certificate’s chemistry and test results, and whether they’re within the part’s limits.
        </EmptyState>
      ) : isFetching && !mtr ? (
        <RowsSkeleton rows={4} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : mtr ? (
        <MtrCard mtr={mtr} />
      ) : (
        <EmptyState title={`No certificate on file for ${heat}`}>
          Check the number on the supplier’s paperwork{hasRole('OPERATOR') ? ', or register the certificate.' : '.'}
        </EmptyState>
      )}

      <RegisterCertDialog
        open={registering}
        onOpenChange={setRegistering}
        onRegistered={(m) => {
          setDraft(m.heat_number);
          setHeat(m.heat_number);
        }}
      />
    </div>
  );
}
