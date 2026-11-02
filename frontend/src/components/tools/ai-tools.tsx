'use client';

import { useDeferredValue, useState, type ReactNode } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeftRight, Loader2, Mic, Square } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { Field, FilterChips, NativeSelect, PanelHeading } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { useDictation } from '@/hooks/use-dictation';
import {
  aiCompareDrawings,
  aiDraftEmail,
  aiSuggestItem,
  aiSummarizeCall,
  aiTranslate,
  aiTriageEmail,
} from '@/lib/api';
import { api, ok } from '@/lib/api/client';
import { dateTime } from '@/lib/format';
import { AiOutput, CallView, CopyButton, DraftView, DrawingView, FEATURE_LABEL, TriageView, pctOf } from './ai-output';


const LANGS = [
  ['en', 'English'],
  ['vi', 'Vietnamese'],
  ['zh', 'Mandarin'],
] as const;

const SLOW = 'The model can take up to a minute.';

/** Two columns on wide screens: what you give it, and what comes back. */
function Split({ left, right }: { left: ReactNode; right: ReactNode }) {
  return <div className="grid items-start gap-4 lg:grid-cols-2">{left}{right}</div>;
}

function Panel({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {description && <p className="text-xs text-pretty text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function Working({ busy, error, empty, children }: { busy: boolean; error: Error | null; empty: string; children: ReactNode }) {
  if (busy)
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Working… {SLOW}
      </p>
    );
  if (error)
    return (
      <p role="alert" className="text-sm text-bad">
        {error.message}
      </p>
    );
  return children ?? <p className="text-sm text-muted-foreground">{empty}</p>;
}

function Dictate({ language, onText }: { language: string; onText: (t: string) => void }) {
  const { listening, error, start, stop } = useDictation(onText);
  return (
    <span className="inline-flex items-center gap-2">
      <Button type="button" size="sm" variant={listening ? 'destructive' : 'outline'} onClick={() => (listening ? stop() : start(language))}>
        {listening ? <Square /> : <Mic />}
        {listening ? 'Stop' : 'Dictate'}
      </Button>
      {listening && <span className="text-xs text-muted-foreground">Listening…</span>}
      {error && <span className="text-xs text-bad">{error}</span>}
    </span>
  );
}

const append = (set: (fn: (prev: string) => string) => void) => (t: string) => set((prev) => (prev ? `${prev} ${t}` : t));

/* ------------------------------------------------------------- translate */

export function TranslateTool() {
  const [text, setText] = useState('');
  const [source, setSource] = useState('en');
  const [target, setTarget] = useState('vi');
  const run = useMutation({ mutationFn: () => aiTranslate(text, source, target) as Promise<{ result: any }> });
  const result = run.data?.result;

  return (
    <Split
      left={
        <Panel title="Text to translate" description="For emails, notes and instructions. Safety-critical documents keep an official version checked by a bilingual reviewer.">
          <div className="flex items-end gap-2">
            <Field label="From" className="flex-1">
              <NativeSelect value={source} onChange={(e) => setSource(e.target.value)}>
                {LANGS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Swap languages"
              onClick={() => {
                setSource(target);
                setTarget(source);
              }}
            >
              <ArrowLeftRight />
            </Button>
            <Field label="To" className="flex-1">
              <NativeSelect value={target} onChange={(e) => setTarget(e.target.value)}>
                {LANGS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
          <Field label="Text">
            <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder="Type, paste or dictate…" />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Dictate language={source} onText={append(setText)} />
            <Button className="ml-auto" disabled={!text.trim() || source === target || run.isPending} onClick={() => run.mutate()}>
              {run.isPending && <Loader2 className="animate-spin" />}
              Translate
            </Button>
          </div>
        </Panel>
      }
      right={
        <Panel title="Translation">
          <Working busy={run.isPending} error={run.error} empty="The translation appears here.">
            {result && (
              <>
                <p className="text-sm whitespace-pre-wrap">{result.translation}</p>
                {result.unsure_terms?.length > 0 && <p className="text-xs text-warn">Check these terms: {result.unsure_terms.join(', ')}</p>}
                <CopyButton text={result.translation} />
              </>
            )}
          </Working>
        </Panel>
      }
    />
  );
}

/* ----------------------------------------------------------------- email */

const DRAFT_KINDS = [
  ['order_acknowledgement', 'Order received'],
  ['delivery_update', 'Delivery update'],
  ['supplier_chaser', 'Chase a supplier'],
  ['invoice_dispute', 'Dispute a bill'],
  ['general_reply', 'General reply'],
] as const;

export function EmailTool() {
  const { hasRole } = useAuth();
  const [mail, setMail] = useState({ sender: '', subject: '', body: '' });
  const [draft, setDraft] = useState({ kind: 'order_acknowledgement', language: 'en', recipient: '', facts: '' });
  const triage = useMutation({ mutationFn: () => aiTriageEmail(mail.sender, mail.subject, mail.body) as Promise<{ result: any }> });
  const drafted = useMutation({
    mutationFn: () => aiDraftEmail(draft.kind, draft.facts, draft.language, draft.recipient) as Promise<{ result: any }>,
  });

  if (!hasRole('OPERATOR')) return <EmptyState title="Reading and drafting email needs an operator account" />;

  return (
    <Split
      left={
        <Panel title="Read an email" description="Sorts it and pulls out any order. Orders and anything unsure wait in the Inbox for a person.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="From">
              <Input value={mail.sender} onChange={(e) => setMail({ ...mail, sender: e.target.value })} placeholder="buyer@customer.com" />
            </Field>
            <Field label="Subject">
              <Input value={mail.subject} onChange={(e) => setMail({ ...mail, subject: e.target.value })} />
            </Field>
          </div>
          <Field label="Message">
            <Textarea rows={7} value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} />
          </Field>
          <Button className="justify-self-start" disabled={!mail.body.trim() || triage.isPending} onClick={() => triage.mutate()}>
            {triage.isPending && <Loader2 className="animate-spin" />}
            Read email
          </Button>
          <Working busy={triage.isPending} error={triage.error} empty="">
            {triage.data && <TriageView result={triage.data.result} />}
          </Working>
        </Panel>
      }
      right={
        <Panel title="Draft an email" description="Give it the facts; the draft uses nothing else. You check it and send it yourself.">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Kind">
              <NativeSelect value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
                {DRAFT_KINDS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Language">
              <NativeSelect value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value })}>
                {LANGS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
          <Field label="To">
            <Input value={draft.recipient} onChange={(e) => setDraft({ ...draft, recipient: e.target.value })} placeholder="Name or company" />
          </Field>
          <Field label="Facts to include" hint="Order numbers, dates, quantities. Anything missing comes back as [TO CONFIRM].">
            <Textarea rows={5} value={draft.facts} onChange={(e) => setDraft({ ...draft, facts: e.target.value })} />
          </Field>
          <Button className="justify-self-start" disabled={!draft.facts.trim() || drafted.isPending} onClick={() => drafted.mutate()}>
            {drafted.isPending && <Loader2 className="animate-spin" />}
            Draft email
          </Button>
          <Working busy={drafted.isPending} error={drafted.error} empty="">
            {drafted.data && <DraftView result={drafted.data.result} />}
          </Working>
        </Panel>
      }
    />
  );
}

/* -------------------------------------------------------------- drawings */

export function DrawingTool() {
  const [master, setMaster] = useState<File | null>(null);
  const [supplier, setSupplier] = useState<File | null>(null);
  const run = useMutation({ mutationFn: () => aiCompareDrawings(master, supplier) as Promise<{ result: any }> });

  return (
    <Panel
      title="Check a supplier drawing against ours"
      description="The AI reads both drawings; the comparison itself is fixed rules, so the same drawings always give the same result. Reading two drawings can take a couple of minutes."
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Our master drawing" hint={master?.name}>
          <Input type="file" accept=".pdf,image/*" onChange={(e) => setMaster(e.target.files?.[0] ?? null)} />
        </Field>
        <Field label="Supplier’s drawing" hint={supplier?.name}>
          <Input type="file" accept=".pdf,image/*" onChange={(e) => setSupplier(e.target.files?.[0] ?? null)} />
        </Field>
      </div>
      <Button className="justify-self-start" disabled={!master || !supplier || run.isPending} onClick={() => run.mutate()}>
        {run.isPending && <Loader2 className="animate-spin" />}
        Compare drawings
      </Button>
      <Working busy={run.isPending} error={run.error} empty="">
        {run.data && <DrawingView result={run.data.result} />}
      </Working>
    </Panel>
  );
}

/* ----------------------------------------------------------------- calls */

export function CallTool({ recordingsReady }: { recordingsReady?: boolean }) {
  const { hasRole } = useAuth();
  const [transcript, setTranscript] = useState('');
  const [from, setFrom] = useState('');
  const run = useMutation({
    mutationFn: () =>
      aiSummarizeCall(transcript, from) as Promise<{ result: any; matched_orders?: { sales_order_number: string; customer_po_number: string; status: string }[] }>,
  });

  if (!hasRole('OPERATOR')) return <EmptyState title="Summarising calls needs an operator account" />;

  return (
    <Split
      left={
        <Panel
          title="Summarise a call"
          description="Paste or dictate what was said. A summary that names an order waits in the Inbox; approving it adds the note to that job."
        >
          <Field label="Caller’s number">
            <Input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="+1 555 0100" />
          </Field>
          <Field label="What was said">
            <Textarea rows={8} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Dictate language="en" onText={append(setTranscript)} />
            <Button className="ml-auto" disabled={!transcript.trim() || run.isPending} onClick={() => run.mutate()}>
              {run.isPending && <Loader2 className="animate-spin" />}
              Summarise
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            A phone system can post each call’s transcript to <code>/api/v1/ai/calls?token=…</code> (set PHONE_WEBHOOK_TOKEN). The AI
            never takes an order or quotes a price.{' '}
            {recordingsReady ? 'Recordings can be transcribed on this server.' : 'Transcribing recordings on the server is off (needs faster-whisper).'}
          </p>
        </Panel>
      }
      right={
        <Panel title="Summary">
          <Working busy={run.isPending} error={run.error} empty="The call summary appears here.">
            {run.data && (
              <>
                <CallView result={run.data.result} />
                {run.data.matched_orders?.length ? (
                  <p className="text-xs text-muted-foreground">
                    Matching order:{' '}
                    {run.data.matched_orders.map((o) => `${o.sales_order_number} (PO ${o.customer_po_number}, ${o.status})`).join('; ')}
                  </p>
                ) : null}
              </>
            )}
          </Working>
        </Panel>
      }
    />
  );
}

/* ----------------------------------------------------------------- parts */

type Candidate = { item_id: string; description: string; score: number };
const ccol = columnHelper<Candidate>();
const candidateColumns = ccol.columns([
  ccol.accessor('item_id', { header: 'Item', cell: (c) => <span className="font-mono text-xs">{c.getValue()}</span> }),
  ccol.accessor('description', { header: 'Description', meta: { className: 'max-w-96 truncate' } }),
  ccol.accessor('score', { header: 'Text match', meta: { align: 'right' } }),
]);

export function PartTool() {
  const { hasRole } = useAuth();
  const [raw, setRaw] = useState('');
  const run = useMutation({ mutationFn: () => aiSuggestItem(raw) as Promise<{ result: any; candidates: Candidate[] }> });
  if (!hasRole('OPERATOR')) return <EmptyState title="Matching part names needs an operator account" />;

  return (
    <Panel
      title="Which catalogue item did they mean?"
      description="For part names the normal matching is unsure about. Approve the suggestion in the Inbox and it’s remembered next time."
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (raw.trim().length >= 2) run.mutate();
        }}
      >
        <Field label="What the customer wrote" className="min-w-64 flex-1">
          <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="e.g. half inch 400 brinell sheet" />
        </Field>
        <Button type="submit" disabled={raw.trim().length < 2 || run.isPending}>
          {run.isPending && <Loader2 className="animate-spin" />}
          Suggest
        </Button>
      </form>
      <Working busy={run.isPending} error={run.error} empty="">
        {run.data && (
          <div className="grid gap-3">
            <p className={run.data.result.item_id ? 'text-sm' : 'text-sm text-warn'}>
              {run.data.result.item_id ? (
                <>
                  Suggested <span className="font-mono">{run.data.result.item_id}</span> ({pctOf(run.data.result.confidence)} sure), waiting in
                  the Inbox for approval.
                </>
              ) : (
                'No clear match.'
              )}{' '}
              <span className="text-muted-foreground">{run.data.result.reason}</span>
            </p>
            <DataTable label="Nearest catalogue items" columns={candidateColumns} data={run.data.candidates ?? []} getRowId={(c) => c.item_id} minWidth={480} />
          </div>
        )}
      </Working>
    </Panel>
  );
}

/* -------------------------------------------------------------- glossary */

type Term = { en: string; vi?: string; zh?: string; approved?: boolean };
const tcol = columnHelper<Term>();
const termColumns = tcol.columns([
  tcol.accessor('en', { header: 'English', meta: { className: 'font-medium' } }),
  tcol.accessor('vi', { header: 'Vietnamese', cell: (c) => c.getValue() || '–' }),
  tcol.accessor('zh', { header: 'Mandarin', cell: (c) => c.getValue() || '–' }),
  tcol.accessor('approved', {
    header: 'Checked',
    cell: (c) => <ToneBadge tone={c.getValue() ? 'ok' : 'neutral'}>{c.getValue() ? 'By a reviewer' : 'Not yet'}</ToneBadge>,
  }),
]);

export function GlossaryTool() {
  const [q, setQ] = useState('');
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['ai', 'glossary'],
    queryFn: async () => ((await ok(api.GET('/api/v1/ai/glossary'))) as { terms: Term[] }).terms,
    staleTime: 10 * 60_000,
  });
  // Filtering trails the typing slightly, so letters appear at once on a slow PC.
  const needle = useDeferredValue(q).trim().toLowerCase();
  const rows = (data ?? []).filter((t) => !needle || [t.en, t.vi, t.zh].some((v) => v?.toLowerCase().includes(needle)));

  return (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground">
        The engineering terms translation always uses. Terms not yet checked by a bilingual reviewer are used, but flagged.
      </p>
      <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a term" aria-label="Find a term" className="sm:w-72" />
      {isPending ? (
        <RowsSkeleton rows={5} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <DataTable
          label="Glossary"
          columns={termColumns}
          data={rows}
          getRowId={(t) => t.en}
          minWidth={560}
          empty={<EmptyState title={data?.length ? 'No term matches' : 'The glossary is empty'} />}
        />
      )}
    </div>
  );
}

/* --------------------------------------------------------------- history */

type Suggestion = {
  id: number;
  feature: string;
  status: string;
  title: string;
  created_at: string;
  confidence?: number | null;
  provider?: string;
  model?: string;
  output?: any;
};

const STATUS = {
  SUGGESTED: { label: 'Waiting', tone: 'warn' },
  APPROVED: { label: 'Approved', tone: 'ok' },
  REJECTED: { label: 'Rejected', tone: 'neutral' },
  INFO: { label: 'Done', tone: 'info' },
} as const;

const hcol = columnHelper<Suggestion>();
const historyColumns = hcol.columns([
  hcol.accessor('created_at', { header: 'When', cell: (c) => dateTime(c.getValue()) }),
  hcol.accessor('feature', { header: 'Kind', cell: (c) => FEATURE_LABEL[c.getValue()] ?? c.getValue() }),
  hcol.accessor('title', { header: 'About', meta: { className: 'max-w-96 truncate' } }),
  hcol.accessor('status', {
    header: 'Status',
    cell: (c) => {
      const s = STATUS[c.getValue() as keyof typeof STATUS] ?? { label: c.getValue(), tone: 'neutral' as const };
      return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
    },
  }),
]);

export function HistoryTool() {
  const [feature, setFeature] = useState('');
  const [open, setOpen] = useState<Suggestion | null>(null);
  const { data, isPending, error, refetch } = useQuery({
    queryKey: ['ai', 'history', feature],
    queryFn: async () =>
      ((await ok(api.GET('/api/v1/ai/suggestions', { params: { query: { feature: feature || undefined, limit: 100 } } }))) as { items: Suggestion[] }).items,
  });

  return (
    <div className="grid gap-3">
      <FilterChips
        label="Kind"
        value={feature}
        onChange={(v) => {
          setFeature(v);
          setOpen(null);
        }}
        options={[{ value: '', label: 'All' }, ...Object.entries(FEATURE_LABEL).map(([value, label]) => ({ value, label }))]}
      />
      <p className="text-xs text-muted-foreground">Everything the AI has produced. Results waiting for a person are decided in the Inbox.</p>
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]">
        {isPending ? (
          <RowsSkeleton rows={6} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : (
          <DataTable
            label="AI history"
            columns={historyColumns}
            data={data ?? []}
            getRowId={(s) => String(s.id)}
            selectedId={open ? String(open.id) : null}
            onSelect={(s) => setOpen(open?.id === s.id ? null : s)}
            minWidth={640}
            empty={<EmptyState title="No AI results yet" />}
          />
        )}
        {open && (
          <section className="grid gap-3 rounded-lg border bg-card p-4" aria-label="Result">
            <div className="grid gap-0.5">
              <h2 className="text-sm font-semibold">{open.title}</h2>
              <p className="text-xs text-muted-foreground">
                {FEATURE_LABEL[open.feature] ?? open.feature} · {dateTime(open.created_at)}
                {open.confidence != null && ` · ${pctOf(open.confidence)} sure`}
                {open.model && ` · ${open.model}`}
              </p>
            </div>
            <PanelHeading>What the AI produced</PanelHeading>
            <AiOutput feature={open.feature} output={open.output} />
          </section>
        )}
      </div>
    </div>
  );
}

export function useAiStatus() {
  return useQuery({
    queryKey: ['ai', 'status'],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/ai/status'))) as {
        engine?: { ready: boolean; provider: string; model: string; detail: string };
        recordings?: { ready: boolean };
        queue?: { waiting_for_review?: number };
      },
    staleTime: 60_000,
  });
}

export function AiStatusLine() {
  const { data, error } = useAiStatus();
  const engine = data?.engine;
  if (error) return <p className="text-xs text-bad">Couldn’t reach the AI service: {error.message}</p>;
  if (!engine) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <ToneBadge tone={engine.ready ? 'ok' : 'warn'}>{engine.ready ? 'AI ready' : 'AI not ready'}</ToneBadge>
      <span>
        {engine.detail || `${engine.provider} ${engine.model}.`} The AI reads, translates and drafts; a person approves anything that commits
        money, a date or a specification.
      </span>
    </p>
  );
}
