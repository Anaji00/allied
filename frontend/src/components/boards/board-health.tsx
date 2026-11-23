'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Issue = {
  rule: string;
  severity: 'high' | 'medium' | 'low';
  item_id: string | null;
  item: string | null;
  message: string;
  suggestion?: string | null;
};
type Health = { open_items: number; items_with_issues: number; clean_pct: number; counts: Record<string, number>; issues: Issue[] };

const RULE: Record<string, string> = {
  no_owner: 'No owner',
  no_owner_column: 'No People column on this board',
  no_status: 'No status',
  no_date: 'No date',
  stale: 'Not touched in a while',
  duplicate_po: 'Same PO twice',
};
const TONE: Record<Issue['severity'], Tone> = { high: 'bad', medium: 'warn', low: 'neutral' };

/** One quiet line at the top of a board: how clean it is. Open it to see what to fix, grouped by problem, each linking to its row. */
export function BoardHealth({ boardId, onOpenItem }: { boardId: string; onOpenItem: (itemId: string) => void }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ['boards', boardId, 'health'],
    queryFn: async (): Promise<Health | null> => {
      const res = await authFetch(`/api/v1/boards/${encodeURIComponent(boardId)}/health`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Health) : null;
    },
    retry: false,
    staleTime: 60_000,
  });
  const h = q.data;
  if (!h || h.open_items === 0) return null;
  const rules = Object.keys(h.counts).filter((r) => h.counts[r] > 0);
  const clean = h.issues.length === 0;
  return (
    <section aria-label="Needs fixing" className="rounded-lg border bg-card text-[13px]">
      <button
        type="button"
        aria-expanded={open}
        disabled={clean}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left disabled:cursor-default"
      >
        {!clean && <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} aria-hidden />}
        <span className="font-medium">{clean ? 'Nothing to fix' : 'Needs fixing'}</span>
        <ToneBadge tone={h.clean_pct >= 90 ? 'ok' : h.clean_pct >= 60 ? 'warn' : 'bad'}>{Math.round(h.clean_pct)}% complete</ToneBadge>
        {!clean && (
          <span className="text-muted-foreground">
            {h.items_with_issues} of {h.open_items} open items have something missing or odd
          </span>
        )}
      </button>
      {open && (
        <div className="grid gap-3 border-t px-3 py-3">
          {rules.map((r) => (
            <div key={r} className="grid gap-1">
              <h3 className="text-xs font-semibold text-muted-foreground">
                {RULE[r] ?? r} <span className="tabular-nums">{h.counts[r]}</span>
              </h3>
              <ul className="grid gap-1">
                {h.issues
                  .filter((i) => i.rule === r)
                  .map((i, n) => (
                    <li key={`${i.item_id}-${n}`} className="flex flex-wrap items-baseline gap-x-2">
                      <ToneBadge tone={TONE[i.severity]}>
                        {i.severity === 'high' ? 'Urgent' : i.severity === 'medium' ? 'Soon' : 'Minor'}
                      </ToneBadge>
                      {i.item_id ? <span className="font-medium">{i.item || 'This item'}</span> : null}
                      <span className="text-muted-foreground">{i.suggestion || i.message}</span>
                      {i.item_id ? (
                        <Button size="xs" variant="outline" className="ml-auto" onClick={() => onOpenItem(i.item_id!)}>
                          Open it
                        </Button>
                      ) : null}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
