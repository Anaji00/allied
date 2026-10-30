'use client';

import { useEffect, useState } from 'react';
import {
  Badge, Button, Card, Columns, DataTable, DetailList, EmptyState, Field, Input, Notice, Page, Select,
  Skeleton, Tabs, Textarea, Toolbar, dateTime,
} from '@/components/ui';
import {
  aiCompareDrawings, aiDraftEmail, aiSuggestItem, aiSummarizeCall, aiTranslate, aiTriageEmail,
  fetchAiStatus, fetchAiSuggestions, reviewAiSuggestion,
} from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { useDictation } from './useDictation';
import styles from './ai.module.css';

const TABS = [
  { key: 'review', label: 'To review' },
  { key: 'translate', label: 'Translate' },
  { key: 'email', label: 'Email' },
  { key: 'drawings', label: 'Drawing check' },
  { key: 'calls', label: 'Phone calls' },
  { key: 'parts', label: 'Part names' },
];

const FEATURE_LABEL = {
  email_triage: 'Email read',
  email_draft: 'Email draft',
  translation: 'Translation',
  call_summary: 'Phone call',
  drawing_check: 'Drawing check',
  item_match: 'Part match',
  exception_explanation: 'Explanation',
};

const LANGS = [
  { value: 'en', label: 'English' },
  { value: 'vi', label: 'Vietnamese' },
  { value: 'zh', label: 'Mandarin' },
];

const WORKING = 'Working… the local model can take up to a minute.';

/** Runs an async call and tracks busy / error / result for one tool. */
function useAction(fn) {
  const [state, setState] = useState({ busy: false, error: '', data: null });
  async function run(...args) {
    setState({ busy: true, error: '', data: null });
    try {
      const data = await fn(...args);
      setState({ busy: false, error: '', data });
    } catch (e) {
      setState({ busy: false, error: e.message, data: null });
    }
  }
  return [state, run];
}

function pct(value) {
  return value == null ? '–' : `${Math.round(value * 100)}%`;
}

export default function AiToolsPage() {
  const [tab, setTab] = useState('review');
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState('');

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const s = await fetchAiStatus();
        if (alive) setStatus(s);
      } catch (e) {
        if (alive) setStatusError(e.message);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, []);

  const engine = status?.engine;
  const waiting = status?.queue?.waiting_for_review;
  const tabs = TABS.map((t) => (t.key === 'review' && waiting ? { ...t, count: waiting } : t));

  return (
    <Page>
      {statusError && <Notice tone="danger" title="Could not reach the AI service">{statusError}</Notice>}
      {engine && (
        <Notice
          tone={engine.ready ? 'success' : 'warning'}
          title={engine.ready ? `AI is ready: ${engine.provider === 'ollama' ? 'local model' : engine.provider} ${engine.model}` : 'AI is not ready'}
        >
          {engine.detail} AI reads, translates and drafts; a person approves anything that commits money, a date or a specification.
        </Notice>
      )}
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {/* Every tool stays mounted and inactive ones are hidden, so a result
          (or a slow model call still running) survives switching tabs. */}
      <div hidden={tab !== 'review'}><ReviewQueue active={tab === 'review'} /></div>
      <div hidden={tab !== 'translate'}><TranslateTool /></div>
      <div hidden={tab !== 'email'}><EmailTools /></div>
      <div hidden={tab !== 'drawings'}><DrawingTool /></div>
      <div hidden={tab !== 'calls'}><CallTool recordingsReady={status?.recordings?.ready} /></div>
      <div hidden={tab !== 'parts'}><PartTool /></div>
    </Page>
  );
}

/* ---------------------------------------------------------------- review */

function ReviewQueue({ active }) {
  const { hasRole } = useAuth();
  const canAct = hasRole?.('OPERATOR');
  const [reloadKey, setReloadKey] = useState(0);
  const [data, setData] = useState(null);
  const [recent, setRecent] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(null);
  const [open, setOpen] = useState(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const [pending, all] = await Promise.all([
          fetchAiSuggestions({ status: 'SUGGESTED', limit: 100 }),
          fetchAiSuggestions({ limit: 30 }),
        ]);
        if (!alive) return;
        setData(pending.items);
        setRecent(all.items);
        setError('');
      } catch (e) {
        if (alive) setError(e.message);
      }
    }
    // The tab stays mounted while hidden, so it reloads each time it is shown:
    // the other tools add suggestions while it is out of sight.
    if (active) load();
    return () => {
      alive = false;
    };
  }, [reloadKey, active]);

  const retry = () => setReloadKey((k) => k + 1);

  async function decide(id, approve) {
    setBusy(id);
    try {
      await reviewAiSuggestion(id, approve);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Columns>
      <div>
        <Card title="Waiting for a person" description="Nothing here has been acted on. Approve to accept the suggestion, reject to discard it.">
          {error && data && <Notice tone="danger">{error}</Notice>}
          {!data ? (
            error ? <LoadError error={error} onRetry={retry} /> : <Skeleton />
          ) : data.length === 0 ? (
            <EmptyState title="Nothing to review." description="Orders, complaints, unsure reads, drawing checks and part matches land here." />
          ) : (
            <ul className={styles.queue}>
              {data.map((s) => (
                <li key={s.id} className={styles.queueItem}>
                  <div className={styles.queueHead}>
                    <Badge tone="info">{FEATURE_LABEL[s.feature] || s.feature}</Badge>
                    <button type="button" className={styles.queueTitle} onClick={() => setOpen(open === s.id ? null : s.id)}>
                      {s.title}
                    </button>
                  </div>
                  <div className={styles.meta}>
                    {dateTime(s.created_at)}
                    {s.confidence != null && ` · confidence ${pct(s.confidence)}`}
                    {` · ${s.model || s.provider}`}
                  </div>
                  {open === s.id && <OutputView feature={s.feature} output={s.output} />}
                  {canAct && (
                    <Toolbar className={styles.queueActions}>
                      <Button size="sm" variant="primary" loading={busy === s.id} onClick={() => decide(s.id, true)}>Approve</Button>
                      <Button size="sm" variant="ghost" disabled={busy === s.id} onClick={() => decide(s.id, false)}>Reject</Button>
                      {open !== s.id && <Button size="sm" variant="ghost" onClick={() => setOpen(s.id)}>Details</Button>}
                    </Toolbar>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div>
        <Card title="Recent AI results" flush>
          {!recent ? (
            <div className={styles.pad}>
              {error ? <LoadError error={error} onRetry={retry} /> : <Skeleton />}
            </div>
          ) : (
            <DataTable
              rows={recent}
              empty="No AI results yet."
              columns={[
                { key: 'feature', label: 'Kind', render: (r) => FEATURE_LABEL[r.feature] || r.feature },
                { key: 'status', label: 'Status', render: (r) => <StatusBadge status={r.status} /> },
                { key: 'created_at', label: 'When', render: (r) => dateTime(r.created_at) },
              ]}
            />
          )}
        </Card>
      </div>
    </Columns>
  );
}

function LoadError({ error, onRetry }) {
  return (
    <Notice tone="danger" title="Could not load the review queue" action={<Button size="sm" onClick={onRetry}>Retry</Button>}>
      {error}
    </Notice>
  );
}

function StatusBadge({ status }) {
  const tone = { SUGGESTED: 'warning', APPROVED: 'success', REJECTED: 'neutral', INFO: 'info' }[status] || 'neutral';
  const label = { SUGGESTED: 'Waiting', APPROVED: 'Approved', REJECTED: 'Rejected', INFO: 'Done' }[status] || status;
  return <Badge tone={tone}>{label}</Badge>;
}

/** Shows an AI result in plain terms, whichever tool produced it. */
function OutputView({ feature, output }) {
  if (!output) return null;
  if (feature === 'email_triage') return <TriageView result={output} />;
  if (feature === 'email_draft') return <DraftView result={output} />;
  if (feature === 'call_summary') return <CallView result={output} />;
  if (feature === 'drawing_check') return <DrawingView result={output} />;
  if (feature === 'translation') return <p className={styles.prose}>{output.translation}</p>;
  if (feature === 'item_match') {
    return (
      <DetailList items={[['Catalogue item', output.item_id || 'No match'], ['Why', output.reason], ['Confidence', pct(output.confidence)]]} />
    );
  }
  return <pre className={styles.pre}>{JSON.stringify(output, null, 2)}</pre>;
}

/* ------------------------------------------------------------- translate */

function DictateButton({ language, onText }) {
  const { listening, error, start, stop } = useDictation(onText);
  return (
    <span className={styles.dictate}>
      <Button size="sm" variant={listening ? 'danger' : 'secondary'} onClick={() => (listening ? stop() : start(language))}>
        {listening ? 'Stop dictating' : 'Dictate'}
      </Button>
      {listening && <span className={styles.listening}>Listening…</span>}
      {error && <span className={styles.dictateError}>{error}</span>}
    </span>
  );
}

function TranslateTool() {
  const [text, setText] = useState('');
  const [source, setSource] = useState('en');
  const [target, setTarget] = useState('vi');
  const [state, run] = useAction(aiTranslate);
  const result = state.data?.result;

  function swap() {
    setSource(target);
    setTarget(source);
  }

  return (
    <Columns side="left">
      <div>
        <Card title="Text to translate" description="For reading emails, notes and instructions. Safety-critical documents keep an official version checked by a bilingual reviewer.">
          <div className={styles.form}>
            <Toolbar>
              <Field label="From"><Select value={source} onChange={(e) => setSource(e.target.value)} options={LANGS} /></Field>
              <Button size="sm" variant="ghost" onClick={swap} aria-label="Swap languages">⇄</Button>
              <Field label="To"><Select value={target} onChange={(e) => setTarget(e.target.value)} options={LANGS} /></Field>
            </Toolbar>
            <Field label="Text">
              <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Type, paste or dictate…" rows={8} />
            </Field>
            <Toolbar>
              <DictateButton language={source} onText={(t) => setText((prev) => (prev ? `${prev} ${t}` : t))} />
              <Button variant="primary" loading={state.busy} disabled={!text.trim() || source === target} onClick={() => run(text, source, target)}>
                Translate
              </Button>
            </Toolbar>
          </div>
        </Card>
      </div>
      <div>
        <Card title="Translation">
          {state.busy && <Notice tone="info">{WORKING}</Notice>}
          {state.error && <Notice tone="danger">{state.error}</Notice>}
          {!state.busy && !state.error && !result && <EmptyState title="The translation appears here." />}
          {result && (
            <div className={styles.form}>
              <p className={styles.prose}>{result.translation}</p>
              {result.unsure_terms?.length > 0 && (
                <Notice tone="warning" title="Check these terms">{result.unsure_terms.join(', ')}</Notice>
              )}
              <Toolbar>
                <Button size="sm" onClick={() => navigator.clipboard?.writeText(result.translation)}>Copy</Button>
              </Toolbar>
            </div>
          )}
        </Card>
      </div>
    </Columns>
  );
}

/* ----------------------------------------------------------------- email */

const DRAFT_KINDS = [
  { value: 'order_acknowledgement', label: 'Order received' },
  { value: 'delivery_update', label: 'Delivery update' },
  { value: 'supplier_chaser', label: 'Chase a supplier' },
  { value: 'invoice_dispute', label: 'Dispute a bill' },
  { value: 'general_reply', label: 'General reply' },
];

const CATEGORY = {
  order: ['Order', 'accent'],
  quote_request: ['Quote request', 'info'],
  supplier_invoice: ['Supplier bill', 'neutral'],
  delivery_question: ['Delivery question', 'info'],
  complaint: ['Complaint', 'danger'],
  other: ['Other', 'neutral'],
  junk: ['Junk', 'neutral'],
};

function TriageView({ result }) {
  const [label, tone] = CATEGORY[result.category] || [result.category, 'neutral'];
  return (
    <div className={styles.form}>
      <Toolbar>
        <Badge tone={tone}>{label}</Badge>
        <span className={styles.meta}>confidence {pct(result.confidence)} · language {result.language}</span>
      </Toolbar>
      <p className={styles.prose}>{result.summary}</p>
      <DetailList items={[
        ['Customer', result.customer_name || '–'],
        ['PO number', result.po_number || '–'],
        ['Wanted by', result.requested_date || '–'],
      ]} />
      {result.lines?.length > 0 && (
        <DataTable
          rows={result.lines}
          columns={[
            { key: 'description', label: 'Item' },
            { key: 'quantity', label: 'Qty', align: 'right' },
            { key: 'unit', label: 'Unit' },
          ]}
        />
      )}
      {result.uncertain_fields?.length > 0 && <Notice tone="warning" title="Not sure about">{result.uncertain_fields.join(', ')}</Notice>}
    </div>
  );
}

function DraftView({ result }) {
  return (
    <div className={styles.form}>
      <DetailList items={[['Subject', result.subject]]} />
      <pre className={styles.letter}>{result.body}</pre>
      {result.states_commitment && <Notice tone="warning">This draft mentions a price, date or promise. Check it before sending.</Notice>}
      <Toolbar>
        <Button size="sm" onClick={() => navigator.clipboard?.writeText(`${result.subject}\n\n${result.body}`)}>Copy</Button>
      </Toolbar>
    </div>
  );
}

function EmailTools() {
  const [mail, setMail] = useState({ sender: '', subject: '', body: '' });
  const [draft, setDraft] = useState({ kind: 'order_acknowledgement', language: 'en', recipient: '', facts: '' });
  const [triage, runTriage] = useAction(aiTriageEmail);
  const [drafted, runDraft] = useAction(aiDraftEmail);

  return (
    <Columns side="left">
      <div>
        <Card title="Read an email" description="Sorts it and pulls out any order. Orders and anything unsure go to the review queue.">
          <div className={styles.form}>
            <Field label="From"><Input value={mail.sender} onChange={(e) => setMail({ ...mail, sender: e.target.value })} placeholder="buyer@customer.com" /></Field>
            <Field label="Subject"><Input value={mail.subject} onChange={(e) => setMail({ ...mail, subject: e.target.value })} /></Field>
            <Field label="Message"><Textarea value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} rows={7} /></Field>
            <Toolbar>
              <Button variant="primary" loading={triage.busy} disabled={!mail.body.trim()} onClick={() => runTriage(mail.sender, mail.subject, mail.body)}>
                Read email
              </Button>
            </Toolbar>
            {triage.busy && <Notice tone="info">{WORKING}</Notice>}
            {triage.error && <Notice tone="danger">{triage.error}</Notice>}
            {triage.data && <TriageView result={triage.data.result} />}
          </div>
        </Card>
      </div>
      <div>
        <Card title="Draft an email" description="Give the facts; the draft uses nothing else. You check it and send it yourself.">
          <div className={styles.form}>
            <Toolbar>
              <Field label="Kind"><Select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })} options={DRAFT_KINDS} /></Field>
              <Field label="Language"><Select value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value })} options={LANGS} /></Field>
            </Toolbar>
            <Field label="To"><Input value={draft.recipient} onChange={(e) => setDraft({ ...draft, recipient: e.target.value })} placeholder="Name or company" /></Field>
            <Field label="Facts to include" hint="Order numbers, dates, quantities. Missing facts become [TO CONFIRM].">
              <Textarea value={draft.facts} onChange={(e) => setDraft({ ...draft, facts: e.target.value })} rows={5} />
            </Field>
            <Toolbar>
              <Button variant="primary" loading={drafted.busy} disabled={!draft.facts.trim()}
                onClick={() => runDraft(draft.kind, draft.facts, draft.language, draft.recipient)}>
                Draft email
              </Button>
            </Toolbar>
            {drafted.busy && <Notice tone="info">{WORKING}</Notice>}
            {drafted.error && <Notice tone="danger">{drafted.error}</Notice>}
            {drafted.data && <DraftView result={drafted.data.result} />}
          </div>
        </Card>
      </div>
    </Columns>
  );
}

/* -------------------------------------------------------------- drawings */

const ROW_TONE = { match: 'success', different: 'danger', unreadable: 'warning' };
const ROW_LABEL = { match: 'Match', different: 'Different', unreadable: 'Could not read' };

function show(value) {
  if (Array.isArray(value)) return value.join(', ') || '–';
  if (value === '' || value == null) return '–';
  return String(value);
}

function DrawingView({ result }) {
  return (
    <div className={styles.form}>
      <Notice
        tone={result.differences ? 'warning' : 'success'}
        title={result.differences ? `${result.differences} difference(s) to check` : 'No differences found'}
      >
        An engineer signs off every drawing. Anything that could not be read counts as a difference.
      </Notice>
      <DataTable
        rows={result.rows}
        rowKey={(r) => r.field}
        columns={[
          { key: 'field', label: 'What' },
          { key: 'status', label: 'Result', render: (r) => <Badge tone={ROW_TONE[r.status]}>{ROW_LABEL[r.status] || r.status}</Badge> },
          { key: 'master', label: 'Master', render: (r) => show(r.master) },
          { key: 'supplier', label: 'Supplier', render: (r) => show(r.supplier) },
          { key: 'note', label: 'Note', render: (r) => r.note || '' },
        ]}
      />
      {result.supplier_notes_english?.length > 0 && (
        <div>
          <p className={styles.subhead}>Supplier&apos;s notes, in English</p>
          <ul className={styles.list}>
            {result.supplier_notes_english.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function DrawingTool() {
  const [master, setMaster] = useState(null);
  const [supplier, setSupplier] = useState(null);
  const [state, run] = useAction(aiCompareDrawings);

  return (
    <Card
      title="Check a supplier drawing against ours"
      description="Upload Allied's master drawing and the supplier's version (PDF). The AI reads both; the comparison itself is fixed rules, so the same drawings always give the same result."
    >
      <div className={styles.form}>
        <div className={styles.files}>
          <Field label="Our master drawing" hint={master?.name}>
            <input type="file" accept=".pdf,image/*" className={styles.file} onChange={(e) => setMaster(e.target.files?.[0] || null)} />
          </Field>
          <Field label="Supplier drawing" hint={supplier?.name}>
            <input type="file" accept=".pdf,image/*" className={styles.file} onChange={(e) => setSupplier(e.target.files?.[0] || null)} />
          </Field>
        </div>
        <Toolbar>
          <Button variant="primary" loading={state.busy} disabled={!master || !supplier} onClick={() => run(master, supplier)}>
            Compare drawings
          </Button>
        </Toolbar>
        {state.busy && <Notice tone="info">Reading both drawings… this can take a couple of minutes on the local model.</Notice>}
        {state.error && <Notice tone="danger">{state.error}</Notice>}
        {state.data && <DrawingView result={state.data.result} />}
      </div>
    </Card>
  );
}

/* ----------------------------------------------------------------- calls */

const REASON = {
  order_status: 'Order status', new_order: 'New order', quote_request: 'Quote request',
  delivery: 'Delivery', complaint: 'Complaint', supplier: 'Supplier', other: 'Other',
};

function CallView({ result }) {
  return (
    <div className={styles.form}>
      <Toolbar>
        <Badge tone={result.reason === 'complaint' ? 'danger' : 'info'}>{REASON[result.reason] || result.reason}</Badge>
        {result.callback_needed && <Badge tone="warning">Call back</Badge>}
        {result.urgency === 'high' && <Badge tone="danger">Urgent</Badge>}
      </Toolbar>
      <p className={styles.prose}>{result.summary}</p>
      <DetailList items={[
        ['Caller', [result.caller_name, result.company].filter(Boolean).join(', ') || '–'],
        ['Numbers mentioned', show(result.po_numbers)],
      ]} />
      {result.action_items?.length > 0 && (
        <ul className={styles.list}>{result.action_items.map((a, i) => <li key={i}>{a}</li>)}</ul>
      )}
    </div>
  );
}

function CallTool({ recordingsReady }) {
  const [transcript, setTranscript] = useState('');
  const [from, setFrom] = useState('');
  const [state, run] = useAction(aiSummarizeCall);
  const data = state.data;

  return (
    <Columns side="left">
      <div>
        <Card title="Summarise a call" description="Paste or dictate what was said. Calls about an order already on the monday board get a note on that job.">
          <div className={styles.form}>
            <Field label="Caller's number"><Input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="+1 555 0100" /></Field>
            <Field label="What was said"><Textarea value={transcript} onChange={(e) => setTranscript(e.target.value)} rows={8} /></Field>
            <Toolbar>
              <DictateButton language="en" onText={(t) => setTranscript((prev) => (prev ? `${prev} ${t}` : t))} />
              <Button variant="primary" loading={state.busy} disabled={!transcript.trim()} onClick={() => run(transcript, from)}>Summarise</Button>
            </Toolbar>
          </div>
        </Card>
        <Card title="Connecting the phone system">
          <p className={styles.small}>
            A phone system can post each received call&apos;s transcript to <code>/api/v1/ai/calls?token=…</code> (set
            PHONE_WEBHOOK_TOKEN). The AI never takes an order or quotes a price; it writes the message and flags callbacks.
            {recordingsReady ? ' Recordings can be transcribed on this server.' : ' Transcribing recordings on the server is off (needs faster-whisper).'}
          </p>
        </Card>
      </div>
      <div>
        <Card title="Summary">
          {state.busy && <Notice tone="info">{WORKING}</Notice>}
          {state.error && <Notice tone="danger">{state.error}</Notice>}
          {!state.busy && !state.error && !data && <EmptyState title="The call summary appears here." />}
          {data && (
            <div className={styles.form}>
              <CallView result={data.result} />
              {data.matched_orders?.length > 0 && (
                <Notice tone="info" title="Matching order">
                  {data.matched_orders.map((o) => `SO ${o.sales_order_number} (PO ${o.customer_po_number}, ${o.status})`).join('; ')}
                  {data.monday_updates?.length > 0 && ' · note added to the monday job'}
                </Notice>
              )}
            </div>
          )}
        </Card>
      </div>
    </Columns>
  );
}

/* ----------------------------------------------------------------- parts */

function PartTool() {
  const [raw, setRaw] = useState('');
  const [state, run] = useAction(aiSuggestItem);
  const data = state.data;

  return (
    <Card title="Which catalogue item did they mean?" description="For part names the normal matching is unsure about. Approve the suggestion in To review and it is remembered for next time.">
      <div className={styles.form}>
        <Toolbar>
          <Field label="What the customer wrote" className={styles.grow}>
            <Input value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="e.g. half inch 400 brinell sheet" />
          </Field>
          <Button variant="primary" loading={state.busy} disabled={raw.trim().length < 2} onClick={() => run(raw)}>Suggest</Button>
        </Toolbar>
        {state.busy && <Notice tone="info">{WORKING}</Notice>}
        {state.error && <Notice tone="danger">{state.error}</Notice>}
        {data && (
          <>
            <Notice tone={data.result.item_id ? 'success' : 'warning'} title={data.result.item_id ? `Suggested: ${data.result.item_id}` : 'No clear match'}>
              {data.result.reason} {data.result.item_id && '· waiting in To review'}
            </Notice>
            <DataTable
              rows={data.candidates}
              rowKey={(r) => r.item_id}
              columns={[
                { key: 'item_id', label: 'Item' },
                { key: 'description', label: 'Description' },
                { key: 'score', label: 'Text match', align: 'right' },
              ]}
            />
          </>
        )}
      </div>
    </Card>
  );
}
