'use client';

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Upload } from 'lucide-react';
import { Field, NativeSelect, PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime } from '@/lib/format';

const BASE = '/api/v1/sage-import';

type Kind = { kind: string; label: string; required: string[]; columns: Record<string, string> };
type Preview = {
  token: string;
  kind: string;
  kind_label: string;
  already_imported: { imported_at?: string; imported_by?: string; file_name?: string } | null;
  file: { name: string; rows: number; merged_lines?: number };
  warnings?: string[];
  columns: { mapped: { header: string; field: string }[]; unmapped: string[]; date_order: 'mdy' | 'dmy' | null };
  counts: { new: number; changed: number; unchanged: number; problems: number };
  rows: {
    new: { row: number; key: string }[];
    changed: { row: number; key: string; changes: { field: string; old: unknown; new: unknown }[] }[];
    unchanged: number;
    problems: { row: number; key: string; reason: string }[];
  };
  message: string;
};
type Done = { already_imported: boolean; created: number; updated: number; unchanged: number; problems: number; message: string };
type History = {
  id: number;
  kind_label: string;
  file_name: string;
  created: number;
  updated: number;
  unchanged: number;
  problems: number;
  imported_by: string;
  imported_at: string;
};

async function failure(res: Response) {
  const d = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return new Error(typeof d === 'string' ? d : `That didn’t work (${res.status}).`);
}
const show = (v: unknown) => (v === null || v === undefined || v === '' ? '(blank)' : String(v));

/**
 * Bring in what a person exported from Sage 50 (customers, parts, invoices, payments, open orders, vendor bills).
 * The app only reads the file; it never touches Sage. A check shows what would change and saves nothing; a manager then saves it.
 */
export function BringInFromSage() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState('auto');
  const [file, setFile] = useState<File | null>(null);
  const [dateOrder, setDateOrder] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  const kinds = useQuery({
    queryKey: ['sage-import', 'kinds'],
    queryFn: async (): Promise<Kind[]> => {
      const res = await authFetch(`${BASE}/kinds`, { cache: 'no-store' });
      if (!res.ok) throw await failure(res);
      return (await res.json()) as Kind[];
    },
    retry: false,
  });
  const undoImport = useMutation({
    mutationFn: async (id: number) => {
      const res = await authFetch(`${BASE}/history/${id}/undo`, { method: 'POST' });
      if (!res.ok) throw await failure(res);
      return (await res.json()) as { message?: string };
    },
    onSuccess: (out) => {
      toast.success(out.message ?? 'Taken back.');
      void client.invalidateQueries({ queryKey: ['sage-import'] });
    },
    onError: (e) => toast.error('It was not taken back', { description: e.message }),
  });
  const history = useQuery({
    queryKey: ['sage-import', 'history'],
    queryFn: async (): Promise<History[]> => {
      const res = await authFetch(`${BASE}/history?limit=50`, { cache: 'no-store' });
      return res.ok ? ((await res.json()) as History[]) : [];
    },
    retry: false,
  });
  const check = useMutation({
    mutationFn: async (order: string) => {
      const body = new FormData();
      body.append('file', file as File);
      body.append('kind', kind);
      if (order) body.append('date_order', order);
      const res = await authFetch(`${BASE}/preview`, { method: 'POST', body });
      if (!res.ok) throw await failure(res);
      return (await res.json()) as Preview;
    },
    onSuccess: (p) => {
      setPreview(p);
      setDone(null);
    },
    onError: (e) => {
      setPreview(null);
      toast.error('The file wasn’t read', { description: e.message });
    },
  });
  const save = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`${BASE}/commit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: preview!.token }),
      });
      if (!res.ok) throw await failure(res);
      return (await res.json()) as Done;
    },
    onSuccess: (d) => {
      setDone(d);
      setPreview(null);
      setFile(null);
      toast.success(d.already_imported ? 'That file was already brought in. Nothing changed.' : d.message);
      void client.invalidateQueries({ queryKey: ['sage-import'] });
      void client.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e) => toast.error('Nothing was saved', { description: e.message }),
  });

  if (kinds.isError) {
    return <p className="text-sm text-muted-foreground">Bringing in from Sage isn’t switched on yet.</p>;
  }
  const ambiguous = preview?.rows.problems.some((p) => /month|day/i.test(p.reason) && /ambiguous|could be/i.test(p.reason)) ?? false;
  const rows = (preview?.counts.new ?? 0) + (preview?.counts.changed ?? 0);

  return (
    <div className="grid max-w-4xl gap-4">
      <p className="text-sm text-muted-foreground">
        In Sage, export a list to a file (CSV or Excel), then choose it here. We read the file and show what would change. Nothing is saved
        until a manager says so, and nothing is ever sent to Sage.
      </p>

      <section className="grid gap-3 rounded-lg border bg-card p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="What is in the file?">
            <NativeSelect value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="auto">Work it out from the file</option>
              {(kinds.data ?? []).map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="The file">
            <div className="flex items-center gap-2">
              <input
                ref={input}
                type="file"
                accept=".csv,.tsv,.xlsx"
                className="hidden"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setPreview(null);
                  setDone(null);
                  setDateOrder('');
                }}
              />
              <Button type="button" variant="outline" size="sm" onClick={() => input.current?.click()}>
                <Upload /> Choose a file
              </Button>
              <span className="min-w-0 truncate text-sm text-muted-foreground">{file ? file.name : 'None chosen'}</span>
            </div>
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!file || check.isPending} onClick={() => check.mutate(dateOrder)}>
            {check.isPending && <Loader2 className="animate-spin" />}
            Check the file
          </Button>
          {!file && <span className="text-xs text-muted-foreground">Choose a file first.</span>}
        </div>
      </section>

      {preview && (
        <section className="grid gap-3 rounded-lg border bg-card p-4" aria-label="What would change">
          <div className="grid gap-0.5">
            <h3 className="text-sm font-semibold">
              {preview.kind_label}: {preview.file.rows} rows in {preview.file.name}
            </h3>
            <p className="text-[13px] text-muted-foreground">{preview.message} Nothing has been saved yet.</p>
          </div>
          {(preview.warnings ?? []).length > 0 && (
            <ul className="grid gap-0.5 rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
              {preview.warnings!.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          {preview.already_imported && (
            <p className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
              This exact file was already brought in
              {preview.already_imported.imported_at ? ` on ${dateTime(preview.already_imported.imported_at)}` : ''}
              {preview.already_imported.imported_by ? ` by ${preview.already_imported.imported_by}` : ''}. Saving it again changes nothing.
            </p>
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ['New', preview.counts.new],
              ['Changed', preview.counts.changed],
              ['Same as now', preview.counts.unchanged],
              ['Need a look', preview.counts.problems],
            ].map(([label, n]) => (
              <div key={String(label)} className="grid gap-0.5 rounded-lg border px-3 py-2">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className={`text-xl font-semibold tabular-nums ${label === 'Need a look' && Number(n) > 0 ? 'text-warn' : ''}`}>{n}</dd>
              </div>
            ))}
          </div>

          {preview.columns.unmapped.length > 0 && (
            <p className="text-xs text-muted-foreground">Columns we didn’t use: {preview.columns.unmapped.join(', ')}.</p>
          )}

          {ambiguous && (
            <div className="flex flex-wrap items-center gap-2 rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
              Some dates could be month first or day first. Which is it in this file?
              <NativeSelect
                className="h-8 w-auto text-xs"
                value={dateOrder}
                onChange={(e) => {
                  setDateOrder(e.target.value);
                  if (e.target.value) check.mutate(e.target.value);
                }}
              >
                <option value="">Choose…</option>
                <option value="mdy">Month first (11/20/2026)</option>
                <option value="dmy">Day first (20/11/2026)</option>
              </NativeSelect>
            </div>
          )}

          {preview.rows.changed.length > 0 && (
            <div className="grid gap-1">
              <PanelHeading>What would change</PanelHeading>
              <ul className="grid gap-1 text-[13px]">
                {preview.rows.changed.map((r) => (
                  <li key={r.row}>
                    <span className="font-medium">{r.key}</span>
                    <span className="text-muted-foreground">
                      {' '}
                      {r.changes.map((c) => `${c.field}: ${show(c.old)} → ${show(c.new)}`).join(' · ')}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {preview.rows.new.length > 0 && (
            <div className="grid gap-1">
              <PanelHeading>New</PanelHeading>
              <p className="text-[13px] text-muted-foreground">{preview.rows.new.map((r) => r.key).join(', ')}</p>
            </div>
          )}
          {preview.rows.problems.length > 0 && (
            <div className="grid gap-1">
              <PanelHeading>Need a look (these are not saved)</PanelHeading>
              <ul className="grid gap-1 text-[13px]">
                {preview.rows.problems.map((p) => (
                  <li key={`${p.row}-${p.key}`}>
                    <span className="font-medium">
                      Row {p.row}
                      {p.key ? `, ${p.key}` : ''}:
                    </span>{' '}
                    <span className="text-warn">{p.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            {hasRole('MANAGER') ? (
              <Button disabled={rows === 0 || save.isPending} onClick={() => save.mutate()}>
                {save.isPending && <Loader2 className="animate-spin" />}
                Save these {rows} rows
              </Button>
            ) : (
              <span className="text-sm text-muted-foreground">A manager saves these.</span>
            )}
            {rows === 0 && <span className="text-xs text-muted-foreground">Nothing new or changed to save.</span>}
          </div>
        </section>
      )}

      {done && (
        <p className="rounded-md bg-ok-soft px-3 py-2 text-[13px] text-ok">
          {done.already_imported
            ? 'That file was already brought in, so nothing changed.'
            : `Saved: ${done.created} new, ${done.updated} changed, ${done.unchanged} the same, ${done.problems} not saved.`}
        </p>
      )}

      <section className="grid gap-2">
        <PanelHeading>Brought in so far</PanelHeading>
        {(history.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing yet.</p>
        ) : (
          <ul className="grid divide-y rounded-lg border bg-card text-[13px]">
            {history.data!.map((h) => (
              <li key={h.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2">
                <span className="font-medium">{h.kind_label}</span>
                <span className="min-w-0 truncate text-muted-foreground">{h.file_name}</span>
                <span className="ml-auto tabular-nums text-muted-foreground">
                  {h.created} new · {h.updated} changed · {h.problems} not saved
                </span>
                <span className="text-xs text-muted-foreground">
                  {dateTime(h.imported_at)} · {h.imported_by}
                </span>
                {(h as { undone?: boolean }).undone ? (
                  <span className="text-xs text-muted-foreground">Taken back</span>
                ) : (
                  hasRole('MANAGER') && (
                    <button
                      type="button"
                      className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                      disabled={undoImport.isPending}
                      onClick={() => {
                        if (
                          window.confirm(`Take back this import?

The rows it added are removed and the values it changed go back. Anything changed since is left alone.`)
                        )
                          undoImport.mutate(h.id);
                      }}
                    >
                      Undo this import
                    </button>
                  )
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
