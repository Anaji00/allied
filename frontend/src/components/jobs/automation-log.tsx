'use client';

import { useQuery } from '@tanstack/react-query';
import { Fold } from '@/components/data/fold';
import { authFetch } from '@/lib/api';
import { dateTime } from '@/lib/format';

type Log = { count: number; items: { at: string; sentence: string; source?: string }[] };

/** What the app did on this job by itself: drafts written, alerts raised, automatic steps. Hidden until the server has the log. */
export function AutomationLog({ jobRef }: { jobRef: string }) {
  const log = useQuery({
    queryKey: ['jobs', jobRef, 'automation'],
    queryFn: async (): Promise<Log | null> => {
      const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(jobRef)}/automation?limit=20`, { cache: 'no-store' }).catch(
        () => null,
      );
      return res?.ok ? ((await res.json()) as Log) : null;
    },
    retry: false,
    staleTime: 60_000,
  }).data;
  if (!log || log.count === 0) return null;
  return (
    <Fold id="job-automation" title="What the app did on this job" summary={`${log.count}`} defaultOpen={false}>
      <ul className="grid gap-1.5 text-[13px]">
        {log.items.map((i, n) => (
          <li key={n} className="grid gap-0.5">
            <span>{i.sentence}</span>
            <span className="text-xs text-muted-foreground">{dateTime(i.at)}</span>
          </li>
        ))}
      </ul>
    </Fold>
  );
}
