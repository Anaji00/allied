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
import { downloadSageCsv, fetchCSVTemplates, fetchSageExportSummary, generateSageBatchBundle, unmarkSageExport } from '@/lib/api';
import { money } from '@/lib/format';
import { BringInFromSage } from './sage-bring-in';
import { ListImport } from './list-import';
import { useHealth } from '@/lib/queries';

const TABS = ['sage', 'lists', 'bring'] as const;

export function SageImports({ param = 'tab' }: { param?: string }) {
  const { hasRole } = useAuth();
  const [tab, setTab] = useQueryState(param, parseAsStringLiteral(TABS).withDefault('sage'));
  if (!hasRole('MANAGER'))
    return <EmptyState title="Managers only">Sage import files and list imports are open to managers and admins.</EmptyState>;
  return (
    <div className="grid gap-4">
      <SectionTabs
        label="Sage and imports"
        value={tab}
        onChange={(t) => void setTab(t === 'sage' ? null : t)}
        tabs={[
          ['sage', 'Sage files'],
          ['lists', 'Update lists'],
          ['bring', 'Bring in from Sage'],
        ]}
      />
      {tab === 'sage' ? <SageFiles /> : tab === 'lists' ? <ListImport /> : <BringInFromSage />}
    </div>
  );
}

/** The server writes "about USD 151,993.12"; on screen it reads "about $151,993.12", like every other converted amount. */
const plainUsd = (t?: string) => (t ? t.replace(/about USD ([\d,]+(?:\.\d+)?)/g, 'about $$$1') : t);

type Summary = {
  sales_orders_count?: number;
  total_sales_volume?: number;
  purchase_orders_count?: number;
  total_purchase_volume?: number;
  /** The totals worded by the server, one amount per currency. */
  sales_label?: string;
  purchase_label?: string;
};
type Kind = 'sales-orders' | 'purchase-orders';
const FILE: Record<Kind, string> = { 'sales-orders': 'SO_IMPORT.CSV', 'purchase-orders': 'PO_IMPORT.CSV' };

const SAGE_LAST_KEY = 'allied.sage.last';

function SageFiles() {
  const client = useQueryClient();
  const { data: health } = useHealth();
  const [last, setLastState] = useState<{ kind: Kind; orders: string[] } | null>(() => {
    try {
      const raw = typeof window !== 'undefined' ? sessionStorage.getItem(SAGE_LAST_KEY) : null;
      return raw ? (JSON.parse(raw) as { kind: Kind; orders: string[] }) : null;
    } catch {
      return null;
    }
  });
  const setLast = (next: { kind: Kind; orders: string[] } | null) => {
    setLastState(next);
    try {
      if (next) sessionStorage.setItem(SAGE_LAST_KEY, JSON.stringify(next));
      else sessionStorage.removeItem(SAGE_LAST_KEY);
    } catch {
      // ignore storage errors
    }
  };
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
        marked.length
          ? `${FILE[kind]} downloaded: ${marked.length} order(s), now marked as sent to Sage`
          : `${FILE[kind]} downloaded. Nothing new since the last file.`,
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
            ['Total', plainUsd(s.sales_label) ?? `about ${money(s.total_sales_volume ?? 0)}`],
          ]}
          label="Download and mark as sent to Sage: sales orders"
          busy={download.isPending && download.variables === 'sales-orders'}
          disabled={download.isPending}
          onDownload={() => {
            if (window.confirm(`This marks ${s.sales_orders_count ?? 0} sales order(s) as sent to Sage. Continue?`))
              download.mutate('sales-orders');
          }}
        />
        <FileCard
          title="Supplier POs"
          facts={[
            ['Lines ready', String(s.purchase_orders_count ?? 0)],
            ['Payable', plainUsd(s.purchase_label) ?? `about ${money(s.total_purchase_volume ?? 0)}`],
          ]}
          label="Download and mark as sent to Sage: supplier POs"
          busy={download.isPending && download.variables === 'purchase-orders'}
          disabled={download.isPending}
          onDownload={() => {
            if (window.confirm(`This marks ${s.purchase_orders_count ?? 0} supplier PO line(s) as sent to Sage. Continue?`))
              download.mutate('purchase-orders');
          }}
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
            Preview what Sage will bring in
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Sales go to the fabrication sales account in Sage, and steel materials to the steel materials account.
        </p>
        {preview.data && (
          <div className="grid gap-3 lg:grid-cols-2">
            {Object.entries(FILE).map(([, name]) => (
              <div key={name} className="grid gap-1">
                <span className="font-mono text-xs">{name}</span>
                <pre className="max-h-72 overflow-auto rounded-md bg-muted/50 p-2 font-mono text-xs">{preview.data[name] || 'Empty'}</pre>
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
      <p className="text-xs text-muted-foreground">
        Pressing this makes the file and records these orders as sent to Sage, so they are not in the next file. To only look, use the
        preview below.
      </p>
    </section>
  );
}
