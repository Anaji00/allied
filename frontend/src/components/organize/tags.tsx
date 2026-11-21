'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X } from 'lucide-react';
import { SuggestInput } from '@/components/data/pick';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type ItemTags = { tags: string[] };
type TagList = { suggested?: string[]; used?: { tag: string }[] };

const path = (kind: string, ref: string) => `/api/v1/tags/${encodeURIComponent(kind)}/${encodeURIComponent(ref)}`;

/**
 * The app's own labels on a record ("senior follow", "blind ship"): kept in the app, never written to monday.
 * Hidden until the server has them.
 */
export function Tags({ kind, refId }: { kind: string; refId: string }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState('');
  const mine = useQuery({
    queryKey: ['tags', kind, refId],
    queryFn: async (): Promise<ItemTags | null> => {
      const res = await authFetch(path(kind, refId), { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as ItemTags) : null;
    },
    retry: false,
    staleTime: 60_000,
  });
  const all = useQuery({
    queryKey: ['tags', 'all'],
    queryFn: async (): Promise<TagList | null> => {
      const res = await authFetch('/api/v1/tags', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as TagList) : null;
    },
    enabled: adding,
    retry: false,
    staleTime: 120_000,
  });
  const change = useMutation({
    mutationFn: async ({ add, tag }: { add: boolean; tag: string }) => {
      const res = await authFetch(add ? path(kind, refId) : `${path(kind, refId)}?tag=${encodeURIComponent(tag)}`, {
        method: add ? 'POST' : 'DELETE',
        headers: add ? { 'Content-Type': 'application/json' } : undefined,
        body: add ? JSON.stringify({ tag }) : undefined,
      });
      const out = (await res.json().catch(() => null)) as { detail?: string } | null;
      if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t work (${res.status}).`);
    },
    onSuccess: () => {
      setText('');
      void client.invalidateQueries({ queryKey: ['tags'] });
    },
    onError: (e) => toast.error('Not changed', { description: e.message }),
  });
  if (!mine.data) return null;
  const canEdit = hasRole('OPERATOR');
  const options = [...new Set([...(all.data?.suggested ?? []), ...(all.data?.used ?? []).map((u) => u.tag)])].filter(
    (t) => !mine.data!.tags.includes(t),
  );
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs" aria-label="Tags">
      <span className="text-muted-foreground">Tags</span>
      {mine.data.tags.map((t) => (
        <span key={t} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5">
          {t}
          {canEdit && (
            <button
              type="button"
              aria-label={`Remove ${t}`}
              className="text-muted-foreground hover:text-foreground"
              onClick={() => change.mutate({ add: false, tag: t })}
            >
              <X className="size-3" />
            </button>
          )}
        </span>
      ))}
      {mine.data.tags.length === 0 && !adding && <span className="text-muted-foreground">none</span>}
      {canEdit &&
        (adding ? (
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) change.mutate({ add: true, tag: text.trim() });
            }}
          >
            <SuggestInput
              value={text}
              onChange={setText}
              options={options}
              placeholder="Add a tag"
              className="h-7 w-44 text-xs"
              autoFocus
            />
            <Button type="submit" size="xs" disabled={!text.trim() || change.isPending}>
              Add
            </Button>
            <Button type="button" size="xs" variant="ghost" onClick={() => setAdding(false)}>
              Done
            </Button>
          </form>
        ) : (
          <button
            type="button"
            className="rounded-full border px-2 py-0.5 text-muted-foreground hover:bg-muted"
            onClick={() => setAdding(true)}
          >
            + Add a tag
          </button>
        ))}
      <span className="text-muted-foreground">(kept in the app only; monday is not changed)</span>
    </div>
  );
}
