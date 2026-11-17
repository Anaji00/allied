'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { FilterChips } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime, since } from '@/lib/format';

const BASE = '/api/v1/monday-push';

type Status = {
  connected: boolean;
  read_only: boolean;
  can_push: boolean;
  staged: number;
  failed: number;
  counts: Record<string, number>;
  detail: string;
};
type Change = {
  id: number;
  kind: string;
  summary: string;
  status: string;
  error?: string | null;
  created_at?: string | null;
  source?: string;
  attempts?: number;
};
type Preview = {
  id: number;
  summary: string;
  board?: string;
  item?: string;
  notes?: string[];
  changes?: { column: string; now: unknown; becomes: unknown }[];
  comment?: string;
  new_row?: { name?: string; fields?: Record<string, unknown> };
};

const STATES = ['STAGED', 'PUSHING', 'FAILED', 'PUSHED', 'SKIPPED', 'EXPIRED'] as const;
type State = (typeof STATES)[number];
const LABEL: Record<State, { label: string; tone: Tone }> = {
  STAGED: { label: 'Waiting', tone: 'info' },
  PUSHING: { label: 'Sending now', tone: 'info' },
  FAILED: { label: 'Failed', tone: 'bad' },
  PUSHED: { label: 'Sent', tone: 'ok' },
  SKIPPED: { label: 'Skipped', tone: 'neutral' },
  EXPIRED: { label: 'Too old', tone: 'neutral' },
};
const KIND: Record<string, string> = { update_columns: 'Change a row', post_update: 'Add a comment', create_item: 'Add a row' };
const show = (v: unknown) => (v === null || v === undefined || v === '' ? '–' : typeof v === 'object' ? JSON.stringify(v) : String(v));

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
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
  return (await res.json()) as T;
}

/** The count on the tab: how many changes are waiting to go to monday. */
export function useMondayStatus() {
  return useQuery({
    queryKey: ['monday-push', 'status'],
    queryFn: async (): Promise<Status | null> => {
      try {
        return await call<Status>('/status');
      } catch {
        return null;
      }
    },
    refetchInterval: 60_000,
    retry: false,
  });
}

/**
 * What the app would like to change on monday, listed like staged edits: look, skip what you don't want,
 * and a manager sends the rest. There is no switch here for monday's read-only setting; while it is on, nothing is sent.
 */
export function MondayChanges() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [state, setState] = useState<State>('STAGED');
  const status = useMondayStatus();
  const list = useQuery({
    queryKey: ['monday-push', 'changes', state],
    queryFn: () => call<Change[]>(`/changes?state=${state}`),
    enabled: Boolean(status.data),
    retry: false,
  });
  const waiting = state === 'STAGED' || state === 'FAILED';
  const preview = useQuery({
    queryKey: ['monday-push', 'preview'],
    queryFn: async (): Promise<Preview[]> => {
      try {
        return await call<Preview[]>('/preview');
      } catch {
        return [];
      }
    },
    enabled: Boolean(status.data) && waiting && hasRole('OPERATOR'),
    retry: false,
  });
  const detail = new Map((preview.data ?? []).map((p) => [p.id, p]));
  const refresh = () => void client.invalidateQueries({ queryKey: ['monday-push'] });

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => call<{ message?: string; ok?: boolean }>(path, 'POST', body),
    onSuccess: (r) => {
      if (r?.message) (r.ok === false ? toast.warning : toast.success)(r.message);
      refresh();
    },
    onError: (e) => toast.error('That didn’t go through', { description: e.message }),
  });

  if (status.isPending) return <RowsSkeleton rows={3} />;
  if (!status.data)
    return <EmptyState title="Not available yet">The list of changes waiting for monday shows up here once it is switched on.</EmptyState>;
  const s = status.data;
  const rows = list.data ?? [];

  return (
    <div className="grid gap-4">
      <div className="grid gap-1.5 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">
            {s.staged} {s.staged === 1 ? 'change is' : 'changes are'} waiting
            {s.failed > 0 && `, ${s.failed} failed`}
          </p>
          <ToneBadge tone={s.can_push ? 'warn' : 'neutral'}>{s.can_push ? 'Sending is on' : 'Nothing is sent'}</ToneBadge>
          {hasRole('MANAGER') && !s.can_push && <span className="ml-auto text-xs text-muted-foreground">Sending is switched off.</span>}
          {hasRole('MANAGER') && s.can_push && s.staged === 0 && (
            <span className="ml-auto text-xs text-muted-foreground">Nothing is waiting.</span>
          )}
          {hasRole('MANAGER') && (
            <Button
              size="sm"
              className="ml-auto"
              disabled={!s.can_push || s.staged === 0 || act.isPending}
              onClick={() => {
                if (window.confirm(`Send ${s.staged} change${s.staged === 1 ? '' : 's'} to monday? This changes the real board.`))
                  act.mutate({ path: '/push', body: {} });
              }}
            >
              {act.isPending && <Loader2 className="animate-spin" />}
              Send to monday
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{s.detail}</p>
      </div>

      <FilterChips
        label="Show"
        value={state}
        onChange={setState}
        options={STATES.map((st) => ({
          value: st,
          label: LABEL[st].label,
          count: s.counts?.[st] ?? 0,
          tone: st === 'FAILED' ? ('bad' as const) : undefined,
        }))}
      />

      {list.isPending ? (
        <RowsSkeleton rows={3} />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState title="Nothing here">No changes in this list.</EmptyState>
      ) : (
        <ul className="grid gap-3">
          {rows.map((c) => {
            const d = detail.get(c.id);
            const st = LABEL[c.status as State] ?? { label: c.status, tone: 'neutral' as Tone };
            return (
              <li key={c.id} className="grid gap-2 rounded-lg border bg-card p-4 text-[13px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{c.summary}</span>
                  <ToneBadge tone={st.tone}>{st.label}</ToneBadge>
                  <span className="text-xs text-muted-foreground">
                    {KIND[c.kind] ?? c.kind}
                    {d?.board ? ` · ${d.board}` : ''}
                    {d?.item ? ` · ${d.item}` : ''}
                    {c.created_at ? ` · ${since(c.created_at)}` : ''}
                  </span>
                  <span className="ml-auto flex gap-1.5">
                    {c.status === 'STAGED' && hasRole('OPERATOR') && (
                      <Button
                        size="xs"
                        variant="ghost"
                        disabled={act.isPending}
                        onClick={() => act.mutate({ path: `/changes/${c.id}/skip` })}
                      >
                        Skip
                      </Button>
                    )}
                    {c.status === 'FAILED' && hasRole('MANAGER') && (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={act.isPending}
                        onClick={() => act.mutate({ path: `/changes/${c.id}/retry` })}
                      >
                        Try again
                      </Button>
                    )}
                  </span>
                </div>
                {c.error && <p className="rounded-md bg-bad-soft p-2 text-xs text-bad">{c.error}</p>}
                {d?.changes && d.changes.length > 0 && (
                  <table className="w-full text-xs">
                    <thead className="text-left text-muted-foreground">
                      <tr>
                        <th className="py-1 pr-3 font-medium">Column</th>
                        <th className="py-1 pr-3 font-medium">monday shows</th>
                        <th className="py-1 font-medium">It would become</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.changes.map((x, i) => (
                        <tr key={i} className="border-t">
                          <td className="py-1 pr-3 font-medium">{x.column}</td>
                          <td className="py-1 pr-3 text-muted-foreground">{show(x.now)}</td>
                          <td className="py-1">{show(x.becomes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {d?.comment && <p className="rounded-md bg-muted/50 p-2 text-xs whitespace-pre-wrap">{d.comment}</p>}
                {d?.new_row && (
                  <div className="grid gap-0.5 rounded-md bg-muted/50 p-2 text-xs">
                    <span className="font-medium">{d.new_row.name || 'New row'}</span>
                    {Object.entries(d.new_row.fields ?? {}).map(([k, v]) => (
                      <span key={k}>
                        <span className="text-muted-foreground">{k}: </span>
                        {show(v)}
                      </span>
                    ))}
                  </div>
                )}
                {d?.notes && d.notes.length > 0 && (
                  <ul className="grid gap-0.5 text-xs text-muted-foreground">
                    {d.notes.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                )}
                {!waiting && c.created_at && <p className="text-xs text-muted-foreground">Waiting since {dateTime(c.created_at)}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
