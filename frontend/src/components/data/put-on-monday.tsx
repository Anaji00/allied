'use client';

import { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { authFetch } from '@/lib/api';

type Mapping = {
  staged?: boolean;
  dry_run?: boolean;
  board?: string;
  group?: string;
  name?: string;
  columns?: { fact: string; column: string; value: unknown }[];
  left_out?: { fact: string; reason: string }[];
  message?: string;
};

/**
 * Put this job, request, order, purchase order or shipment on a monday board as a row. It shows what would be filled in and what
 * is left out first (a preview, nothing saved), then stages it as a change waiting for monday. It goes to monday only when a manager
 * sends the changes, like every other edit.
 */
function remembered(k: 'board' | 'group') {
  try {
    return localStorage.getItem(`allied.tomonday.${k}`) ?? '';
  } catch {
    return '';
  }
}

export function PutOnMonday({ kind, refId, title, onClose }: { kind: string; refId: string; title: string; onClose: () => void }) {
  const client = useQueryClient();
  // The last board and group used are offered first, so the usual case is one tap.
  const [board, setBoard] = useState(() => remembered('board'));
  const [group, setGroup] = useState(() => remembered('group'));
  useEffect(() => {
    try {
      if (board) localStorage.setItem('allied.tomonday.board', board);
      localStorage.setItem('allied.tomonday.group', group);
    } catch {
      // Private windows refuse storage; nothing is remembered.
    }
  }, [board, group]);
  const base = `/api/v1/actions/${encodeURIComponent(kind)}/${encodeURIComponent(refId)}/to-monday`;
  const boards = useQuery({
    queryKey: ['boards', 'for-monday'],
    queryFn: async (): Promise<{ board_id: string; name: string }[]> => {
      const res = await authFetch('/api/v1/boards', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as { board_id: string; name: string }[]) : [];
    },
    retry: false,
  });
  // The board's groups, so the row can go into the right one.
  const groups = useQuery({
    queryKey: ['board-groups', board],
    queryFn: async (): Promise<{ id: string; title: string }[]> => {
      const res = await authFetch(`/api/v1/board-edit/boards/${encodeURIComponent(board)}/columns`, { cache: 'no-store' }).catch(
        () => null,
      );
      return res?.ok ? (((await res.json()) as { groups?: { id: string; title: string }[] }).groups ?? []) : [];
    },
    enabled: Boolean(board),
    retry: false,
  });
  const preview = useQuery({
    queryKey: ['to-monday', kind, refId, board, group],
    queryFn: async (): Promise<Mapping | { error: string }> => {
      const res = await authFetch(base, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ board_id: board, ...(group ? { group } : {}), dry_run: true }),
      });
      const body = (await res.json().catch(() => null)) as (Mapping & { detail?: string }) | null;
      if (!res.ok) return { error: typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).` };
      return body ?? {};
    },
    enabled: Boolean(board),
    retry: false,
  });
  const stage = useMutation({
    mutationFn: async () => {
      const res = await authFetch(base, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ board_id: board, ...(group ? { group } : {}) }),
      });
      const body = (await res.json().catch(() => null)) as (Mapping & { detail?: string }) | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      return body ?? {};
    },
    onSuccess: () => {
      toast.success('Saved, waiting to go to monday', { description: 'You can see it as a change on the board. A manager sends it.' });
      void client.invalidateQueries({ queryKey: ['monday-push'] });
      onClose();
    },
    onError: (e) => toast.error('It wasn’t saved', { description: e.message }),
  });

  const p = preview.data && !('error' in preview.data) ? preview.data : null;
  const error = preview.data && 'error' in preview.data ? preview.data.error : null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[88svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Put on a monday board</DialogTitle>
          <DialogDescription>
            {title}. Choose a board to see what would be filled in. Nothing goes to monday until a manager sends it.
          </DialogDescription>
        </DialogHeader>
        <Field label="Which board?">
          <NativeSelect
            value={board}
            onChange={(e) => {
              setBoard(e.target.value);
              setGroup('');
            }}
          >
            <option value="">Choose a board…</option>
            {(boards.data ?? []).map((b) => (
              <option key={b.board_id} value={b.board_id}>
                {b.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        {board && (groups.data ?? []).length > 0 && (
          <Field label="Which group?">
            <NativeSelect value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="">The first group</option>
              {(groups.data ?? []).map((g) => (
                <option key={g.id} value={g.id}>
                  {g.title}
                </option>
              ))}
            </NativeSelect>
          </Field>
        )}
        {board && preview.isPending && <Loader2 className="size-5 animate-spin text-muted-foreground" />}
        {error && <p className="rounded-md bg-bad-soft px-3 py-2 text-[13px] text-bad">{error}</p>}
        {p && (
          <div className="grid gap-2 text-[13px]">
            <p className="font-medium">New row: {p.name ?? title}</p>
            {(p.columns ?? []).length > 0 && (
              <ul className="grid gap-0.5">
                {p.columns!.map((c) => (
                  <li key={c.column} className="flex gap-2">
                    <span className="w-36 shrink-0 text-muted-foreground">{c.column}</span>
                    <span className="min-w-0 rounded-sm bg-ok-soft px-1.5 text-ok">{String(c.value ?? '')}</span>
                  </li>
                ))}
              </ul>
            )}
            {(p.left_out ?? []).length > 0 && (
              <div className="grid gap-0.5 text-muted-foreground">
                <span className="text-xs font-medium">Left out (no column of that name on this board)</span>
                {p.left_out!.map((l) => (
                  <span key={l.fact}>
                    {l.fact}: {l.reason}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!p || stage.isPending} onClick={() => stage.mutate()}>
            {stage.isPending && <Loader2 className="animate-spin" />}
            Save for monday
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
