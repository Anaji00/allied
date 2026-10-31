'use client';

import { useRef, useState } from 'react';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, FileUp, Loader2 } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { Field, NativeSelect, PanelHeading, SectionTabs } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import {
  downloadSageCsv,
  fetchCSVTemplates,
  fetchSageExportSummary,
  generateSageBatchBundle,
  importCSVData,
  unmarkSageExport,
  uploadCSVFile,
} from '@/lib/api';
import { money } from '@/lib/format';
import { useHealth } from '@/lib/queries';

const TABS = ['sage', 'lists'] as const;

export function SageImports() {
  const { hasRole } = useAuth();
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('sage'));
  if (!hasRole('MANAGER')) return <EmptyState title="Managers only">Sage import files and list imports are open to managers and admins.</EmptyState>;
  return (
    <div className="grid gap-4">
      <SectionTabs
        label="Sage and imports"
        value={tab}
        onChange={(t) => void setTab(t === 'sage' ? null : t)}
        tabs={[
          ['sage', 'Sage files'],
          ['lists', 'Update lists'],
        ]}
      />
      {tab === 'sage' ? <SageFiles /> : <ListImports />}
    </div>
  );
}

type Summary = { sales_orders_count?: number; total_sales_volume?: number; purchase_orders_count?: number; total_purchase_volume?: number };
type Kind = 'sales-orders' | 'purchase-orders';
const FILE: Record<Kind, string> = { 'sales-orders': 'SO_IMPORT.CSV', 'purchase-orders': 'PO_IMPORT.CSV' };

function SageFiles() {
  const client = useQueryClient();
  const { data: health } = useHealth();
  const [last, setLast] = useState<{ kind: Kind; orders: string[] } | null>(null);
  const summary = useQuery({
    queryKey: ['sage', 'summary'],
    queryFn: async () => (await fetchSageExportSummary()) as Summary,
    refetchInterval: 30_000,
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ['sage', 'summary'] });

  const download = useMutation({
    mutationFn: async (kind: Kind) => ({ kind, marked: (await downloadSageCsv(kind)) as string[] }),
    onSuccess: ({ kind, marked }) => {
      setLast(marked.length ? { kind, orders: marked } : null);
      toast.success(
        marked.length ? `${FILE[kind]} downloaded: ${marked.length} order(s), now marked as sent to Sage` : `${FILE[kind]} downloaded. Nothing new since the last file.`,
      );
      refresh();
    },
    onError: (e) => toast.error('The file wasn’t downloaded', { description: e.message }),
  });
  const putBack = useMutation({
    mutationFn: async () => (last ? ((await unmarkSageExport(last.kind, last.orders)) as { reset: string[] }) : { reset: [] }),
    onSuccess: (res) => {
      toast.success(`${res.reset.length} order(s) will be in the next ${last ? FILE[last.kind] : 'file'}`);
      setLast(null);
      refresh();
    },
    onError: (e) => toast.error('Couldn’t put them back', { description: e.message }),
  });
  const preview = useMutation({
    mutationFn: async () => ((await generateSageBatchBundle()) as { files?: Record<string, string> }).files ?? {},
    onError: (e) => toast.error('Couldn’t make the files', { description: e.message }),
  });

  if (summary.isPending) return <RowsSkeleton rows={4} />;
  if (summary.error) return <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />;
  const s = summary.data ?? {};

  return (
    <div className="grid max-w-4xl gap-4">
      <p className="text-sm text-muted-foreground">
        Ready-to-import files for Sage 50. Each file holds only orders not yet sent to Sage, so import every file you download.
        {health?.simulation_mode ? ' Sage is in practice mode, so nothing is written into Sage automatically.' : ''}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <FileCard
          title="Sales orders"
          facts={[
            ['Ready', String(s.sales_orders_count ?? 0)],
            ['Total', money(s.total_sales_volume ?? 0)],
          ]}
          label={`Download ${FILE['sales-orders']}`}
          busy={download.isPending && download.variables === 'sales-orders'}
          disabled={download.isPending}
          onDownload={() => download.mutate('sales-orders')}
        />
        <FileCard
          title="Supplier POs"
          facts={[
            ['Lines ready', String(s.purchase_orders_count ?? 0)],
            ['Payable', money(s.total_purchase_volume ?? 0)],
          ]}
          label={`Download ${FILE['purchase-orders']}`}
          busy={download.isPending && download.variables === 'purchase-orders'}
          disabled={download.isPending}
          onDownload={() => download.mutate('purchase-orders')}
        />
      </div>

      {last && (
        <div role="status" className="grid gap-2 rounded-md bg-info-soft p-3 text-[13px]">
          <p className="font-medium">
            {FILE[last.kind]}: {last.orders.length} order(s) marked as sent to Sage
          </p>
          <p className="font-mono text-xs break-words">{last.orders.join(', ')}</p>
          <Button size="sm" variant="outline" className="justify-self-start" disabled={putBack.isPending} onClick={() => putBack.mutate()}>
            {putBack.isPending && <Loader2 className="animate-spin" />}
            File didn’t save? Put them back
          </Button>
        </div>
      )}

      <div className="grid gap-2 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <PanelHeading>Preview the files</PanelHeading>
          <Button size="sm" variant="outline" className="ml-auto" disabled={preview.isPending} onClick={() => preview.mutate()}>
            {preview.isPending && <Loader2 className="animate-spin" />}
            Show what Sage will import
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">Sales post to 40000-00-FAB and steel materials to 50000-00-STEEL.</p>
        {preview.data && (
          <div className="grid gap-3 lg:grid-cols-2">
            {Object.entries(FILE).map(([, name]) => (
              <div key={name} className="grid gap-1">
                <span className="font-mono text-xs">{name}</span>
                <pre className="max-h-72 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-[11px]">{preview.data[name] || 'Empty'}</pre>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function FileCard({
  title,
  facts,
  label,
  busy,
  disabled,
  onDownload,
}: {
  title: string;
  facts: [string, string][];
  label: string;
  busy: boolean;
  disabled: boolean;
  onDownload: () => void;
}) {
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      <Facts items={facts} />
      <Button size="sm" className="justify-self-start" disabled={disabled} onClick={onDownload}>
        {busy ? <Loader2 className="animate-spin" /> : <Download />}
        {label}
      </Button>
    </section>
  );
}

const LIST_TYPES = [
  ['ITEMS', 'Products'],
  ['CUSTOMERS', 'Customer accounts'],
  ['VENDORS', 'Suppliers'],
  ['ORDERS_BATCH', 'A batch of customer orders'],
] as const;

type ImportResult = { status: string; message?: string; rows_processed?: number; records_created?: number; records_updated?: number; errors?: string[] };

function ListImports() {
  const client = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [type, setType] = useState<string>('ITEMS');
  const [text, setText] = useState('');
  const templates = useQuery({
    queryKey: ['csv', 'templates'],
    queryFn: async () => ((await fetchCSVTemplates()) as { templates?: Record<string, string> }).templates ?? {},
    staleTime: Infinity,
  });
  const done = (res: ImportResult) => {
    void client.invalidateQueries({ queryKey: ['sage'] });
    if (res.status === 'SUCCESS') toast.success('Import finished');
    else toast.warning('Import finished with warnings');
  };
  const paste = useMutation({ mutationFn: async () => (await importCSVData(type, text)) as ImportResult, onSuccess: done });
  const upload = useMutation({ mutationFn: async (f: File) => (await uploadCSVFile(f, type)) as ImportResult, onSuccess: done });
  const result = upload.data ?? paste.data;
  const error = upload.error ?? paste.error;
  const busy = paste.isPending || upload.isPending;
  const label = LIST_TYPES.find(([v]) => v === type)?.[1] ?? '';

  // Switching type keeps pasted rows; the sample only replaces an empty box or the previous untouched sample.
  function changeType(next: string) {
    const untouched = !text.trim() || text === templates.data?.[type];
    setType(next);
    if (untouched) setText(templates.data?.[next] ?? '');
  }

  return (
    <form
      className="grid max-w-4xl gap-3 rounded-lg border bg-card p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) paste.mutate();
      }}
    >
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">Update lists from a spreadsheet</h2>
        <p className="text-xs text-muted-foreground">Add or change products, customers or suppliers, or bring in a batch of orders, from a CSV file.</p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="What are you updating?" className="min-w-56">
          <NativeSelect value={type} onChange={(e) => changeType(e.target.value)}>
            {LIST_TYPES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Button type="button" variant="outline" size="sm" className="h-8" disabled={!templates.data?.[type]} onClick={() => setText(templates.data?.[type] ?? '')}>
          Load sample rows
        </Button>
        <Button type="button" variant="outline" size="sm" className="h-8" disabled={busy} onClick={() => fileRef.current?.click()}>
          <FileUp /> Upload a CSV file
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = '';
          }}
        />
      </div>
      <Field label="Rows (the first line is the column headings)">
        <Textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste rows from your spreadsheet here…" spellCheck={false} className="font-mono text-xs" />
      </Field>
      <Button type="submit" className="justify-self-start" disabled={!text.trim() || busy}>
        {busy && <Loader2 className="animate-spin" />}
        Import {label.toLowerCase()}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error.message}
        </p>
      )}
      {result && (
        <div className="grid gap-1.5 rounded-md bg-muted/40 p-3 text-[13px]">
          <p className={result.status === 'SUCCESS' ? 'font-medium text-ok' : 'font-medium text-warn'}>
            {result.message} {result.rows_processed ?? 0} rows read, {result.records_created ?? 0} added, {result.records_updated ?? 0} updated.
          </p>
          {(result.errors?.length ?? 0) > 0 && (
            <ul className="grid list-disc gap-0.5 pl-5 text-bad">
              {result.errors!.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}
