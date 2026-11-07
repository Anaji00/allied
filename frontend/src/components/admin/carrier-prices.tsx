'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Check, Loader2, Plus, Trash2 } from 'lucide-react';
import { ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type Line = {
  id: string;
  carrier: string;
  service: string;
  regions: string[];
  rate_usd: number | null;
  per_lb_usd: number | null;
  transit_days: number | null;
  as_of: string | null;
  note?: string | null;
  enabled: boolean;
  status: string;
  stale: boolean;
};
type Rates = {
  lines: Line[];
  needs_review: number;
  live: { id: string; name: string; ready: boolean; detail?: string }[];
};

const WHERE: Record<string, string> = { any: 'Anywhere', domestic: 'US', canada: 'Canada', international: 'Overseas' };
const BASE = '/api/v1/carriers/rates';

async function call(path: string, method: string, body?: unknown) {
  const res = await authFetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
  }
  return res.status === 204 ? null : res.json().catch(() => null);
}

/** A price box that saves when you leave it, and only if the number changed. */
function NumberBox({ value, label, disabled, onSave }: { value: number | null; label: string; disabled: boolean; onSave: (n: number | null) => void }) {
  const [text, setText] = useState(value == null ? '' : String(value));
  return (
    <Input
      aria-label={label}
      className="h-8 w-24 text-right tabular-nums"
      inputMode="decimal"
      disabled={disabled}
      value={text}
      onChange={(e) => setText(e.target.value.replace(/[^\d.]/g, ''))}
      onBlur={() => {
        const n = text === '' ? null : Number(text);
        if (n !== value && !(n != null && Number.isNaN(n))) onSave(n);
      }}
    />
  );
}

export function CarrierPrices() {
  const { hasRole } = useAuth();
  const manager = hasRole('MANAGER');
  const client = useQueryClient();
  const [adding, setAdding] = useState('');
  const rates = useQuery({
    queryKey: ['carriers', 'rates'],
    queryFn: async (): Promise<Rates | null> => {
      const res = await authFetch(BASE, { cache: 'no-store' });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`Couldn’t load carrier prices (${res.status}).`);
      return res.json();
    },
  });
  const save = useMutation({
    mutationFn: async (v: { path: string; method: string; body?: unknown; done?: string }) => {
      await call(v.path, v.method, v.body);
      return v.done;
    },
    onSuccess: (done) => {
      if (done) toast.success(done);
      void client.invalidateQueries({ queryKey: ['carriers'] });
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });
  const edit = (id: string, body: Record<string, unknown>, done = 'Saved') => save.mutate({ path: `/${encodeURIComponent(id)}`, method: 'PATCH', body, done });

  if (rates.isPending) return <RowsSkeleton rows={4} />;
  if (rates.isError) return <ErrorState error={rates.error} onRetry={() => void rates.refetch()} />;
  const data = rates.data;
  if (!data) return <p className="text-sm text-muted-foreground">Carrier prices aren’t available yet.</p>;

  return (
    <div className="grid gap-4">
      {data.needs_review > 0 && (
        <p role="status" className="rounded-lg bg-warn-soft p-3 text-sm text-warn">
          {data.needs_review} {data.needs_review === 1 ? 'price needs' : 'prices need'} a check before you rely on {data.needs_review === 1 ? 'it' : 'them'}.
        </p>
      )}
      <p className="text-sm text-muted-foreground">
        Freight prices change all the time, so these are your own figures. Type a new price and today’s date is saved with it.
        {data.live.length > 0 && (
          <>
            {' '}
            Live prices: {data.live.map((l) => `${l.name} (${l.ready ? 'ready' : 'not set up'})`).join(', ')}.
          </>
        )}
      </p>

      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full min-w-[820px] text-[13px]">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              {['Carrier', 'Service', 'Where', 'Price ($)', 'Per pound ($)', 'Days', 'Last checked', ''].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.lines.map((l) => (
              <tr key={l.id} className={`border-t align-middle ${l.enabled ? '' : 'opacity-50'}`}>
                <td className="px-3 py-2 font-medium">{l.carrier}</td>
                <td className="px-3 py-2">{l.service}</td>
                <td className="px-3 py-2">{l.regions.map((r) => WHERE[r] ?? r).join(', ')}</td>
                <td className="px-3 py-2">
                  <NumberBox key={`${l.id}-p-${l.rate_usd}`} label={`${l.carrier} price`} value={l.rate_usd} disabled={!manager} onSave={(n) => edit(l.id, { rate_usd: n })} />
                </td>
                <td className="px-3 py-2">
                  <NumberBox key={`${l.id}-l-${l.per_lb_usd}`} label={`${l.carrier} price per pound`} value={l.per_lb_usd} disabled={!manager} onSave={(n) => edit(l.id, { per_lb_usd: n })} />
                </td>
                <td className="px-3 py-2">
                  <NumberBox key={`${l.id}-t-${l.transit_days}`} label={`${l.carrier} days`} value={l.transit_days} disabled={!manager} onSave={(n) => edit(l.id, { transit_days: n })} />
                </td>
                <td className="px-3 py-2">
                  <span className="grid gap-0.5">
                    <span>{l.status}</span>
                    {l.stale && <ToneBadge tone="warn">Confirm</ToneBadge>}
                  </span>
                </td>
                <td className="px-3 py-2">
                  {manager && (
                    <span className="flex justify-end gap-1.5">
                      <Button size="sm" variant="outline" disabled={save.isPending} onClick={() => save.mutate({ path: `/${encodeURIComponent(l.id)}/confirm`, method: 'POST', done: 'Marked as checked today' })}>
                        <Check /> I checked this today
                      </Button>
                      <Button size="sm" variant="ghost" disabled={save.isPending} onClick={() => edit(l.id, { enabled: !l.enabled }, l.enabled ? 'Turned off' : 'Turned on')}>
                        {l.enabled ? 'Turn off' : 'Turn on'}
                      </Button>
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Remove ${l.carrier} ${l.service}`}
                        disabled={save.isPending}
                        onClick={() => {
                          if (window.confirm(`Remove ${l.carrier} ${l.service}?`)) save.mutate({ path: `/${encodeURIComponent(l.id)}`, method: 'DELETE', done: 'Removed' });
                        }}
                      >
                        <Trash2 />
                      </Button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {manager && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!adding.trim()) return;
            save.mutate({ path: '', method: 'POST', body: { carrier: adding.trim() }, done: 'Carrier added. Fill in its price.' });
            setAdding('');
          }}
        >
          <label className="grid gap-1 text-xs">
            <span className="font-medium">Add a carrier</span>
            <Input className="w-56" placeholder="Carrier name" value={adding} onChange={(e) => setAdding(e.target.value)} />
          </label>
          <Button type="submit" variant="outline" size="sm" disabled={!adding.trim() || save.isPending}>
            {save.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Add
          </Button>
        </form>
      )}
    </div>
  );
}
