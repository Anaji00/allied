'use client';

import { useId } from 'react';
import { useQuery } from '@tanstack/react-query';
import { NativeSelect } from '@/components/data/filters';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import { withEngineers } from '@/lib/engineers';

export type AppPerson = { email: string; name: string; role: string; label: string };

/** Everyone the app knows by name: the alert contacts (with their role) and every user. */
export async function fetchAppPeople(): Promise<AppPerson[]> {
  const contacts = await authFetch('/api/v1/alerts/contacts', { cache: 'no-store' })
    .then((r) =>
      r.ok ? (r.json() as Promise<{ contacts?: { email: string; name?: string; role_tag?: string; active?: boolean }[] }>) : null,
    )
    .catch(() => null);
  const users = await authFetch('/api/v1/updates/people?q=', { cache: 'no-store' })
    .then((r) => (r.ok ? (r.json() as Promise<{ email: string; name?: string }[]>) : []))
    .catch(() => []);
  const byEmail = new Map<string, AppPerson>();
  const add = (email: string, name?: string, role?: string) => {
    const key = email.trim().toLowerCase();
    if (!key.includes('@')) return;
    const had = byEmail.get(key);
    const nm = (name || had?.name || '').trim();
    byEmail.set(key, { email, name: nm, role: (role || had?.role || '').toLowerCase(), label: nm ? `${nm} (${email})` : email });
  };
  (contacts?.contacts ?? []).filter((c) => c.active !== false).forEach((c) => add(c.email, c.name, c.role_tag));
  users.forEach((u) => add(u.email, u.name));
  return withEngineers([...byEmail.values()]);
}

export function useAppPeople() {
  return useQuery({ queryKey: ['people', 'all'], queryFn: fetchAppPeople, staleTime: 60_000, retry: false });
}

/** A person chosen from the list by name; engineers come first. Falls back to a plain email box if the app knows nobody. */
export function PersonSelect({
  value,
  onChange,
  none = 'Nobody yet',
  className,
  placeholder,
  area = 'general',
}: {
  value: string;
  onChange: (email: string) => void;
  none?: string;
  className?: string;
  placeholder?: string;
  /** In engineering areas the engineers come first; everywhere else leadership does. */
  area?: 'engineering' | 'general';
}) {
  const people = useAppPeople();
  const list = people.data ?? [];
  if (people.isSuccess && list.length === 0) {
    return (
      <Input
        type="email"
        className={className}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? 'name@alliedweldingsystems.com'}
      />
    );
  }
  const known = list.some((p) => p.email.toLowerCase() === value.toLowerCase());
  const engineers = list.filter((p) => p.role === 'engineer');
  const leaders = list.filter((p) => p.role === 'leadership');
  const rest = list.filter((p) => p.role !== 'engineer' && p.role !== 'leadership');
  const option = (p: (typeof list)[number]) => (
    <option key={p.email || p.name} value={p.email} disabled={!p.email}>
      {p.label}
    </option>
  );
  const groups =
    area === 'engineering'
      ? ([
          ['Engineers', engineers],
          ['Leadership', leaders],
        ] as const)
      : ([
          ['Leadership', leaders],
          ['Engineers', engineers],
        ] as const);
  return (
    <NativeSelect className={className} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{people.isPending ? 'Loading people…' : none}</option>
      {value && !known && <option value={value}>{value}</option>}
      {groups.map(([label, group]) =>
        group.length > 0 ? (
          <optgroup key={label} label={label}>
            {group.map(option)}
          </optgroup>
        ) : null,
      )}
      {rest.length > 0 && (
        <optgroup label={engineers.length || leaders.length ? 'Everyone else' : 'People'}>
          {rest.map((p) => (
            <option key={p.email || p.name} value={p.email} disabled={!p.email}>
              {p.label}
            </option>
          ))}
        </optgroup>
      )}
    </NativeSelect>
  );
}

/** A text box that suggests names the app already knows as you type, but still lets you write a new one. */
export function SuggestInput({
  value,
  onChange,
  options,
  ...rest
}: { value: string; onChange: (v: string) => void; options: string[] } & Omit<
  React.ComponentProps<typeof Input>,
  'value' | 'onChange' | 'list'
>) {
  const id = useId();
  const unique = [...new Set(options.map((o) => o.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  return (
    <>
      <Input {...rest} list={id} autoComplete="off" value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id={id}>
        {unique.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  );
}

/** Customer names the app has seen on orders. */
export function useKnownCustomers() {
  return useQuery({
    queryKey: ['known', 'customers'],
    queryFn: async (): Promise<string[]> => {
      const res = await authFetch('/api/v1/orders?view=all&limit=200&offset=0', { cache: 'no-store' });
      if (!res.ok) return [];
      const body = (await res.json()) as { items?: { customer_name?: string | null }[] };
      return [...new Set((body.items ?? []).map((o) => o.customer_name ?? '').filter(Boolean))];
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}
