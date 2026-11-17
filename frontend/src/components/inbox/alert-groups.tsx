'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ChevronRight } from 'lucide-react';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';
import { useUndoQueue } from '@/lib/undo';
import { cn } from '@/lib/utils';

type Group = {
  group: string;
  count: number;
  worst: 'critical' | 'warning' | 'info';
  oldest_days: number;
  items: { id: number; title: string; severity: string; link?: string | null; age_days: number }[];
};

const TONE: Record<Group['worst'], Tone> = { critical: 'bad', warning: 'warn', info: 'info' };
const WORD: Record<Group['worst'], string> = { critical: 'Urgent', warning: 'Soon', info: 'FYI' };
const age = (d: number) => (d < 1 ? 'today' : d === 1 ? '1 day' : `${Math.round(d)} days`);

/**
 * Open alerts folded into about eight areas instead of one long list. Closed by default; open one to see its
 * oldest few, or close the whole area at once. Quiet when there is nothing to do.
 */
export function AlertGroups({ everyone }: { everyone: boolean }) {
  const client = useQueryClient();
  const undo = useUndoQueue();
  const [open, setOpen] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const rollup = useQuery({
    queryKey: ['alerts', 'rollup', everyone],
    queryFn: async (): Promise<Group[]> => {
      const res = await authFetch(`/api/v1/alerts/rollup${everyone ? '?everyone=true' : ''}`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Group[]) : [];
    },
    refetchInterval: 30_000,
    retry: false,
  });

  function closeGroup(g: Group) {
    setHidden((s) => new Set(s).add(g.group));
    undo(`Closing ${g.count} in ${g.group}`, {
      commit: async (keepalive) => {
        const res = await authFetch('/api/v1/alerts/rollup/acknowledge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ group: g.group }),
          keepalive,
        });
        if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
        void client.invalidateQueries({ queryKey: ['alerts', 'rollup'] });
        void client.invalidateQueries({ queryKey: ['inbox'] });
      },
      onUndo: () =>
        setHidden((s) => {
          const next = new Set(s);
          next.delete(g.group);
          return next;
        }),
      onError: (e) => {
        setHidden((s) => {
          const next = new Set(s);
          next.delete(g.group);
          return next;
        });
        toast.error('Nothing was closed', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  // The briefs (daily summary, personal brief) are folded away elsewhere on the page.
  const groups = (rollup.data ?? []).filter((g) => g.count > 0 && g.group !== 'Briefs' && !hidden.has(g.group));
  if (groups.length === 0) return null;
  return (
    <section aria-label="Alerts by area" className="grid divide-y rounded-lg border bg-card">
      {groups.map((g) => {
        const isOpen = open === g.group;
        return (
          <div key={g.group}>
            <div className="flex items-center gap-2 px-3 py-2">
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : g.group)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
              >
                <ChevronRight className={cn('size-4 shrink-0 transition-transform', isOpen && 'rotate-90')} aria-hidden />
                <span className="font-medium">{g.group}</span>
                <span className="text-muted-foreground tabular-nums">{g.count}</span>
                <ToneBadge tone={TONE[g.worst]}>{WORD[g.worst]}</ToneBadge>
                <span className="hidden text-xs text-muted-foreground sm:inline">oldest {age(g.oldest_days)}</span>
                {g.items[0] && <span className="min-w-0 truncate text-xs text-muted-foreground">· {g.items[0].title}</span>}
              </button>
              <Button size="sm" variant="ghost" onClick={() => closeGroup(g)}>
                Done with all
              </Button>
            </div>
            {isOpen && (
              <ul className="grid gap-1 px-9 pb-3 text-[13px]">
                {g.items.map((i) => (
                  <li key={i.id} className="flex flex-wrap items-baseline gap-x-2">
                    {i.link ? (
                      <Link href={i.link} className="underline-offset-2 hover:underline">
                        {i.title}
                      </Link>
                    ) : (
                      <span>{i.title}</span>
                    )}
                    <span className="text-xs text-muted-foreground">{age(i.age_days)}</span>
                  </li>
                ))}
                {g.count > g.items.length && (
                  <li className="text-xs text-muted-foreground">and {g.count - g.items.length} more, in the list below</li>
                )}
              </ul>
            )}
          </div>
        );
      })}
    </section>
  );
}
