'use client';

import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { money } from '@/lib/format';
import type { Lead } from './api';
import { useLeadList } from './use-leads';

type Group = { company: string; industry?: string | null; location?: string | null; spend?: number | null; people: Lead[] };

/** The same leads grouped by company, so one plant with several contacts reads as one account. */
export function Accounts() {
  const list = useLeadList('', false);
  if (list.isPending) return <RowsSkeleton rows={5} />;
  if (!list.data) return <EmptyState title="Accounts aren’t ready yet">The app’s server needs an update to keep leads.</EmptyState>;
  if (list.data.items.length === 0) return <EmptyState title="No accounts yet">Companies appear here once you add leads.</EmptyState>;

  const groups = new Map<string, Group>();
  for (const l of list.data.items) {
    const key = String(l.account_id ?? l.company ?? l.id);
    const g = groups.get(key) ?? { company: l.company || l.name || 'Unnamed', industry: l.industry, location: l.location, spend: l.estimated_annual_spend, people: [] };
    g.people.push(l);
    groups.set(key, g);
  }
  const rows = [...groups.values()].sort((a, b) => (b.spend ?? 0) - (a.spend ?? 0));

  return (
    <ul className="grid gap-2 md:grid-cols-2" aria-label="Accounts">
      {rows.map((g) => (
        <li key={g.company} className="grid gap-1.5 rounded-lg border bg-card p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[13px] font-medium">{g.company}</span>
            {g.spend ? <span className="text-xs text-muted-foreground tabular-nums">{money(g.spend)} a year</span> : null}
          </div>
          <p className="text-xs text-muted-foreground">{[g.industry, g.location].filter(Boolean).join(' · ') || 'No industry or place yet'}</p>
          <ul className="grid gap-0.5 text-[13px]">
            {g.people.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2">
                <span>{p.name || 'No name yet'}</span>
                {p.title && <span className="text-xs text-muted-foreground">{p.title}</span>}
                <ToneBadge>{p.status}</ToneBadge>
                {p.source === 'target' && <ToneBadge tone="info">Target account</ToneBadge>}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
