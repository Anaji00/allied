'use client';

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { FileUp, Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { authFetch, fetchCSVTemplates } from '@/lib/api';

const LIST_TYPES = [
  ['ITEMS', 'Products'],
  ['CUSTOMERS', 'Customer accounts'],
  ['VENDORS', 'Suppliers'],
  ['ORDERS_BATCH', 'A batch of customer orders'],
] as const;

type Row = {
  row: number;
  action: 'new' | 'changed' | 'skipped';
  key?: string | null;
  changes?: { field: string; was?: unknown; now?: unknown }[];
  reason?: string | null;
};
type Preview = {
  import_type: string;
  new: number;
  changed: number;
  skipped: number;
  rows: Row[];
  errors?: string[];
  message?: string;
  token?: string;
};
type Result = {
  status: string;
  message?: string;
  rows_processed?: number;
  records_created?: number;
  records_updated?: number;
  errors?: string[];
};

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await authFetch(`/api/v1/csv/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const out = (await res.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (!res.ok || !out) {
    const detail =
      typeof (out as { detail?: string } | null)?.detail === 'string'
        ? (out as { detail: string }).detail
        : `That didn’t work (${res.status}).`;
    throw Object.assign(new Error(detail), { status: res.status });
  }
  return out;
}

const show = (v: unknown) => (v === null || v === undefined || v === '' ? '(blank)' : String(v));

function RowList({ title, note, rows }: { title: string; note: string; rows: Row[] }) {
  const [all, setAll] = useState(false);
  if (rows.length === 0) return null;
  const shown = all ? rows : rows.slice(0, 5);
  return (
    <div className="grid gap-1">
      <p className="text-xs font-medium">
        {title} <span className="font-normal text-muted-foreground">{note}</span>
      </p>
      <ul className="grid gap-0.5 text-[13px]">
        {shown.map((r) => (
          <li key={r.row}>
            <span className="text-muted-foreground">Row {r.row}</span> {r.key ?? ''}
            {r.action === 'changed' &&
              (r.changes ?? []).map((c) => (
                <span key={c.field} className="block pl-4 text-xs text-muted-foreground">
                  {c.field}: was {show(c.was)}, now {show(c.now)}
                </span>
              ))}
            {r.action === 'skipped' && r.reason && <span className="block pl-4 text-xs text-muted-foreground">{r.reason}</span>}
          </li>
        ))}
      </ul>
      {rows.length > 5 && (
        <button type="button" className="justify-self-start text-xs underline underline-offset-2" onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}

/**
 * Update the product, customer and supplier lists (or make a batch of orders) from a spreadsheet. The file is checked first: the
 * person sees what would be added, changed and skipped, and nothing is saved until they press the import button.
 */
export function ListImport() {
  const client = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [type, setType] = useState<string>('ITEMS');
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [done, setDone] = useState<Result | null>(null);
  const templates = useQuery({
    queryKey: ['csv', 'templates'],
    queryFn: async () => ((await fetchCSVTemplates()) as { templates?: Record<string, string> }).templates ?? {},
    staleTime: Infinity,
  });
  const label = LIST_TYPES.find(([v]) => v === type)?.[1] ?? '';

  const check = useMutation({
    mutationFn: () => post<Preview>('preview', { csv_text: text, import_type: type }),
    onSuccess: (p) => {
      setPreview(p);
      setDone(null);
    },
    onError: (e) => toast.error(`The file could not be checked. ${e.message} Nothing was changed.`),
  });
  const run = useMutation({
    mutationFn: () => post<Result>('import', { csv_text: text, import_type: type, token: preview?.token }),
    onSuccess: (r) => {
      setDone(r);
      setPreview(null);
      void client.invalidateQueries({ queryKey: ['sage'] });
    },
    onError: (e: Error & { status?: number }) => {
      if (e.status === 409) {
        setPreview(null);
        toast.error('The file changed since it was checked, or the check is too old. Check it again.');
      } else toast.error(`The file was not imported. ${e.message} Nothing was changed.`);
    },
  });

  const change = (next: string) => {
    const untouched = !text.trim() || text === templates.data?.[type];
    setType(next);
    setPreview(null);
    if (untouched) setText(templates.data?.[next] ?? '');
  };
  const todo = (preview?.new ?? 0) + (preview?.changed ?? 0);
  const batch = type === 'ORDERS_BATCH';
  const rows = (a: Row['action']) => (preview?.rows ?? []).filter((r) => r.action === a);

  return (
    <div className="grid max-w-4xl gap-3 rounded-lg border bg-card p-4">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">Update lists from a spreadsheet</h2>
        <p className="text-xs text-muted-foreground">
          Add or change products, customers or suppliers, or bring in a batch of orders, from a CSV file. You see what will happen first.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="What are you updating?" className="min-w-56">
          <NativeSelect value={type} onChange={(e) => change(e.target.value)}>
            {LIST_TYPES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8"
          disabled={!templates.data?.[type]}
          onClick={() => setText(templates.data?.[type] ?? '')}
        >
          Load sample rows
        </Button>
        <Button type="button" variant="outline" size="sm" className="h-8" onClick={() => fileRef.current?.click()}>
          <FileUp /> Choose a CSV file
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              void f.text().then((t) => {
                setText(t);
                setPreview(null);
                setDone(null);
              });
            }
            e.target.value = '';
          }}
        />
      </div>
      <Field label="Rows (the first line is the column headings)">
        <Textarea
          rows={8}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPreview(null);
          }}
          placeholder="Paste rows from your spreadsheet here, or choose a file."
          spellCheck={false}
          className="font-mono text-xs"
        />
      </Field>
      {!preview && (
        <Button className="justify-self-start" disabled={!text.trim() || check.isPending} onClick={() => check.mutate()}>
          {check.isPending && <Loader2 className="animate-spin" />}
          Check the file
        </Button>
      )}

      {preview && (
        <div className="grid gap-3 rounded-md bg-muted/40 p-3" role="status">
          <p className="text-sm font-medium">Here is what will happen</p>
          <p className="text-[13px]">
            {preview.new} new, {preview.changed} changed, {preview.skipped} skipped.
          </p>
          <RowList title="New" note="These will be added." rows={rows('new')} />
          <RowList title="Changed" note="These will be changed. Old value, then new value." rows={rows('changed')} />
          <RowList title="Skipped" note="These will not be used. Each row says why." rows={rows('skipped')} />
          {(preview.errors?.length ?? 0) > 0 && (
            <p className="text-[13px] text-warn">{preview.errors!.length} rows have a problem. Fix them in the file, or import the rest.</p>
          )}
          {batch && <p className="text-xs text-muted-foreground">This makes new orders and purchase orders.</p>}
          <p className="text-xs text-muted-foreground">Nothing has been saved yet.</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={todo === 0 || run.isPending} onClick={() => run.mutate()}>
              {run.isPending && <Loader2 className="animate-spin" />}
              {batch ? `Make ${todo} orders` : `Import ${todo} rows`}
            </Button>
            {todo === 0 && <span className="text-xs text-muted-foreground">Nothing to import.</span>}
            <Button variant="outline" onClick={() => setPreview(null)}>
              Choose another file
            </Button>
          </div>
        </div>
      )}

      {done && (
        <div role="status" className="grid gap-1 rounded-md bg-ok-soft p-3 text-[13px]">
          <p className="font-medium text-ok">
            Done. {done.records_created ?? 0} added, {done.records_updated ?? 0} changed,{' '}
            {Math.max(0, (done.rows_processed ?? 0) - (done.records_created ?? 0) - (done.records_updated ?? 0))} skipped. {label}
          </p>
          {(done.errors?.length ?? 0) > 0 && (
            <ul className="grid list-disc gap-0.5 pl-5 text-bad">
              {done.errors!.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
