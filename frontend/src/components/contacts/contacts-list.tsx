'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import { date } from '@/lib/format';

type Contact = {
  ref: string;
  name: string;
  email?: string | null;
  phone?: string | null;
  contact?: string | null;
  last_order_at?: string | null;
  open_orders?: number;
  owner?: string | null;
  /** What this address is for (for example "Quotes"), and a note on who to send quotes to. */
  email_purpose?: string | null;
  quote_contact_note?: string | null;
};

/** The customers (or any kind of contact) as one simple list with search: who they are, who looks after them, what is open. */
export function ContactsList({ kind }: { kind: 'customer' | 'vendor' | 'carrier' }) {
  const [q, setQ] = useState('');
  const list = useQuery({
    queryKey: ['contacts', kind, q],
    queryFn: async (): Promise<{ items: Contact[]; total: number } | null> => {
      const res = await authFetch(`/api/v1/contacts?kind=${kind}&q=${encodeURIComponent(q)}&limit=100&sort=name`, {
        cache: 'no-store',
      }).catch(() => null);
      return res?.ok ? ((await res.json()) as { items: Contact[]; total: number }) : null;
    },
    retry: false,
  });
  if (list.isPending) return <RowsSkeleton rows={6} />;
  if (!list.data) return <EmptyState title="Not available yet">The list of {kind}s shows here once it is switched on.</EmptyState>;
  const rows = list.data.items;
  return (
    <div className="grid gap-3">
      <Input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={`Search ${kind}s`}
        aria-label={`Search ${kind}s`}
        className="max-w-xs"
      />
      {rows.length === 0 ? (
        <EmptyState title={`No ${kind}s found`}>Try a shorter search.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full border-collapse text-[13px]">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Contact</th>
                <th className="px-3 py-2 font-medium max-md:hidden">Looked after by</th>
                <th className="px-3 py-2 text-right font-medium">Open</th>
                <th className="px-3 py-2 font-medium max-md:hidden">Last order</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.ref} className="border-t">
                  <td className="px-3 py-2 font-medium">{c.name}</td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {[c.contact, c.email, c.phone].filter(Boolean).join(' · ') || '–'}
                    {c.email_purpose && <span className="block text-xs">{c.email_purpose}</span>}
                    {c.quote_contact_note && <span className="block text-xs text-foreground">Quotes: {c.quote_contact_note}</span>}
                  </td>
                  <td className="px-3 py-2 max-md:hidden">{c.owner || '–'}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{c.open_orders ?? 0}</td>
                  <td className="px-3 py-2 max-md:hidden">{c.last_order_at ? date(c.last_order_at) : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{list.data.total} in all.</p>
    </div>
  );
}
