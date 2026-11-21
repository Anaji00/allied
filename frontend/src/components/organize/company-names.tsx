'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, X } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type Alias = { id: number; company: string; plant?: string | null; family?: string | null; spellings: string[] };

async function call(path: string, method = 'GET', body?: unknown) {
  const res = await authFetch(`/api/v1/aliases${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  const out = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t work (${res.status}).`);
  return out;
}

/**
 * The spellings of each company seen on the boards ("OC-AMARLLO", "Amarillo") tied to one company, plant and family. Kept in the app
 * only: the boards are never rewritten. A spelling can mean only one company, so families stay apart.
 */
export function CompanyNamesTool() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ['aliases'], queryFn: () => call('') as Promise<Alias[]>, retry: false });
  const [form, setForm] = useState({ company: '', plant: '', family: '' });
  const [spelling, setSpelling] = useState<Record<number, string>>({});
  const [find, setFind] = useState('');
  const manager = hasRole('MANAGER');
  const refresh = () => void client.invalidateQueries({ queryKey: ['aliases'] });
  const add = useMutation({
    mutationFn: () =>
      call('', 'POST', { company: form.company.trim(), plant: form.plant.trim() || undefined, family: form.family.trim() || undefined }),
    onSuccess: () => {
      setForm({ company: '', plant: '', family: '' });
      refresh();
    },
    onError: (e) => toast.error('Not added', { description: e.message }),
  });
  const addSpelling = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => call(`/${id}/spellings`, 'POST', { spelling: text }),
    onSuccess: (_o, v) => {
      setSpelling((s) => ({ ...s, [v.id]: '' }));
      refresh();
    },
    onError: (e) => toast.error('Not added', { description: e.message }),
  });
  const dropSpelling = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => call(`/${id}/spellings?spelling=${encodeURIComponent(text)}`, 'DELETE'),
    onSuccess: refresh,
    onError: (e) => toast.error('Not removed', { description: e.message }),
  });
  const check = useMutation({
    mutationFn: () =>
      call(`/resolve?name=${encodeURIComponent(find.trim())}`) as Promise<{
        company?: string;
        plant?: string;
        family?: string;
        matched_by?: string | null;
        maybe?: { company: string }[];
      }>,
  });

  if (q.isPending) return <Loader2 className="animate-spin text-muted-foreground" />;
  if (q.error) return <EmptyState title="Not switched on yet">This isn’t switched on yet. Tell your administrator.</EmptyState>;
  const r = check.data;
  return (
    <section className="grid max-w-3xl gap-4">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">Company names</h2>
        <p className="text-xs text-muted-foreground">
          One company, site and group for every way it is written on the boards. Only the app’s own list changes; monday is never rewritten.
        </p>
      </div>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (find.trim()) check.mutate();
        }}
      >
        <Field label="Which company is this written as?">
          <Input value={find} onChange={(e) => setFind(e.target.value)} placeholder="CT-KC" className="w-48" />
        </Field>
        <Button type="submit" size="sm" variant="outline" disabled={!find.trim() || check.isPending}>
          Look it up
        </Button>
        {r && (
          <span className="text-[13px]">
            {r.matched_by
              ? `${r.company}${r.plant ? `, ${r.plant}` : ''}${r.family ? ` (${r.family})` : ''}`
              : r.maybe?.length
                ? `Not known. Could it be ${r.maybe.map((m) => m.company).join(' or ')}?`
                : 'Not known yet.'}
          </span>
        )}
      </form>
      <ul className="grid divide-y rounded-lg border bg-card text-[13px]">
        {(q.data ?? []).length === 0 && <li className="px-3 py-3 text-muted-foreground">No companies yet.</li>}
        {(q.data ?? []).map((a) => (
          <li key={a.id} className="grid gap-1.5 px-3 py-2.5">
            <span className="font-medium">
              {a.company}
              {a.plant ? <span className="font-normal text-muted-foreground"> · {a.plant}</span> : null}
              {a.family ? <span className="font-normal text-muted-foreground"> · {a.family}</span> : null}
            </span>
            <span className="flex flex-wrap items-center gap-1.5">
              {a.spellings.map((s) => (
                <span key={s} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs">
                  {s}
                  {manager && (
                    <button
                      type="button"
                      aria-label={`Remove ${s}`}
                      className="text-muted-foreground hover:text-foreground"
                      onClick={() => dropSpelling.mutate({ id: a.id, text: s })}
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </span>
              ))}
              {manager && (
                <form
                  className="flex items-center gap-1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const t = (spelling[a.id] ?? '').trim();
                    if (t) addSpelling.mutate({ id: a.id, text: t });
                  }}
                >
                  <Input
                    value={spelling[a.id] ?? ''}
                    onChange={(e) => setSpelling((s) => ({ ...s, [a.id]: e.target.value }))}
                    placeholder="Another way it is written"
                    className="h-7 w-40 text-xs"
                  />
                  <Button type="submit" size="xs" variant="outline">
                    Add
                  </Button>
                </form>
              )}
            </span>
          </li>
        ))}
      </ul>
      {manager ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (form.company.trim()) add.mutate();
          }}
        >
          <Field label="New company">
            <Input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} className="w-48" />
          </Field>
          <Field label="Site (optional)">
            <Input value={form.plant} onChange={(e) => setForm({ ...form, plant: e.target.value })} className="w-40" />
          </Field>
          <Field label="Group (optional)">
            <Input
              value={form.family}
              onChange={(e) => setForm({ ...form, family: e.target.value })}
              placeholder="Owens Corning"
              className="w-40"
            />
          </Field>
          <Button type="submit" size="sm" disabled={!form.company.trim() || add.isPending}>
            Add the company
          </Button>
        </form>
      ) : (
        <p className="text-xs text-muted-foreground">Managers add companies and other ways they are written.</p>
      )}
    </section>
  );
}
