'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery } from '@tanstack/react-query';
import { FileUp, Loader2 } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { Field, NativeSelect, PanelHeading } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button, buttonVariants } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { batchUploadDocuments, fetchDocumentSamples, uploadDocumentScan } from '@/lib/api';
import { money, words } from '@/lib/format';
import { cn } from '@/lib/utils';
import { CopyButton, pctOf } from './ai-output';


const DOC_TYPES = [
  ['AUTO', 'Work it out for me'],
  ['CUSTOMER_PO', 'Customer purchase order'],
  ['VENDOR_INVOICE', 'Supplier bill'],
  ['CAD_DRAWING', 'Drawing'],
  ['MTR_HEAT_CERT', 'Mill test certificate'],
  ['RFQ_PROPOSAL', 'Request for quote'],
  ['GENERAL_DOC', 'Other document'],
] as const;
const DOC_LABEL: Record<string, string> = Object.fromEntries(DOC_TYPES);
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tiff,.txt';

// A note only counts as a pass if it reads like one: "could not read the
// drawing number" next to an OK is how a warning gets read as reassurance.
const PROBLEM_NOTE = /could not|cannot|not read|needs a person|no allied|must not|unresolved|by hand|does not resolve/i;
const clean = (t: string) => String(t || '').replace(/[\p{Extended_Pictographic}️]/gu, '').trim();
const display = (v: unknown) => (v == null || v === '' ? '–' : typeof v === 'object' ? JSON.stringify(v) : String(v));

type Sample = { sample_id: string; title: string; document_type: string; sample_filename: string; sample_text: string };

export function DocumentReader() {
  const { hasRole } = useAuth();
  const inputRef = useRef<HTMLInputElement>(null);
  const [docType, setDocType] = useState('AUTO');
  const [dragging, setDragging] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  const [notes, setNotes] = useState<string[]>([]);

  const samples = useQuery({
    queryKey: ['documents', 'samples'],
    queryFn: async () => (await fetchDocumentSamples()) as Sample[],
    staleTime: Infinity,
  });

  const scan = useMutation({
    mutationFn: async ({ files, type }: { files: File[]; type: string }) => {
      if (files.length === 1) return { scans: [await uploadDocumentScan(files[0], type)], notes: [] as string[] };
      const res = await batchUploadDocuments(files);
      return { scans: res.scans ?? [], notes: [...(res.batch_summary_notes ?? []), ...(res.failed_scans ? [`${res.failed_scans} file(s) couldn’t be read.`] : [])] };
    },
    onSuccess: (res) => {
      setResults(res.scans);
      setNotes(res.notes);
    },
  });

  const read = (files: File[], type = docType) => files.length && scan.mutate({ files, type });

  if (!hasRole('OPERATOR')) {
    // The scan route records what it reads, so it is open to operators and above.
    return <p className="text-sm text-muted-foreground">Reading documents needs an operator account.</p>;
  }

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
      <div className="grid gap-4">
        <section className="grid gap-3 rounded-lg border bg-card p-4">
          <h2 className="text-sm font-semibold">Read a document</h2>
          <Field label="What kind of document">
            <NativeSelect value={docType} onChange={(e) => setDocType(e.target.value)}>
              {DOC_TYPES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              read(Array.from(e.dataTransfer.files));
            }}
            disabled={scan.isPending}
            className={cn(
              'grid justify-items-center gap-1.5 rounded-lg border border-dashed px-4 py-8 text-center text-sm transition-colors',
              dragging ? 'border-foreground bg-muted' : 'hover:bg-muted/50',
            )}
          >
            {scan.isPending ? <Loader2 className="size-5 animate-spin" /> : <FileUp className="size-5 text-muted-foreground" />}
            <span className="font-medium">{scan.isPending ? 'Reading…' : 'Drop files here, or choose'}</span>
            <span className="text-xs text-muted-foreground">PDF, image or text. Several files are read together.</span>
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              read(Array.from(e.target.files ?? []));
              e.target.value = '';
            }}
          />
          {scan.error && (
            <p role="alert" className="text-sm text-bad">
              {scan.error.message}
            </p>
          )}
        </section>

        {(samples.data?.length ?? 0) > 0 && (
          <section className="grid gap-2 rounded-lg border bg-card p-4">
            <PanelHeading>Try an example</PanelHeading>
            {samples.data!.map((s) => (
              <Button
                key={s.sample_id}
                variant="outline"
                size="sm"
                className="h-auto justify-start py-1.5 text-left whitespace-normal"
                disabled={scan.isPending}
                onClick={() => read([new File([s.sample_text], s.sample_filename, { type: 'text/plain' })], s.document_type)}
              >
                {s.title}
              </Button>
            ))}
          </section>
        )}
      </div>

      <div className="grid gap-4">
        {notes.length > 0 && (
          <ul className="grid gap-1 rounded-md bg-muted/50 p-3 text-[13px]">
            {notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        )}
        {results.length === 0 ? (
          <div className="grid justify-items-center gap-1.5 rounded-lg border border-dashed px-6 py-16 text-center">
            <p className="text-sm font-medium">What the reader finds appears here</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Customer POs, supplier bills, drawings and mill certificates. Bills are checked against their purchase order.
            </p>
          </div>
        ) : (
          results.map((r, i) => <ScanResult key={r.scan_id ?? i} result={r} />)
        )}
      </div>
    </div>
  );
}

function ScanResult({ result }: { result: any }) {
  const fields = Object.entries(result.fields ?? {}) as [string, any][];
  const lines: any[] = result.line_items ?? [];
  const checks: string[] = result.compliance_notes ?? [];
  const next: any[] = result.suggested_actions ?? [];
  const rec = result.reconciliation;

  return (
    <article className="grid gap-4 rounded-lg border bg-card p-4">
      <header className="flex flex-wrap items-start gap-2">
        <div className="grid min-w-0 gap-0.5">
          <h2 className="truncate text-sm font-semibold">{result.filename}</h2>
          <p className="text-xs text-muted-foreground">Read with {pctOf(result.confidence_score)} confidence</p>
        </div>
        <ToneBadge tone="info" className="ml-auto">
          {DOC_LABEL[result.document_type] ?? words(result.document_type) ?? 'Document'}
        </ToneBadge>
      </header>

      {rec && <Reconciliation rec={rec} />}

      {fields.length > 0 ? (
        <div className="grid gap-1.5">
          <PanelHeading>Details found</PanelHeading>
          <Facts
            items={fields.map(([key, f]) => [
              words(f.field_name || key),
              <span key={key}>
                {display(f.normalized_value ?? f.raw_value)}
                {Number(f.confidence) < 0.8 && (
                  <ToneBadge tone="warn" className="ml-1.5">
                    Please check
                  </ToneBadge>
                )}
              </span>,
            ])}
          />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No labelled details were found.</p>
      )}

      {lines.length > 0 && (
        <div className="grid gap-1.5">
          <PanelHeading>Items found ({lines.length})</PanelHeading>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-[13px]">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 font-medium">Part</th>
                  <th className="py-1 font-medium">Description</th>
                  <th className="py-1 text-right font-medium">Qty</th>
                  <th className="py-1 text-right font-medium">Unit price</th>
                  <th className="py-1 pl-3 font-medium">Supplier</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {lines.map((l, i) => (
                  <tr key={i} className="align-top">
                    <td className="py-1.5 pr-2 font-mono text-xs">{l.item_id || 'Custom'}</td>
                    <td className="py-1.5 pr-2">
                      {l.description}
                      {(l.dimensions || l.drawing_ref || l.heat_number) && (
                        <span className="block text-xs text-muted-foreground">
                          {[l.dimensions, l.drawing_ref && `Drawing ${l.drawing_ref}`, l.heat_number && `Heat ${l.heat_number}`].filter(Boolean).join(' · ')}
                        </span>
                      )}
                      {Number(l.confidence) < 0.8 && <ToneBadge tone="warn">Please check</ToneBadge>}
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{Number(l.quantity)}</td>
                    <td className="py-1.5 text-right tabular-nums">{l.unit_price ? money(l.unit_price) : '–'}</td>
                    <td className="py-1.5 pl-3">{l.preferred_vendor || 'Stock'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {checks.length > 0 && (
        <div className="grid gap-1.5">
          <PanelHeading>Checks</PanelHeading>
          <ul className="grid gap-1 text-[13px]">
            {checks.map((note, i) => {
              const problem = PROBLEM_NOTE.test(note);
              return (
                <li key={i} className="flex items-start gap-2">
                  <ToneBadge tone={problem ? 'warn' : 'ok'} className="mt-px">
                    {problem ? 'Check' : 'OK'}
                  </ToneBadge>
                  <span>{note}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {next.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {next.map((a, i) => (
            <Link key={i} href={a.route_url} className={buttonVariants({ size: 'sm', variant: i === 0 ? 'default' : 'outline' })}>
              {clean(a.label)}
            </Link>
          ))}
        </div>
      )}

      <details className="text-[13px]">
        <summary className="cursor-pointer text-muted-foreground">Everything the reader picked up, word for word</summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-muted/50 p-3 font-mono text-xs whitespace-pre-wrap">
          {result.raw_transcribed_text || 'No text was read.'}
        </pre>
        {result.raw_transcribed_text && <CopyButton text={result.raw_transcribed_text} label="Copy text" />}
      </details>
      <p className="text-xs text-muted-foreground">
        Reference {result.scan_id}
        {result.processing_time_ms != null && ` · read in ${Math.round(result.processing_time_ms)} ms`}
        {result.ocr_engine && ` · ${result.ocr_engine}`}
      </p>
    </article>
  );
}

const REC_TITLE: Record<string, string> = {
  RECONCILED_APPROVED: 'Matches the purchase order. Safe to pay.',
  DUPLICATE_INVOICE: 'This bill was already approved once. Payment held.',
  VENDOR_MISMATCH: 'This bill isn’t from the PO’s supplier. Payment held.',
  UNMATCHED_NO_PO: 'No known purchase order on this bill. Payment held.',
};

function Reconciliation({ rec }: { rec: any }) {
  const ok = rec.reconciliation_status === 'RECONCILED_APPROVED';
  const title =
    REC_TITLE[rec.reconciliation_status] ??
    (Number(rec.cost_variance_amount) > 0 ? `Overbilled by ${money(rec.cost_variance_amount)}. Payment held.` : 'Doesn’t match the purchase order. Payment held.');
  return (
    <div className={cn('grid gap-1.5 rounded-md p-3 text-[13px]', ok ? 'bg-ok-soft' : 'bg-bad-soft')}>
      <p className={cn('font-medium', ok ? 'text-ok' : 'text-bad')}>{title}</p>
      <p className="text-muted-foreground">
        PO {rec.po_number || '–'} · approved {money(rec.authorized_po_total || 0)} · billed {money(rec.actual_billed_total || 0)}
      </p>
      {rec.discrepancies?.length > 0 && (
        <ul className="grid list-disc gap-0.5 pl-5">
          {rec.discrepancies.map((d: string, i: number) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}
      {rec.dispute_email_draft && (
        <p className="text-muted-foreground">
          A dispute letter has been written; see{' '}
          <Link className="underline underline-offset-2" href="/health?tab=automation">
            mail the system has written
          </Link>
          .
        </p>
      )}
    </div>
  );
}
