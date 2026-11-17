'use client';

import { useAppPeople } from '@/components/data/pick';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { plain, words } from '@/lib/format';
import type { ItemType } from './updates-panel';

export type Ball = {
  item_type: ItemType;
  item_ref: string;
  label?: string;
  owner_email?: string | null;
  owner_name?: string | null;
  ball_with: string;
  ball_label?: string;
  ball_person?: string;
  ball_name?: string;
  note?: string | null;
  due_at?: string | null;
  overdue?: boolean;
  days_waiting?: number;
  level?: number;
  status?: string;
  resolved?: boolean;
  derived?: boolean;
  history?: {
    at: string;
    by: string;
    from?: string;
    to?: string;
    note?: string;
  }[];
};
type Person = { email: string; name: string };

export const HOLDERS: [string, string, boolean][] = [
  // [value, label, a person on our team (picked from the list) rather than a name typed in]
  ['us', 'Us', true],
  ['engineer', 'Engineering', true],
  ['manager', 'A manager', true],
  ['vendor', 'The vendor', false],
  ['customer', 'The customer', false],
  ['carrier', 'The carrier', false],
  ['other', 'Someone else', false],
];

const STATUS: Record<string, { label: string; tone: Tone }> = {
  'on track': { label: 'On track', tone: 'ok' },
  reminded: { label: 'Reminded', tone: 'info' },
  'owner told': { label: 'Owner told', tone: 'warn' },
  'leadership told': { label: 'Leadership told', tone: 'bad' },
  done: { label: 'Done', tone: 'neutral' },
};
export const statusOf = (b: Pick<Ball, 'status' | 'level'>) =>
  STATUS[b.status ?? ''] ?? { label: plain(b.status ?? '') || words(b.status ?? ''), tone: 'neutral' as Tone };

const BASE = '/api/v1/ball';

export async function ballCall<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await authFetch(`${BASE}${path}`, {
    method,
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    const err = new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json().catch(() => null)) as T;
}

export const waitText = (b: Ball) =>
  `${b.days_waiting ?? 0} working ${b.days_waiting === 1 ? 'day' : 'days'}${b.due_at ? ` · due ${b.due_at}` : ''}`;

/** Hands the item to someone, or changes what it is waiting for. */
export function HandOver({
  itemType,
  itemRef,
  current,
  onClose,
}: {
  itemType: ItemType;
  itemRef: string;
  current?: Ball | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [kind, setKind] = useState(current?.ball_with ?? 'us');
  const [person, setPerson] = useState(current?.ball_person ?? '');
  const [owner, setOwner] = useState(current?.owner_email ?? '');
  const [note, setNote] = useState(current?.note ?? '');
  const [due, setDue] = useState(current?.due_at?.slice(0, 10) ?? '');
  const team = HOLDERS.find(([v]) => v === kind)?.[2] ?? false;
  const list = useAppPeople();

  const save = useMutation({
    mutationFn: () =>
      ballCall('', 'PUT', {
        item_type: itemType,
        item_ref: itemRef,
        ball_with: kind,
        ball_person: person,
        owner: owner || undefined,
        note: note.trim() || undefined,
        due: due || undefined,
      }),
    onSuccess: () => {
      toast.success('Handed over');
      void client.invalidateQueries({ queryKey: ['ball'] });
      void client.invalidateQueries({ queryKey: ['updates'] });
      onClose();
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Who is it waiting on?</DialogTitle>
          <DialogDescription>The person gets told, and the wait starts again from today.</DialogDescription>
        </DialogHeader>
        <form
          id="hand-over"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (person.trim()) save.mutate();
          }}
        >
          <Field label="It is with">
            <NativeSelect
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setPerson('');
              }}
            >
              {HOLDERS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label={team ? 'Who' : 'Name'}>
            {team ? (
              <NativeSelect value={person} onChange={(e) => setPerson(e.target.value)} required>
                <option value="">{list.isPending ? 'Loading…' : 'Choose a person'}</option>
                {(list.data ?? []).map((p) => (
                  <option key={p.email || p.name} value={p.email} disabled={!p.email}>
                    {p.email ? p.name : `${p.name} (no email yet)`}
                  </option>
                ))}
              </NativeSelect>
            ) : (
              <Input value={person} onChange={(e) => setPerson(e.target.value)} required placeholder="Company or person" />
            )}
          </Field>
          <Field label="Waiting for (optional)">
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: the signed drawing" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Wanted by (optional)">
              <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
            </Field>
            <Field label="Owner (optional)" hint="The one person who answers for it.">
              <NativeSelect value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="">Keep as it is</option>
                {(list.data ?? []).map((p) => (
                  <option key={p.email || p.name} value={p.email} disabled={!p.email}>
                    {p.email ? p.name : `${p.name} (no email yet)`}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="hand-over" disabled={save.isPending || !person.trim()}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Pass to someone
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** "It is waiting on …" for one item, with Pass to someone and Done. Says nothing until the server has it. */
export function BallStrip({ itemType, itemRef }: { itemType: ItemType; itemRef: string }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [handing, setHanding] = useState(false);
  const canWrite = hasRole('OPERATOR');
  const q = useQuery({
    queryKey: ['ball', itemType, itemRef],
    queryFn: async (): Promise<{ ball: Ball | null } | null> => {
      try {
        return {
          ball: await ballCall<Ball | null>(`?item_type=${itemType}&item_ref=${encodeURIComponent(itemRef)}`),
        };
      } catch (e) {
        const status = (e as { status?: number }).status;
        if (status === 404 || (status && status >= 500)) return null;
        throw e;
      }
    },
    retry: false,
  });
  const done = useMutation({
    mutationFn: () => ballCall('/resolve', 'POST', { item_type: itemType, item_ref: itemRef }),
    onSuccess: () => {
      toast.success('Marked as done');
      void client.invalidateQueries({ queryKey: ['ball'] });
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  if (q.isPending || q.isError || !q.data) return null;
  const b = q.data.ball;
  const open = b && !b.resolved;
  const st = b ? statusOf(b) : null;

  return (
    <div className="grid gap-2 rounded-md border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {open && b ? (
          <>
            <p className="text-sm">
              It is waiting on <strong>{b.ball_name || b.ball_person}</strong>
              {b.ball_label ? `, ${b.ball_label}` : ''} · {waitText(b)}
            </p>
            {st && <ToneBadge tone={st.tone}>{st.label}</ToneBadge>}
            {b.overdue && <ToneBadge tone="bad">Late</ToneBadge>}
            {b.derived && <ToneBadge>Filled in by the app</ToneBadge>}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            {b?.resolved ? 'Nobody is waiting on this any more.' : 'Nobody is waiting on this.'}
          </p>
        )}
        {canWrite && (
          <span className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setHanding(true)}>
              {open ? 'Pass to someone' : 'Who is it waiting on?'}
            </Button>
            {open && (
              <Button size="sm" variant="ghost" disabled={done.isPending} onClick={() => done.mutate()}>
                Done
              </Button>
            )}
          </span>
        )}
      </div>
      {open && b && (b.note || b.owner_name) && (
        <p className="text-xs text-muted-foreground">
          {b.note && <>Waiting for: {b.note}. </>}
          {b.owner_name && <>Owner: {b.owner_name}.</>}
        </p>
      )}
      {handing && <HandOver itemType={itemType} itemRef={itemRef} current={open ? b : null} onClose={() => setHanding(false)} />}
    </div>
  );
}
