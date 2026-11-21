'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Col = { field: string; label: string; type: string; editable: boolean; choices?: string[] };
type Cell = { value: unknown; text?: string; type: string; editable: boolean };
type Row = {
  kind: string;
  record_kind: string;
  ref: string;
  link?: string;
  cells: Record<string, Cell>;
  needs_me?: boolean;
  check_currency?: boolean;
  check_currency_reason?: string | null;
};
type Book = { count: number; columns: Record<string, Col[]>; rows: Row[]; needs_me?: number };

const KINDS = [
  ['all', 'Everything'],
  ['rfq', 'Quote requests'],
  ['so', 'Sales orders'],
  ['po', 'Purchase orders'],
] as const;
const KIND_WORD: Record<string, string> = { rfq: 'Quote request', so: 'Sales order', po: 'Purchase order', job: 'Job' };
const show = (v: unknown) => (v === null || v === undefined || v === '' ? '–' : String(v));

/**
 * One list for quote requests, sales orders and purchase orders (or jobs), like a spreadsheet: click a value to change it.
 * The columns and what can be edited come from the server; a changed value is checked and saved to the record at once.
 */
export function BookGrid({ area }: { area: 'orders' | 'jobs' }) {
  const client = useQueryClient();
  const [kind, setKind] = useState<(typeof KINDS)[number][0]>('all');
  const [q, setQ] = useState('');
  const [mine, setMine] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ['book', area, kind, q, mine],
    queryFn: async (): Promise<Book | null> => {
      const params = new URLSearchParams({ area, limit: '200' });
      if (area === 'orders') params.set('kind', kind);
      if (q.trim()) params.set('q', q.trim());
      if (mine) params.set('mine', 'true');
      const res = await authFetch(`/api/v1/book?${params}`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Book) : null;
    },
    retry: false,
  });
  const save = useMutation({
    mutationFn: async ({ row, field, text }: { row: Row; field: string; text: string }) => {
      const res = await authFetch(`/api/v1/records/${encodeURIComponent(row.record_kind)}/${encodeURIComponent(row.ref)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ field, text }),
      });
      const body = (await res.json().catch(() => null)) as { detail?: string; understood?: string } | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      return body ?? {};
    },
    onSuccess: (r) => {
      toast.success('Saved', { description: r.understood });
      setEditing(null);
      void client.invalidateQueries({ queryKey: ['book'] });
    },
    onError: (e) => toast.error('That wasn’t saved', { description: e.message }),
  });

  const data = list.data;
  // With several kinds on one page, show the columns the kinds share first and the kind's own after them.
  const cols = (r: Row) => data?.columns[r.kind] ?? [];

  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {area === 'orders' && (
          <div className="flex gap-1.5" role="group" aria-label="Kind">
            {KINDS.map(([v, label]) => (
              <button
                key={v}
                type="button"
                aria-pressed={kind === v}
                onClick={() => setKind(v)}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-xs',
                  kind === v ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" className="size-3.5 accent-foreground" checked={mine} onChange={(e) => setMine(e.target.checked)} />
          Mine only
        </label>
        <Input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search"
          aria-label="Search"
          className="ml-auto max-w-xs"
        />
      </div>
      {list.isPending ? (
        <RowsSkeleton rows={6} />
      ) : !data ? (
        <EmptyState title="Not available yet">This list shows here once it is switched on.</EmptyState>
      ) : data.rows.length === 0 ? (
        <EmptyState title="Nothing here">Try another kind or a shorter search.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full border-collapse text-[13px]">
            <tbody>
              {data.rows.map((r) => (
                <tr key={`${r.kind}-${r.ref}`} className="border-b align-top last:border-0">
                  <td className="w-28 px-3 py-2 text-xs text-muted-foreground">
                    {KIND_WORD[r.kind] ?? r.kind}
                    <span className="block font-mono text-foreground">{r.ref}</span>
                    {r.needs_me && <span className="text-warn">needs you</span>}
                    {r.check_currency && (
                      <span className="block text-warn" title={r.check_currency_reason ?? undefined}>
                        check currency
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-x-5 gap-y-1">
                      {cols(r).map((c) => {
                        const cell = r.cells[c.field];
                        const key = `${r.kind}-${r.ref}-${c.field}`;
                        if (!cell) return null;
                        return (
                          <div key={c.field} className="grid min-w-24 gap-0.5">
                            <span className="text-[11px] text-muted-foreground">{c.label}</span>
                            {editing === key ? (
                              <input
                                autoFocus
                                disabled={save.isPending}
                                defaultValue={cell.value === null || cell.value === undefined ? '' : String(cell.value)}
                                className="h-7 w-40 rounded-md border border-ring bg-background px-2 text-[13px] outline-none ring-2 ring-ring/30"
                                onFocus={(e) => e.currentTarget.select()}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') save.mutate({ row: r, field: c.field, text: e.currentTarget.value });
                                  else if (e.key === 'Escape') setEditing(null);
                                }}
                                onBlur={() => setEditing(null)}
                              />
                            ) : cell.editable ? (
                              <button
                                type="button"
                                className="rounded px-1 text-left hover:bg-muted"
                                title="Click to change"
                                onClick={() => setEditing(key)}
                              >
                                {show(cell.text ?? cell.value)}
                              </button>
                            ) : (
                              <span className="px-1 text-muted-foreground">{show(cell.text ?? cell.value)}</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && <p className="text-xs text-muted-foreground">{data.count} shown.</p>}
    </div>
  );
}
