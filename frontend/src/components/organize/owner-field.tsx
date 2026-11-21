'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { PersonSelect } from '@/components/data/pick';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { person } from '@/lib/format';

type Owner = { owner?: string | null; ball_with?: string | null; note?: string | null } | null;
const path = (kind: string, ref: string) => `/api/v1/owners/${encodeURIComponent(kind)}/${encodeURIComponent(ref)}`;

/**
 * Who looks after an order, quote request or quote, and who has the ball. monday has no such column for these, so it is kept in the
 * app. Hidden until the server has it.
 */
export function OwnerField({ kind, refId }: { kind: string; refId: string }) {
  const { hasRole, employeeEmail } = useAuth();
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ owner: '', ball_with: '', note: '' });
  const q = useQuery({
    queryKey: ['owner', kind, refId],
    queryFn: async (): Promise<{ ok: boolean; data: Owner }> => {
      const res = await authFetch(path(kind, refId), { cache: 'no-store' }).catch(() => null);
      return res?.ok ? { ok: true, data: (await res.json()) as Owner } : { ok: false, data: null };
    },
    retry: false,
    staleTime: 60_000,
  });
  const clear = useMutation({
    mutationFn: async () => {
      const res = await authFetch(path(kind, refId), { method: 'DELETE' });
      if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
    },
    onSuccess: () => {
      toast.success('Cleared');
      setEditing(false);
      void client.invalidateQueries({ queryKey: ['owner', kind, refId] });
    },
    onError: (e) => toast.error('Not cleared', { description: e.message }),
  });
  const save = useMutation({
    mutationFn: async () => {
      const res = await authFetch(path(kind, refId), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner: draft.owner || undefined, ball_with: draft.ball_with || undefined, note: draft.note || undefined }),
      });
      const out = (await res.json().catch(() => null)) as { detail?: string } | null;
      if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t save (${res.status}).`);
    },
    onSuccess: () => {
      toast.success('Saved');
      setEditing(false);
      void client.invalidateQueries({ queryKey: ['owner', kind, refId] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  if (!q.data?.ok) return null;
  const o = q.data.data;
  const canEdit = hasRole('OPERATOR');
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px]">
      {editing ? (
        <>
          <PersonSelect
            value={draft.owner}
            onChange={(v) => setDraft({ ...draft, owner: v })}
            none="Who looks after it"
            className="w-auto min-w-44"
          />
          <PersonSelect
            value={draft.ball_with}
            onChange={(v) => setDraft({ ...draft, ball_with: v })}
            none="Waiting on nobody"
            className="w-auto min-w-44"
          />
          <Input
            value={draft.note}
            onChange={(e) => setDraft({ ...draft, note: e.target.value })}
            placeholder="Note"
            className="h-8 w-56"
          />
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
          {(o?.owner || o?.ball_with || o?.note) && (
            <Button size="sm" variant="ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
              Clear it all
            </Button>
          )}
        </>
      ) : (
        <>
          <span>
            <span className="text-muted-foreground">Owner </span>
            {o?.owner ? person(o.owner, employeeEmail) || o.owner : <span className="text-muted-foreground">nobody yet</span>}
          </span>
          {o?.ball_with && (
            <span>
              <span className="text-muted-foreground">Waiting on </span>
              {o.ball_with}
            </span>
          )}
          {o?.note && <span className="text-muted-foreground">{o.note}</span>}
          {canEdit && (
            <button
              type="button"
              className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted"
              onClick={() => {
                setDraft({ owner: o?.owner ?? '', ball_with: o?.ball_with ?? '', note: o?.note ?? '' });
                setEditing(true);
              }}
            >
              Change
            </button>
          )}
        </>
      )}
    </div>
  );
}
