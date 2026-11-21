'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Field } from '@/components/data/filters';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { useUndoQueue } from '@/lib/undo';

type VC = {
  id: number;
  name: string;
  company?: string | null;
  country?: string | null;
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
};

/** The people at the vendors, kept apart from Allied staff so they never turn up in a staff list. */
export function VendorContacts() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [search, setSearch] = useState('');
  const undo = useUndoQueue();
  const [gone, setGone] = useState<Set<number>>(() => new Set());
  const manager = hasRole('MANAGER');
  const [form, setForm] = useState({ name: '', company: '', country: '', email: '', phone: '' });
  const q = useQuery({
    queryKey: ['vendor-contacts', search],
    queryFn: async (): Promise<VC[] | null> => {
      const res = await authFetch(`/api/v1/vendor-contacts?q=${encodeURIComponent(search)}`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as VC[]) : null;
    },
    retry: false,
  });
  const add = useMutation({
    mutationFn: async () => {
      const res = await authFetch('/api/v1/vendor-contacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() || undefined]))),
      });
      const out = (await res.json().catch(() => null)) as { detail?: string } | null;
      if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t save (${res.status}).`);
    },
    onSuccess: () => {
      setForm({ name: '', company: '', country: '', email: '', phone: '' });
      void client.invalidateQueries({ queryKey: ['vendor-contacts'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  // Removing waits a few seconds so it can be undone; emails already written are not changed.
  function remove(c: VC) {
    setGone((g) => new Set(g).add(c.id));
    const back = () =>
      setGone((g) => {
        const n = new Set(g);
        n.delete(c.id);
        return n;
      });
    undo(`${c.name} removed from vendor contacts. Emails already written are not changed.`, {
      commit: async (keepalive) => {
        const res = await authFetch(`/api/v1/vendor-contacts/${c.id}`, { method: 'DELETE', keepalive });
        if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
        void client.invalidateQueries({ queryKey: ['vendor-contacts'] });
      },
      onUndo: back,
      onError: (e) => {
        back();
        toast.error('Not removed', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }
  if (q.isPending) return <RowsSkeleton rows={4} />;
  if (!q.data) return <EmptyState title="Not switched on yet">This isn’t switched on yet. Tell your administrator.</EmptyState>;
  return (
    <div className="grid gap-3">
      <Input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name or company"
        aria-label="Search"
        className="max-w-xs"
      />
      {q.data.length === 0 ? (
        <EmptyState title="No vendor contacts yet">Add the people you deal with at each vendor below.</EmptyState>
      ) : (
        <ul className="grid divide-y rounded-lg border bg-card text-[13px]">
          {q.data
            .filter((c) => !gone.has(c.id))
            .map((c) => (
              <li key={c.id} className="flex items-start gap-2 px-3 py-2.5">
                <div className="grid min-w-0 flex-1 gap-0.5">
                  <span className="font-medium">
                    {c.name}
                    {c.company ? <span className="font-normal text-muted-foreground"> · {c.company}</span> : null}
                    {c.country ? <span className="font-normal text-muted-foreground"> · {c.country}</span> : null}
                  </span>
                  <span className="text-xs text-muted-foreground">{[c.email, c.phone, c.notes].filter(Boolean).join(' · ')}</span>
                </div>
                {manager && (
                  <Button size="xs" variant="ghost" onClick={() => remove(c)}>
                    Remove
                  </Button>
                )}
              </li>
            ))}
        </ul>
      )}
      {hasRole('MANAGER') && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (form.name.trim()) add.mutate();
          }}
        >
          {(
            [
              ['name', 'Name', 'w-40'],
              ['company', 'Company', 'w-44'],
              ['country', 'Country', 'w-28'],
              ['email', 'Email', 'w-52'],
              ['phone', 'Phone', 'w-36'],
            ] as const
          ).map(([k, label, w]) => (
            <Field key={k} label={label}>
              <Input value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} className={w} />
            </Field>
          ))}
          <Button type="submit" size="sm" disabled={!form.name.trim() || add.isPending}>
            Add the contact
          </Button>
        </form>
      )}
    </div>
  );
}
