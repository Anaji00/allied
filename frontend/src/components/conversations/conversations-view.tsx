'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';
import { dateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

type Thread = {
  key: string;
  item_type: string;
  item_ref: string;
  title: string;
  link?: string;
  job_ref?: string | null;
  last_at?: string;
  last_by?: string;
  last_sentence?: string;
  messages: number;
  unread: number;
  waits_on?: { name?: string; note?: string } | null;
  drafts_waiting?: number;
};
type Data = { total: number; unread_total: number; items: Thread[] };

/** Every comment, note and waiting draft in one list, newest first. Each line opens the thing it is about. */
export function ConversationsView() {
  const client = useQueryClient();
  const [onlyUnread, setOnlyUnread] = useState(false);
  const q = useQuery({
    queryKey: ['conversations', onlyUnread],
    queryFn: async (): Promise<Data | null> => {
      const res = await authFetch(`/api/v1/conversations?limit=100${onlyUnread ? '&only_unread=true' : ''}`, { cache: 'no-store' }).catch(
        () => null,
      );
      return res?.ok ? ((await res.json()) as Data) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  });
  const readAll = useMutation({
    mutationFn: async () => {
      const res = await authFetch('/api/v1/conversations/read-all', { method: 'POST' });
      if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['conversations'] }),
    onError: (e) => toast.error('Not marked as read', { description: e.message }),
  });
  const readOne = (t: Thread) =>
    void authFetch(`/api/v1/conversations/${encodeURIComponent(t.item_type)}/${encodeURIComponent(t.item_ref)}/read`, { method: 'POST' })
      .catch(() => null)
      .then(() => client.invalidateQueries({ queryKey: ['conversations'] }));

  if (q.isPending) return <RowsSkeleton rows={6} />;
  if (!q.data)
    return (
      <EmptyState title="Conversations aren’t ready yet">
        The app’s server needs to be updated to show them. Ask whoever runs the server to restart it, then reload this page.
      </EmptyState>
    );
  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {(
          [
            [false, 'Everything'],
            [true, `Unread (${q.data.unread_total})`],
          ] as const
        ).map(([v, label]) => (
          <button
            key={label}
            type="button"
            aria-pressed={onlyUnread === v}
            onClick={() => setOnlyUnread(v)}
            className={cn(
              'rounded-full border px-2.5 py-1',
              onlyUnread === v ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground',
            )}
          >
            {label}
          </button>
        ))}
        {q.data.unread_total > 0 && (
          <Button size="xs" variant="ghost" className="ml-auto" disabled={readAll.isPending} onClick={() => readAll.mutate()}>
            Mark everything read
          </Button>
        )}
      </div>
      {q.data.items.length === 0 ? (
        <EmptyState title="Nothing here">No comments yet.</EmptyState>
      ) : (
        <ul className="grid divide-y rounded-lg border bg-card">
          {q.data.items.map((t) => (
            <li key={t.key}>
              <Link
                href={t.link ?? '/inbox'}
                onClick={() => t.unread > 0 && readOne(t)}
                className="grid gap-0.5 px-3 py-2.5 hover:bg-muted/40"
              >
                <span className="flex items-center gap-2 text-[13px] font-medium">
                  {t.unread > 0 && <span className="size-2 shrink-0 rounded-full bg-primary" aria-label={`${t.unread} unread`} />}
                  {t.title}
                  <span className="ml-auto text-xs font-normal text-muted-foreground">{t.last_at ? dateTime(t.last_at) : ''}</span>
                </span>
                {t.last_sentence && (
                  <span className="truncate text-[13px] text-muted-foreground">
                    {t.last_by ? `${t.last_by}: ` : ''}
                    {t.last_sentence}
                  </span>
                )}
                {(t.waits_on?.name || (t.drafts_waiting ?? 0) > 0) && (
                  <span className="text-xs text-muted-foreground">
                    {t.waits_on?.name ? `Waiting on ${t.waits_on.name}` : ''}
                    {t.waits_on?.name && (t.drafts_waiting ?? 0) > 0 ? ' · ' : ''}
                    {(t.drafts_waiting ?? 0) > 0 ? `${t.drafts_waiting} draft${t.drafts_waiting === 1 ? '' : 's'} waiting for your OK` : ''}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
