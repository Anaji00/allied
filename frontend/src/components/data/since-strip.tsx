'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { authFetch } from '@/lib/api';
import { dateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

type Change = { at: string; who?: string | null; sentence: string; source?: string };
type Since = { count: number; changes: Change[]; since?: string };

const RANGES: [string, string][] = [
  ['yesterday', 'Since yesterday'],
  ['this week', 'This week'],
  ['last week', 'Last week'],
];

/** What changed on a job or a board: who did what, newest first. Quiet when the server can't say. */
export function SinceStrip({ kind, refId }: { kind: 'job' | 'board'; refId: string }) {
  const [open, setOpen] = useState(false);
  const [range, setRange] = useState('yesterday');
  const q = useQuery({
    queryKey: ['since', kind, refId, range],
    queryFn: async (): Promise<Since | null> => {
      const res = await authFetch(`/api/v1/since?kind=${kind}&ref=${encodeURIComponent(refId)}&since=${encodeURIComponent(range)}`, {
        cache: 'no-store',
      }).catch(() => null);
      return res?.ok ? ((await res.json()) as Since) : null;
    },
    staleTime: 60_000,
    retry: false,
  });
  if (!q.data) return null;
  const label = RANGES.find(([v]) => v === range)?.[1] ?? 'Since yesterday';
  return (
    <section aria-label="What changed" className="rounded-lg border bg-card text-[13px]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} aria-hidden />
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">
          {q.data.count === 0 ? 'nothing changed' : `${q.data.count} change${q.data.count === 1 ? '' : 's'}`}
        </span>
      </button>
      {open && (
        <div className="grid gap-2 border-t px-3 py-2.5">
          <div className="flex gap-1.5" role="group" aria-label="Period">
            {RANGES.map(([v, l]) => (
              <button
                key={v}
                type="button"
                aria-pressed={range === v}
                onClick={() => setRange(v)}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-xs',
                  range === v ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground',
                )}
              >
                {l}
              </button>
            ))}
          </div>
          {q.data.changes.length === 0 ? (
            <p className="text-muted-foreground">Nothing changed in that time.</p>
          ) : (
            <ul className="grid gap-1">
              {q.data.changes.slice(0, 30).map((c, i) => (
                <li key={i} className="flex flex-wrap gap-x-2">
                  <span>{c.sentence}</span>
                  <span className="text-xs text-muted-foreground">{[c.who, dateTime(c.at)].filter(Boolean).join(' · ')}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
