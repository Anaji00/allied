'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ChevronRight } from 'lucide-react';
import { useMondayStatus } from '@/components/admin/monday-changes';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Row = {
  id: number;
  kind: string;
  item?: string | null;
  status: 'STAGED' | 'FAILED' | 'CONFLICT' | string;
  changes?: { column: string; base?: unknown; now?: unknown; becomes?: unknown }[];
  comment?: string;
  new_row?: { name?: string };
  notes?: string[];
};

const text = (v: unknown) => (v === null || v === undefined || v === '' ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
const TONE: Record<string, Tone> = { STAGED: 'info', PUSHING: 'info', FAILED: 'bad', CONFLICT: 'warn' };
const WORD: Record<string, string> = { STAGED: 'Waiting', PUSHING: 'Sending now', FAILED: 'Failed', CONFLICT: 'Changed on monday too' };

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await authFetch(path, {
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
    throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
  }
  return (await res.json()) as T;
}

/**
 * This board's waiting changes, read like a git diff: what monday showed when the edit was made (red), what it becomes
 * (green), and what monday shows now if it moved. A change monday has since moved on is held, never overwritten:
 * re-base keeps your edit on top of monday's current value. Nothing is sent while monday is read-only.
 */
export function BoardChanges({ boardId }: { boardId: string }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const canPush = useMondayStatus().data?.can_push ?? false;
  const q = useQuery({
    queryKey: ['monday-push', 'pending', boardId],
    queryFn: async (): Promise<Row[] | null> => {
      const res = await authFetch(`/api/v1/boards/${encodeURIComponent(boardId)}/pending`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Row[]) : null;
    },
    enabled: hasRole('OPERATOR'),
    retry: false,
  });
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => call<{ message?: string; ok?: boolean }>(path, 'POST', body ?? {}),
    onSuccess: (r) => {
      if (r?.message) (r.ok === false ? toast.warning : toast.success)(r.message);
      void client.invalidateQueries({ queryKey: ['monday-push'] });
    },
    onError: (e) => toast.error('That didn’t go through', { description: e.message }),
  });
  const rows = q.data ?? [];
  if (rows.length === 0) return null;
  const held = rows.filter((r) => r.status === 'CONFLICT').length;
  return (
    <section aria-label="Changes waiting for monday" className="sticky bottom-20 z-10 rounded-lg border bg-card text-[13px] shadow-md md:bottom-3">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex items-center gap-2 text-left">
          <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} aria-hidden />
          <span className="font-medium">Changes waiting</span>
          <span className="text-muted-foreground tabular-nums">{rows.length}</span>
          {held > 0 && <ToneBadge tone="warn">{held} held: monday changed</ToneBadge>}
        </button>
        {hasRole('MANAGER') && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={act.isPending || !canPush}
            title={canPush ? undefined : 'monday is read-only, so nothing can be sent'}
            onClick={() => {
              if (
                window.confirm(
                  `Send this board’s ${rows.length} change${rows.length === 1 ? '' : 's'} to monday? This changes the real board.`,
                )
              )
                act.mutate({ path: '/api/v1/monday-push/push', body: { board_id: boardId } });
            }}
          >
            {canPush ? 'Send this board’s changes' : 'Send (monday is read-only)'}
          </Button>
        )}
      </div>
      {open && (
        <ul className="grid divide-y border-t">
          {rows.map((r) => (
            <li key={r.id} className="grid gap-1.5 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{r.item || r.new_row?.name || 'New row'}</span>
                <ToneBadge tone={TONE[r.status] ?? 'neutral'}>{WORD[r.status] ?? r.status}</ToneBadge>
                {r.kind === 'create_item' && <span className="text-xs text-muted-foreground">new row</span>}
                {r.kind === 'post_update' && <span className="text-xs text-muted-foreground">comment</span>}
                <span className="ml-auto flex gap-1">
                  {r.status === 'CONFLICT' && hasRole('MANAGER') && (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={act.isPending}
                      onClick={() => act.mutate({ path: `/api/v1/monday-push/changes/${r.id}/rebase` })}
                    >
                      Keep my version
                    </Button>
                  )}
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={act.isPending}
                    onClick={() => act.mutate({ path: `/api/v1/monday-push/changes/${r.id}/skip` })}
                  >
                    {r.status === 'CONFLICT' ? 'Keep monday’s value' : 'Skip'}
                  </Button>
                </span>
              </div>
              {r.comment && <p className="rounded-sm bg-ok-soft px-2 py-1 text-ok">{r.comment}</p>}
              {(r.changes ?? []).map((c, i) => {
                const moved = text(c.now) !== text(c.base);
                return (
                  <div key={i} className="grid gap-0.5 sm:grid-cols-[10rem_minmax(0,1fr)]">
                    <span className="text-muted-foreground">{c.column}</span>
                    <span className="grid gap-0.5">
                      {text(c.base) && <span className="rounded-sm bg-bad-soft px-1.5 text-bad line-through">{text(c.base)}</span>}
                      <span className="rounded-sm bg-ok-soft px-1.5 font-medium text-ok">{text(c.becomes) || '(cleared)'}</span>
                      {r.status === 'CONFLICT' && moved && (
                        <span className="rounded-sm bg-warn-soft px-1.5 text-warn">monday now shows: {text(c.now) || '(blank)'}</span>
                      )}
                    </span>
                  </div>
                );
              })}
              {(r.notes ?? []).map((n, i) => (
                <p key={i} className="text-xs text-muted-foreground">
                  {n}
                </p>
              ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
