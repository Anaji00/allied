'use client';

import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { useBoards } from '@/components/boards/use-boards';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { words } from '@/lib/format';
import { cn } from '@/lib/utils';

export async function getJson<T>(path: string): Promise<T> {
  const res = await authFetch(path, { cache: 'no-store' });
  const body = (await res.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (!res.ok || !body)
    throw new Error(
      typeof (body as { detail?: string } | null)?.detail === 'string'
        ? (body as { detail: string }).detail
        : `That didn’t work (${res.status}).`,
    );
  return body;
}

export function Card({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <section className="grid max-w-2xl gap-3 rounded-lg border bg-card p-4">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="text-xs text-muted-foreground">{note}</p>
      </div>
      {children}
    </section>
  );
}

/** Plain key/value lines for an answer whose fields the screen does not know in advance. */
function Lines({ data }: { data: Record<string, unknown> }) {
  const rows = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object');
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-[13px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{words(k)}</dt>
          <dd>{String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** "Where is my order?": the answer a person would give a caller, read straight from the order. No AI is used. */
export function OrderStatusTool() {
  const [number, setNumber] = useState('');
  const look = useMutation({
    mutationFn: () => getJson<Record<string, unknown>>(`/api/v1/ai/order-status?number=${encodeURIComponent(number.trim())}`),
  });
  return (
    <Card
      title="Where is my order?"
      note="Type a sales order number to see its status the way you would tell a caller. Nothing is changed."
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (number.trim()) look.mutate();
        }}
      >
        <Field label="Order number">
          <Input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="SO-12889" className="w-48" />
        </Field>
        <Button type="submit" size="sm" disabled={!number.trim() || look.isPending}>
          {look.isPending && <Loader2 className="animate-spin" />} Look it up
        </Button>
      </form>
      {look.error && <p className="text-sm text-bad">{look.error.message}</p>}
      {look.data && <Lines data={look.data} />}
    </Card>
  );
}

type Suspected = {
  text?: string;
  test_orders?: { sales_order_number: string; customer?: string; total?: number | string; currency?: string; reason?: string }[];
  duplicate_groups?: { original: string; duplicates: string[]; reason?: string }[];
};

/** Orders that look like tests or duplicates, for a manager to look at. It only reports; nothing is changed. */
export function SuspectedOrdersTool() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['orders', 'suspected'],
    queryFn: () => getJson<Suspected>('/api/v1/orders/suspected'),
    enabled: hasRole('MANAGER'),
    retry: false,
  });
  if (!hasRole('MANAGER'))
    return <EmptyState title="Managers only">Checking for test and duplicate orders is open to managers and admins.</EmptyState>;
  return (
    <Card
      title="Test and duplicate orders"
      note="Orders that look like tests or copies, with the reason. This only reports; nothing is changed or deleted."
    >
      {q.isPending && <Loader2 className="animate-spin text-muted-foreground" />}
      {q.error && <p className="text-sm text-bad">{q.error.message}</p>}
      {q.data && (
        <div className="grid gap-3 text-[13px]">
          {q.data.text && <p>{q.data.text}</p>}
          {(q.data.test_orders ?? []).map((o) => (
            <p key={o.sales_order_number}>
              <span className="font-mono">{o.sales_order_number}</span> {o.customer ? `for ${o.customer}` : ''}:{' '}
              <span className="text-muted-foreground">{o.reason}</span>
            </p>
          ))}
          {(q.data.duplicate_groups ?? []).map((g) => (
            <p key={g.original}>
              <span className="font-mono">{g.original}</span> may be copied as <span className="font-mono">{g.duplicates.join(', ')}</span>:{' '}
              <span className="text-muted-foreground">{g.reason}</span>
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}

/** A monday board as a spreadsheet file, from the app's own read-only copy. */
export function ExportBoardTool() {
  const boards = useBoards();
  const [board, setBoard] = useState('');
  const [format, setFormat] = useState('xlsx');
  const get = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`/api/v1/exports/boards/${encodeURIComponent(board)}?format=${format}`);
      if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${boards.data?.find((b) => b.board_id === board)?.name ?? 'board'}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (e) => toast.error('The file wasn’t made', { description: e.message }),
  });
  return (
    <Card title="Export a board" note="Download a board as a spreadsheet or PDF. Nothing is sent anywhere.">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Board">
          <NativeSelect value={board} onChange={(e) => setBoard(e.target.value)} className="w-56">
            <option value="">Choose a board…</option>
            {(boards.data ?? []).map((b) => (
              <option key={b.board_id} value={b.board_id}>
                {b.name}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Kind of file">
          <NativeSelect value={format} onChange={(e) => setFormat(e.target.value)} className="w-32">
            <option value="xlsx">Excel</option>
            <option value="csv">CSV</option>
            <option value="pdf">PDF</option>
          </NativeSelect>
        </Field>
        <Button size="sm" disabled={!board || get.isPending} onClick={() => get.mutate()}>
          {get.isPending && <Loader2 className="animate-spin" />} Download
        </Button>
      </div>
    </Card>
  );
}

type Gallery = {
  slug: string;
  recipe_id?: number | string | null;
  title: string;
  description?: string;
  mode?: string;
  enabled?: boolean;
  would_act_on_today?: number;
  last_run?: string | null;
  waiting_for_ok?: number;
  failed?: number;
};
const MODE: Record<string, string> = { off: 'Off', suggest: 'Asks you first', auto: 'Automatic' };

/** Ready-made automations in plain words: what each does, whether it is on, and what it would act on today. Rules only, no AI. */
export function AutomationsTool() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ['autopilot', 'gallery'], queryFn: () => getJson<Gallery[]>('/api/v1/autopilot/gallery'), retry: false });
  const setMode = useMutation({
    mutationFn: async ({ a, mode }: { a: Gallery; mode: 'off' | 'suggest' | 'auto' }) => {
      // A rule not added yet is added first (asking before it acts), then switched.
      let id = a.recipe_id;
      if (id == null) {
        if (mode === 'off') return;
        const add = await authFetch(`/api/v1/autopilot/recipes/starters/${encodeURIComponent(a.slug)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'suggest' }),
        });
        const made = (await add.json().catch(() => null)) as { id?: number | string; detail?: string } | null;
        if (!add.ok || made?.id == null)
          throw new Error(typeof made?.detail === 'string' ? made.detail : `That didn’t work (${add.status}).`);
        id = made.id;
        if (mode === 'suggest') return;
      }
      const res = await authFetch('/api/v1/automation/mode', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: `recipe:${id}`, mode }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => null);
        throw new Error(typeof d?.detail === 'string' ? d.detail : `That didn’t work (${res.status}).`);
      }
    },
    onSuccess: () => {
      toast.success('Saved');
      void client.invalidateQueries({ queryKey: ['autopilot', 'gallery'] });
    },
    onError: (e) => toast.error('Not changed', { description: e.message }),
  });
  const pick = (a: Gallery, mode: 'off' | 'suggest' | 'auto') => {
    if (mode === 'auto' && !window.confirm(`Let “${a.title}” run by itself? Anything sent outside Allied still waits for a person.`))
      return;
    setMode.mutate({ a, mode });
  };
  return (
    <Card
      title="Automations"
      note="Ready-made rules that draft or remind for you. They follow fixed rules, use no AI, and anything that goes outside Allied waits for a person to approve."
    >
      {q.isPending && <Loader2 className="animate-spin text-muted-foreground" />}
      {q.error && <p className="text-sm text-muted-foreground">This isn’t switched on yet. Tell your administrator.</p>}
      {q.data && (
        <ul className="grid divide-y rounded-md border">
          {q.data.map((a) => (
            <li key={a.slug} className="grid gap-0.5 px-3 py-2.5 text-[13px]">
              <span className="flex items-center gap-2 font-medium">
                {a.title}
                <span className="ml-auto flex gap-1" role="group" aria-label={`${a.title}: how it runs`}>
                  {(['off', 'suggest', 'auto'] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      disabled={!hasRole('MANAGER') || setMode.isPending}
                      aria-pressed={(a.mode ?? 'off') === m}
                      onClick={() => (a.mode ?? 'off') !== m && pick(a, m)}
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-[11px] font-normal disabled:opacity-60',
                        (a.mode ?? 'off') === m
                          ? 'border-foreground bg-foreground text-background'
                          : 'text-muted-foreground hover:bg-muted',
                      )}
                    >
                      {MODE[m]}
                    </button>
                  ))}
                </span>
              </span>
              {a.description && <span className="text-muted-foreground">{a.description}</span>}
              <span className="text-xs text-muted-foreground">
                Would act on {a.would_act_on_today ?? 0} today
                {(a.waiting_for_ok ?? 0) > 0 ? ` · ${a.waiting_for_ok} waiting for your OK` : ''}
                {(a.failed ?? 0) > 0 ? ` · ${a.failed} failed` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** A plain table for rows whose fields the screen does not know in advance: simple values only, names turned into words. */
function Rows({ rows }: { rows: Record<string, unknown>[] }) {
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))]
    .filter((k) => rows.some((r) => ['string', 'number', 'boolean'].includes(typeof r[k])))
    .slice(0, 8);
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
            {keys.map((k) => (
              <th key={k} className="px-3 py-1.5 font-medium whitespace-nowrap">
                {words(k)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b last:border-0">
              {keys.map((k) => (
                <td key={k} className="px-3 py-1.5 align-top">
                  {r[k] === null || r[k] === undefined || r[k] === '' ? '–' : String(r[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
