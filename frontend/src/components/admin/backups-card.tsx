'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime, since } from '@/lib/format';

type Backups = {
  enabled: boolean;
  every_hours: number;
  keep_days: number;
  count: number;
  newest?: { name?: string; size?: number; made?: string } | null;
  last_run?: {
    ok: boolean;
    file?: string | null;
    error?: string | null;
    check?: { ok?: boolean; lost?: Record<string, unknown> } | null;
  } | null;
  /** One plain line on whether a copy of the backup is kept somewhere else. */
  copy?: { state: 'made' | 'not_made' | 'not_set_up'; text?: string } | null;
};

/** For managers: is the nightly database backup running, did its last check pass, and a button to take one now. */
export function BackupsCard() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ['health', 'backups'],
    queryFn: async (): Promise<Backups | null> => {
      const res = await authFetch('/api/v1/health/backups', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Backups) : null;
    },
    enabled: hasRole('MANAGER'),
    retry: false,
  });
  const run = useMutation({
    mutationFn: async () => {
      const res = await authFetch('/api/v1/health/backups/run', { method: 'POST' });
      if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
    },
    onSuccess: () => {
      toast.success('Backup taken');
      void client.invalidateQueries({ queryKey: ['health', 'backups'] });
    },
    onError: (e) => toast.error('The backup didn’t run', { description: e.message }),
  });
  const b = q.data;
  if (!b) return null;
  const failed = b.last_run ? b.last_run.ok === false : false;
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Backups">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Backups</h2>
        <ToneBadge tone={!b.enabled ? 'bad' : failed ? 'bad' : 'ok'}>
          {!b.enabled ? 'Off' : failed ? 'Last one failed' : 'Running'}
        </ToneBadge>
        <Button size="sm" variant="outline" className="ml-auto" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending && <Loader2 className="animate-spin" />}
          Back up now
        </Button>
      </div>
      {(() => {
        // The server's summary is an object {state, text, ...}; only its sentence is shown.
        const raw = b as { text?: unknown; summary?: unknown };
        const sum = raw.summary && typeof raw.summary === 'object' ? (raw.summary as { text?: unknown }).text : raw.summary;
        const found = typeof sum === 'string' ? sum : typeof raw.text === 'string' ? raw.text : undefined;
        const plain = found;
        if (plain) return <p className="text-sm font-medium">{plain}</p>;
        if (failed)
          return (
            <p className="text-sm font-medium">
              The last backup did not finish. The earlier copies are still kept, and the app tries again in {b.every_hours} hours. You can
              also press Back up now.
            </p>
          );
        return null;
      })()}
      <p className="text-[13px] text-muted-foreground">
        {b.newest?.made ? `Newest ${dateTime(b.newest.made)} (${since(b.newest.made)}).` : 'No backup yet.'} {b.count} kept
        {b.enabled ? `, one every ${b.every_hours} hours for ${b.keep_days} days` : ''}.
        {b.last_run?.check
          ? b.last_run.check.ok
            ? ' Its check passed.'
            : ` Its check failed${Object.keys(b.last_run.check.lost ?? {}).length ? `: ${Object.keys(b.last_run.check.lost ?? {}).length} tables lost rows` : ''}.`
          : ''}
        {b.last_run?.error ? ' The last backup hit a problem. Ask your administrator.' : ''}
      </p>
      {b.copy?.text &&
        (b.copy.state === 'made' ? (
          <p className="text-[13px] text-muted-foreground">{b.copy.text}</p>
        ) : (
          <details className="text-[13px] text-warn">
            <summary className="cursor-pointer">
              {b.copy.state === 'not_set_up' ? 'No second copy is kept on another disk' : 'The second copy was not made'}
            </summary>
            <span className="mt-1 block">{b.copy.text}</span>
          </details>
        ))}
    </section>
  );
}
