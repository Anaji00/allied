'use client';

import { Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Facts } from '@/components/data/detail-panel';
import { PanelHeading } from '@/components/data/filters';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { words } from '@/lib/format';
import { cn } from '@/lib/utils';


export const FEATURE_LABEL: Record<string, string> = {
  email_triage: 'Email read',
  email_draft: 'Email draft',
  translation: 'Translation',
  call_summary: 'Phone call',
  drawing_check: 'Drawing check',
  item_match: 'Part match',
  exception_explanation: 'Explanation',
};

export const pctOf = (v: unknown) => (v == null || v === '' ? '–' : `${Math.round(Number(v) * (Number(v) <= 1 ? 100 : 1))}%`);

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success('Copied');
  } catch {
    toast.error('Couldn’t copy; select the text and copy it instead');
  }
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  return (
    <Button size="sm" variant="outline" className="justify-self-start" onClick={() => void copyText(text)}>
      <Copy /> {label}
    </Button>
  );
}

/** An AI result in plain terms, whichever tool produced it. */
export function AiOutput({ feature, output }: { feature?: string; output: any }) {
  if (!output) return null;
  switch (feature) {
    case 'email_triage':
      return <TriageView result={output} />;
    case 'email_draft':
      return <DraftView result={output} />;
    case 'call_summary':
      return <CallView result={output} />;
    case 'drawing_check':
      return <DrawingView result={output} />;
    case 'translation':
      return <TranslationView result={output} />;
    case 'item_match':
      return (
        <Facts
          items={[
            ['Catalogue item', output.item_id || 'No match'],
            ['Why', output.reason || '–'],
            ['How sure', pctOf(output.confidence)],
          ]}
        />
      );
    case 'exception_explanation':
      return (
        <div className="grid gap-2 text-[13px]">
          {output.explanation && <p className="text-pretty">{output.explanation}</p>}
          {output.suggested_fix && (
            <p>
              <span className="font-medium">Suggested fix: </span>
              {output.suggested_fix}
            </p>
          )}
        </div>
      );
    default:
      return <Generic value={output} />;
  }
}

function Generic({ value }: { value: unknown }) {
  const rows: [string, string][] =
    value && typeof value === 'object' && !Array.isArray(value)
      ? Object.entries(value as Record<string, unknown>)
          .filter(([, v]) => v !== null && v !== undefined && v !== '')
          .map(([k, v]) => [words(k), typeof v === 'object' ? JSON.stringify(v) : String(v)])
      : [['Result', typeof value === 'string' ? value : JSON.stringify(value)]];
  return <Facts items={rows} />;
}

const CATEGORY: Record<string, { label: string; tone: Tone }> = {
  order: { label: 'Order', tone: 'ok' },
  quote_request: { label: 'Quote request', tone: 'info' },
  supplier_invoice: { label: 'Supplier bill', tone: 'neutral' },
  delivery_question: { label: 'Delivery question', tone: 'info' },
  complaint: { label: 'Complaint', tone: 'bad' },
  other: { label: 'Other', tone: 'neutral' },
  junk: { label: 'Junk', tone: 'neutral' },
};

export function TriageView({ result }: { result: any }) {
  const cat = CATEGORY[result.category] ?? { label: words(result.category), tone: 'neutral' as Tone };
  return (
    <div className="grid gap-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <ToneBadge tone={cat.tone}>{cat.label}</ToneBadge>
        <span className="text-xs text-muted-foreground">
          {pctOf(result.confidence)} sure{result.language ? ` · written in ${result.language}` : ''}
        </span>
      </div>
      {result.summary && <p className="text-pretty">{result.summary}</p>}
      <Facts
        items={[
          ['Customer', result.customer_name || '–'],
          ['PO number', result.po_number || '–'],
          ['Wanted by', result.requested_date || '–'],
        ]}
      />
      {result.lines?.length > 0 && (
        <table className="w-full text-[13px]">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 font-medium">Item</th>
              <th className="py-1 text-right font-medium">Qty</th>
              <th className="py-1 pl-2 font-medium">Unit</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {result.lines.map((l: any, i: number) => (
              <tr key={i}>
                <td className="py-1">{l.description}</td>
                <td className="py-1 text-right tabular-nums">{l.quantity ?? '–'}</td>
                <td className="py-1 pl-2">{l.unit ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {result.uncertain_fields?.length > 0 && <p className="text-xs text-warn">Not sure about: {result.uncertain_fields.join(', ')}</p>}
    </div>
  );
}

export function DraftView({ result }: { result: any }) {
  return (
    <div className="grid gap-2 text-[13px]">
      <p>
        <span className="text-muted-foreground">Subject: </span>
        {result.subject}
      </p>
      <pre className="rounded-md border bg-muted/40 p-3 font-sans whitespace-pre-wrap">{result.body}</pre>
      {result.states_commitment && <p className="text-xs text-warn">This draft mentions a price, date or promise. Check it before sending.</p>}
      <CopyButton text={`${result.subject}\n\n${result.body}`} />
    </div>
  );
}

function TranslationView({ result }: { result: any }) {
  return (
    <div className="grid gap-2 text-[13px]">
      <p className="whitespace-pre-wrap">{result.translation}</p>
      {result.unsure_terms?.length > 0 && <p className="text-xs text-warn">Check these terms: {result.unsure_terms.join(', ')}</p>}
    </div>
  );
}

const REASON: Record<string, string> = {
  order_status: 'Order status',
  new_order: 'New order',
  quote_request: 'Quote request',
  delivery: 'Delivery',
  complaint: 'Complaint',
  supplier: 'Supplier',
  other: 'Other',
};

export function CallView({ result }: { result: any }) {
  return (
    <div className="grid gap-2 text-[13px]">
      <div className="flex flex-wrap gap-1.5">
        <ToneBadge tone={result.reason === 'complaint' ? 'bad' : 'info'}>{REASON[result.reason] ?? words(result.reason)}</ToneBadge>
        {result.callback_needed && <ToneBadge tone="warn">Call back</ToneBadge>}
        {result.urgency === 'high' && <ToneBadge tone="bad">Urgent</ToneBadge>}
      </div>
      {result.summary && <p className="text-pretty">{result.summary}</p>}
      <Facts
        items={[
          ['Caller', [result.caller_name, result.company].filter(Boolean).join(', ') || '–'],
          ['Numbers mentioned', show(result.po_numbers)],
        ]}
      />
      {result.action_items?.length > 0 && (
        <ul className="grid list-disc gap-0.5 pl-5">
          {result.action_items.map((a: string, i: number) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const ROW: Record<string, { label: string; tone: Tone }> = {
  match: { label: 'Match', tone: 'ok' },
  different: { label: 'Different', tone: 'bad' },
  unreadable: { label: 'Couldn’t read', tone: 'warn' },
};

function show(value: unknown) {
  if (Array.isArray(value)) return value.join(', ') || '–';
  if (value === '' || value == null) return '–';
  return String(value);
}

export function DrawingView({ result }: { result: any }) {
  return (
    <div className="grid gap-3 text-[13px]">
      <p className={cn('rounded-md p-2.5', result.differences ? 'bg-warn-soft text-warn' : 'bg-ok-soft text-ok')}>
        {result.differences ? `${result.differences} difference(s) to check.` : 'No differences found.'} An engineer signs off every
        drawing; anything that couldn’t be read counts as a difference.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[480px] text-[13px]">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 font-medium">What</th>
              <th className="py-1 font-medium">Result</th>
              <th className="py-1 font-medium">Ours</th>
              <th className="py-1 font-medium">Supplier’s</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {(result.rows ?? []).map((r: any) => (
              <tr key={r.field} className="align-top">
                <td className="py-1.5 pr-2">{r.field}</td>
                <td className="py-1.5 pr-2">
                  <ToneBadge tone={ROW[r.status]?.tone ?? 'neutral'}>{ROW[r.status]?.label ?? r.status}</ToneBadge>
                </td>
                <td className="py-1.5 pr-2">{show(r.master)}</td>
                <td className="py-1.5">
                  {show(r.supplier)}
                  {r.note && <span className="block text-xs text-muted-foreground">{r.note}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.supplier_notes_english?.length > 0 && (
        <div className="grid gap-1">
          <PanelHeading>Supplier’s notes, in English</PanelHeading>
          <ul className="grid list-disc gap-0.5 pl-5">
            {result.supplier_notes_english.map((n: string, i: number) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
