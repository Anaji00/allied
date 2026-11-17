'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ToneBadge } from '@/components/data/tone-badge';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { since, words } from '@/lib/format';
import { cn } from '@/lib/utils';

type Mode = 'off' | 'suggest' | 'auto' | 'always on';
type Row = {
  id: string;
  kind: 'play' | 'reaction' | 'recipe' | 'check';
  name: string;
  when: string;
  then: string;
  who_reviews?: string | null;
  mode: Mode;
  switchable: boolean;
  last_run?: string | null;
};

const SWITCH: [Mode, string][] = [
  ['off', 'Off'],
  ['suggest', 'Suggest'],
  ['auto', 'Auto'],
];
const WORD: Record<Mode, string> = { off: 'Off', suggest: 'Suggest', auto: 'Auto', 'always on': 'Always on' };
const HELP = 'Off does nothing. Suggest drafts it for a person to approve. Auto does it by itself.';

const fixWords = (t: string) =>
  t
    .replace(/\brfq\b/gi, 'RFQ')
    .replace(/\bSHIPPED\b/g, 'shipped')
    .replace(/\bPO\b/g, 'PO');

/** Everything the app does by itself, in one list with one switch. Only managers can flip it. */
export function AutomationList() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const manager = hasRole('MANAGER');
  const list = useQuery({
    queryKey: ['automation', 'list'],
    queryFn: async (): Promise<Row[] | null> => {
      const res = await authFetch('/api/v1/automation/list', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Row[]) : null;
    },
    retry: false,
  });
  const set = useMutation({
    mutationFn: async ({ id, mode }: { id: string; mode: Mode }) => {
      const res = await authFetch('/api/v1/automation/mode', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, mode }),
      });
      if (!res.ok) {
        const detail = await res
          .json()
          .then((j) => j?.detail)
          .catch(() => null);
        throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
      }
      return (await res.json()) as Row;
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['automation'] }),
    onError: (e) => toast.error('The switch didn’t change', { description: e.message }),
  });

  if (!list.data || list.data.length === 0) return null;
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4" aria-label="Everything that runs by itself">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">Everything that runs by itself</h2>
        <p className="text-xs text-muted-foreground">{HELP}</p>
      </div>
      <ul className="grid divide-y">
        {list.data.map((r) => (
          <li key={r.id} className="grid gap-2 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
            <div className="grid min-w-0 gap-0.5 text-[13px]">
              <span className="font-medium">
                {(r.name.includes(' ') ? r.name : words(r.name)).replace(/\bRfq\b/g, 'RFQ').replace(/\bPo\b/g, 'PO')}
              </span>
              <span>{fixWords(r.then)}</span>
              <span className="text-xs text-muted-foreground">
                {fixWords(r.when)}
                {r.who_reviews ? (/^nobody/i.test(r.who_reviews) ? ' · Acts on its own' : ` · Reviewed by: ${r.who_reviews}`) : ''}
                {r.last_run ? ` · Last ran ${since(r.last_run)}` : ''}
              </span>
            </div>
            {r.switchable && manager ? (
              <div role="group" aria-label={`${r.name} mode`} className="inline-flex rounded-md border p-0.5">
                {SWITCH.map(([m, label]) => (
                  <button
                    key={m}
                    type="button"
                    aria-pressed={r.mode === m}
                    disabled={set.isPending}
                    onClick={() => r.mode !== m && set.mutate({ id: r.id, mode: m })}
                    className={cn(
                      'rounded px-2.5 py-1 text-xs',
                      r.mode === m ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            ) : (
              <ToneBadge tone={r.mode === 'off' ? 'neutral' : 'ok'}>{WORD[r.mode] ?? r.mode}</ToneBadge>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
