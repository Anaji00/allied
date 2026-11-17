'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ToneBadge } from '@/components/data/tone-badge';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type Item = { key: string; level: 'needed' | 'recommended' | 'optional'; label: string; ok: boolean; if_blank: string; how_to_fix: string };
type Setup = { items: Item[]; needs_setting: number; needed_first: string[] };

/** For managers: only what is still not set up, the needed things first, and what stops working until it is. No values are shown. */
export function SetupCard() {
  const { hasRole } = useAuth();
  const [all, setAll] = useState(false);
  const q = useQuery({
    queryKey: ['health', 'setup'],
    queryFn: async (): Promise<Setup | null> => {
      const res = await authFetch('/api/v1/health/setup', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Setup) : null;
    },
    enabled: hasRole('MANAGER'),
    retry: false,
  });
  const rank = { needed: 0, recommended: 1, optional: 2 } as const;
  const todo = (q.data?.items ?? []).filter((i) => !i.ok && i.level !== 'optional').sort((a, b) => rank[a.level] - rank[b.level]);
  if (todo.length === 0) return null;
  // A long list buries the page, so only the first few show until someone asks for the rest.
  const shown = all || todo.length <= 3 ? todo : todo.slice(0, 3);
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4" aria-label="Set these first">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">Set these first</h2>
        <p className="text-xs text-muted-foreground">Each one is blank right now. This is what doesn’t work until it is filled in.</p>
      </div>
      <ul className="grid gap-3">
        {shown.map((i) => (
          <li key={i.key} className="grid gap-0.5 text-[13px]">
            <span className="flex items-center gap-2 font-medium">
              {i.label}
              <ToneBadge tone={i.level === 'needed' ? 'bad' : 'warn'}>{i.level === 'needed' ? 'Needed' : 'Worth doing'}</ToneBadge>
            </span>
            <span className="text-muted-foreground">{i.if_blank}</span>
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground">How to fix it (for whoever looks after the server)</summary>
              <span className="mt-1 block">{i.how_to_fix}</span>
            </details>
          </li>
        ))}
      </ul>
      {todo.length > 3 && (
        <button
          type="button"
          className="justify-self-start text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          onClick={() => setAll(!all)}
        >
          {all ? 'Show fewer' : `Show ${todo.length - 3} more`}
        </button>
      )}
    </section>
  );
}
