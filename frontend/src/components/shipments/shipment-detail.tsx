'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { Field, NativeSelect, PanelHeading } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { api, ok } from '@/lib/api/client';
import { optimistic } from '@/lib/optimistic';
import { day, dateTime, person } from '@/lib/format';
import {
  MODE_LABEL,
  SOURCE_LABEL,
  flagTone,
  statusLabel,
  statusOptions,
  statusTone,
  type Shipment,
} from './use-shipments';

export function ShipmentDetail({ shipment: s, statuses, canEdit }: { shipment: Shipment; statuses: string[]; canEdit: boolean }) {
  const { employeeEmail } = useAuth();
  const moved = s.eta_original && s.eta_current && s.eta_original !== s.eta_current;
  const events = s.events ?? [];

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <ToneBadge tone={statusTone(s.status)}>{statusLabel(s.status, s.mode)}</ToneBadge>
        {(s.flags ?? []).map((f) => (
          <ToneBadge key={f} tone={flagTone(f)}>
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </ToneBadge>
        ))}
      </div>

      {s.flags?.includes('port free time ending') && (
        <p role="alert" className="rounded-md bg-bad-soft p-3 text-sm text-bad">
          Free time at the port ends {day(s.free_time_ends, { weekday: true })}. After that the port charges storage every day
          until it is collected.
        </p>
      )}

      <Facts
        items={[
          ['Arrives', <span key="a">{day(s.eta_current, { weekday: true })}{moved && <span className="text-muted-foreground"> · first promised {day(s.eta_original)}</span>}</span>],
          ['Leaves', day(s.etd, { weekday: true })],
          ['Port free time ends', day(s.free_time_ends)],
          ['By', MODE_LABEL[s.mode] ?? s.mode],
          ['Forwarder', s.forwarder || '–'],
          ['Carrier', s.carrier || '–'],
          ['Booking ref', s.reference ? <span key="r" className="font-mono text-xs">{s.reference}</span> : '–'],
          [s.mode === 'AIR' ? 'Air waybill' : 'Container', <span key="c" className="font-mono text-xs">{(s.mode === 'AIR' ? s.awb : s.container_no) || '–'}</span>],
          ['Owner', person(s.owner_email, employeeEmail) || '–'],
          ...(s.project_item_id
            ? ([['Project', <Link key="p" className="underline underline-offset-2" href={`/projects?project=${encodeURIComponent(s.project_item_id)}`}>Open the project</Link>]] as [string, React.ReactNode][])
            : []),
          ['Last update', dateTime(s.last_update_at)],
        ]}
      />

      {canEdit && <RecordChange key={`${s.id}-${s.status}-${s.last_update_at}`} shipment={s} statuses={statuses} />}

      <div className="grid gap-2">
        <PanelHeading>History</PanelHeading>
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">No updates recorded yet.</p>
        ) : (
          <ol className="grid gap-3 border-l pl-4">
            {events.map((ev, i) => (
              <li key={`${ev.at}-${i}`} className="relative grid gap-0.5 text-[13px]">
                <span className="absolute top-1.5 -left-[21px] size-2 rounded-full bg-muted-foreground/60" aria-hidden />
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{statusLabel(ev.status, s.mode)}</span>
                  <span className="text-xs text-muted-foreground">{dateTime(ev.at)}</span>
                </div>
                {ev.description && <p className="text-pretty">{ev.description}</p>}
                <span className="text-xs text-muted-foreground">
                  {SOURCE_LABEL[ev.source ?? ''] ?? ev.source}
                  {ev.eta && ` · expected ${day(ev.eta)}`}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function RecordChange({ shipment: s, statuses }: { shipment: Shipment; statuses: string[] }) {
  const client = useQueryClient();
  const [status, setStatus] = useState(s.status);
  const [eta, setEta] = useState('');
  const [freeTime, setFreeTime] = useState('');
  const [note, setNote] = useState('');

  const changes = {
    status: status !== s.status ? status : undefined,
    eta: eta || undefined,
    free_time_ends: freeTime || undefined,
    note: note.trim() || undefined,
  };
  const changed = Object.values(changes).some(Boolean);

  const now = () => new Date().toISOString();
  const instant = optimistic<void>(client, ['shipments'], (old: { statuses: string[]; shipments: Shipment[] }) => ({
    ...old,
    shipments: old.shipments.map((x) =>
      x.id !== s.id
        ? x
        : {
            ...x,
            status: changes.status ?? x.status,
            eta_current: changes.eta ?? x.eta_current,
            free_time_ends: changes.free_time_ends ?? x.free_time_ends,
            last_update_at: now(),
            events: [
              { at: now(), status: changes.status ?? x.status, description: changes.note ?? null, source: 'manual', eta: changes.eta ?? null },
              ...(x.events ?? []),
            ],
          },
    ),
  }));
  const save = useMutation({
    mutationFn: async () =>
      ok(api.PATCH('/api/v1/shipments/{shipment_id}', { params: { path: { shipment_id: s.id } }, body: changes as never })),
    onMutate: instant.onMutate,
    onError: (e, _v, context) => {
      instant.rollback(context);
      toast.error('The change wasn’t saved', { description: e.message });
    },
    onSettled: instant.onSettled,
  });

  return (
    <form
      className="grid gap-3 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (changed) save.mutate();
      }}
    >
      <PanelHeading>Record a change</PanelHeading>
      <Field label="Status">
        <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)}>
          {statusOptions(statuses.length ? statuses : [s.status]).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="New arrival date">
          <Input type="date" value={eta} onChange={(e) => setEta(e.target.value)} />
        </Field>
        <Field label="Port free time ends">
          <Input type="date" value={freeTime} onChange={(e) => setFreeTime(e.target.value)} />
        </Field>
      </div>
      <Field label="Note" hint="What happened, in a sentence. It goes in the history.">
        <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <Button type="submit" size="sm" className="justify-self-start" disabled={!changed || save.isPending}>
        {save.isPending && <Loader2 className="animate-spin" />}
        Save change
      </Button>
    </form>
  );
}
