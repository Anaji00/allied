'use client';

import { useDeferredValue, useMemo, useRef, useState } from 'react';
import { parseAsInteger, parseAsString, parseAsStringLiteral, useQueryState } from 'nuqs';
import { useHotkeys } from 'react-hotkeys-hook';
import { ArrowRight, Mail, Plus, Search } from 'lucide-react';
import { DataTable, columnHelper, recordKey } from '@/components/data/data-table';
import { DetailPanel } from '@/components/data/detail-panel';
import { FilterChips } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { day, isoDay, person } from '@/lib/format';
import { cn } from '@/lib/utils';
import { AddShipmentDialog, ForwarderEmailDialog } from './shipment-forms';
import { ShipmentDetail } from './shipment-detail';
import {
  BEFORE_DEPARTURE,
  MODE_LABEL,
  UNDER_WAY,
  flagTone,
  identifier,
  isFinished,
  statusLabel,
  statusTone,
  useShipments,
  type Shipment,
} from './use-shipments';

const VIEWS = ['attention', 'active', 'arriving', 'finished'] as const;
type View = (typeof VIEWS)[number];

const col = columnHelper<Shipment>();

export function ShipmentsView() {
  const { hasRole, employeeEmail } = useAuth();
  const canEdit = hasRole('OPERATOR');
  const wide = useMediaQuery('(min-width: 1280px)');
  const [view, setView] = useQueryState('show', parseAsStringLiteral(VIEWS));
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''));
  // `id` is the link alerts and other pages already use.
  const [id, setId] = useQueryState('id', parseAsInteger);
  const [adding, setAdding] = useState(false);
  const [reading, setReading] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const { data, isPending, error, refetch } = useShipments();
  const all = useMemo(() => data?.shipments ?? [], [data]);
  const statuses = data?.statuses ?? [];

  const groups = useMemo(() => {
    const today = isoDay();
    const weekEnd = isoDay(7);
    const active = all.filter((s) => !isFinished(s));
    return {
      attention: active.filter((s) => s.flags?.length),
      active,
      arriving: active.filter(
        (s) =>
          s.eta_current &&
          s.eta_current >= today &&
          s.eta_current <= weekEnd &&
          (BEFORE_DEPARTURE.includes(s.status) || UNDER_WAY.includes(s.status)),
      ),
      finished: all.filter(isFinished),
    } satisfies Record<View, Shipment[]>;
  }, [all]);

  // A link straight to a shipment opens a view that lists it.
  const linked = id != null ? all.find((s) => s.id === id) : undefined;
  const fallback: View = groups.attention.length || !data ? 'attention' : 'active';
  const current: View =
    view ?? (linked && !groups[fallback].includes(linked) ? (isFinished(linked) ? 'finished' : 'active') : fallback);

  // Filtering trails the typing slightly, so letters appear at once on a slow PC.
  const needle = useDeferredValue(q).trim().toLowerCase();
  const rows = groups[current].filter(
    (s) =>
      !needle ||
      [identifier(s), s.reference, s.forwarder, s.carrier, s.origin, s.destination, s.owner_email]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(needle)),
  );
  const index = rows.findIndex((s) => s.id === id);
  const selected = linked;

  const open = (s?: Shipment) => void setId(s ? s.id : null);
  useListNav(rows, index, open);
  useHotkeys('slash', () => searchRef.current?.focus(), { preventDefault: true });
  useHotkeys('n', () => canEdit && setAdding(true), [canEdit]);

  const columns = col.columns([
    col.display({
      id: 'shipment',
      header: 'Shipment',
      cell: (c) => <span className="font-mono text-xs">{identifier(c.row.original)}</span>,
    }),
    col.display({
      id: 'route',
      header: 'Route',
      meta: { className: 'max-w-64 truncate' },
      cell: (c) => (
        <span className="inline-flex items-center gap-1">
          {c.row.original.origin || '?'} <ArrowRight className="size-3 text-muted-foreground" aria-label="to" /> {c.row.original.destination || '?'}
        </span>
      ),
    }),
    col.accessor('mode', { header: 'By', cell: (c) => MODE_LABEL[c.getValue()] ?? c.getValue() }),
    col.accessor('status', {
      header: 'Status',
      cell: (c) => <ToneBadge tone={statusTone(c.getValue())}>{statusLabel(c.getValue(), c.row.original.mode)}</ToneBadge>,
    }),
    col.accessor('eta_current', {
      header: 'Arrives',
      cell: (c) => {
        const s = c.row.original;
        const moved = s.eta_original && s.eta_current && s.eta_original !== s.eta_current;
        return (
          <span title={moved ? `First promised ${day(s.eta_original)}` : undefined}>
            {day(c.getValue())}
            {moved && <span className="text-warn"> (moved)</span>}
          </span>
        );
      },
    }),
    col.accessor('flags', {
      header: 'Needs',
      cell: (c) => (
        <span className="flex flex-wrap gap-1">
          {(c.getValue() ?? []).map((f) => (
            <ToneBadge key={f} tone={flagTone(f)}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </ToneBadge>
          ))}
        </span>
      ),
    }),
    col.accessor('forwarder', { header: 'Forwarder', meta: { className: 'max-w-40 truncate' }, cell: (c) => c.getValue() || '–' }),
    col.accessor('owner_email', { header: 'Owner', cell: (c) => person(c.getValue(), employeeEmail) || '–' }),
  ]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            type="search"
            value={q}
            onChange={(e) => void setQ(e.target.value || null)}
            placeholder="Container, booking, port…"
            aria-label="Search shipments"
            className="pl-8"
          />
        </div>
        <FilterChips
          label="Show"
          value={current}
          onChange={(v) => void setView(v)}
          options={[
            { value: 'attention', label: 'Needs attention', count: data ? groups.attention.length : undefined, tone: 'warn' },
            { value: 'active', label: 'All active', count: data ? groups.active.length : undefined },
            { value: 'arriving', label: 'Arriving this week', count: data ? groups.arriving.length : undefined },
            { value: 'finished', label: 'Finished', count: data ? groups.finished.length : undefined },
          ]}
        />
        {canEdit && (
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setReading(true)}>
              <Mail /> Read forwarder email
            </Button>
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus /> Add shipment <Kbd className="bg-transparent text-current opacity-60">N</Kbd>
            </Button>
          </div>
        )}
      </div>

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="grid min-w-0 gap-2">
          {isPending ? (
            <RowsSkeleton rows={8} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : (
            <DataTable
              label="Shipments"
              columns={columns}
              data={rows}
              getRowId={(s) => String(s.id)}
              recordName={(s) => recordKey('shipment', s.id)}
              selectedId={id != null ? String(id) : null}
              onSelect={open}
              minWidth={900}
              empty={
                <EmptyState title={needle ? 'No shipments match' : current === 'attention' ? 'Nothing needs attention' : 'No shipments here'}>
                  {needle
                    ? 'Try a shorter search or another filter.'
                    : current === 'attention'
                      ? 'Every active shipment is on schedule and up to date.'
                      : 'Add a shipment, or read a forwarder email to update one.'}
                </EmptyState>
              }
            />
          )}
          {rows.length > 0 && (
            <p className="hidden text-xs text-muted-foreground md:block">
              Late, slipping, quiet or near port charges count as needing attention · <Kbd>J</Kbd>
              <Kbd>K</Kbd> move · <Kbd>/</Kbd> search
            </p>
          )}
          {id != null && data && !linked && (
            <p role="status" className="text-sm text-warn">
              That shipment isn’t on the list any more.
            </p>
          )}
        </div>

        <DetailPanel
          open={Boolean(selected)}
          onClose={() => open(undefined)}
          recordName={selected ? recordKey('shipment', selected.id) : undefined}
          title={selected ? <span className="font-mono">{identifier(selected)}</span> : ''}
          description={selected ? `${selected.origin || '?'} to ${selected.destination || '?'}` : undefined}
        >
          {selected && <ShipmentDetail key={selected.id} shipment={selected} statuses={statuses} canEdit={canEdit} />}
        </DetailPanel>
      </div>

      {canEdit && (
        <>
          <AddShipmentDialog open={adding} onOpenChange={setAdding} statuses={statuses} onAdded={(sid) => void setId(sid).then(() => setView('active'))} />
          <ForwarderEmailDialog open={reading} onOpenChange={setReading} onApplied={(sid) => void setId(sid).then(() => setView(null))} />
        </>
      )}
    </div>
  );
}
