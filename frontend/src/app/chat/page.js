'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  queryRAGChat,
  fetchRAGSources,
  fetchRAGTemplates,
  fetchCacheSummary,
  uploadChatPDF,
  executeSystemAction,
} from '@/lib/api';
import {
  Badge,
  Button,
  Card,
  Columns,
  DetailList,
  Field,
  Input,
  Notice,
  Select,
  Skeleton,
  Spinner,
  Textarea,
} from '@/components/ui';
import styles from './chat.module.css';

// Plain names for the kinds of record an answer can come from.
const SOURCE_TEXT = {
  HISTORICAL_ESTIMATE: 'Past quote',
  WELDING_WPS: 'Welding procedure',
  SUPPLIER_AGREEMENT: 'Supplier terms',
  ASTM_STANDARD: 'ASTM standard',
  MTR_CERT: 'Mill certificate',
  CACHED_ORDER: 'Recent order',
  ITEM_MASTER: 'Product catalog',
  CUSTOMER_MASTER: 'Customer record',
  VENDOR_MASTER: 'Supplier record',
  INTERNAL_DOC: 'Internal document',
  SCANNED_DOC: 'Scanned document',
};

const SAMPLE_DOCS = [
  { value: '', label: 'None, I will paste text' },
  { value: 'SAMPLE-PO-OC', label: 'Owens Corning Edmonton customer PO' },
  { value: 'SAMPLE-DWG-CAD', label: 'Drawing OC-ED-218-R2' },
  { value: 'SAMPLE-MTR-MILL', label: 'Mill certificate, heat HT-300XL-8819' },
];

const IMPORT_TYPES = [
  { value: 'ITEMS', label: 'Products' },
  { value: 'CUSTOMERS', label: 'Customers' },
  { value: 'VENDORS', label: 'Suppliers' },
  { value: 'ORDERS_BATCH', label: 'Batch of customer POs' },
];

// Tasks the assistant can run directly. `value` is the backend action type.
const TASKS = [
  {
    value: 'EXECUTE_END_TO_END_ORDER',
    label: 'Process a customer order',
    submit: 'Process order',
    fields: [
      { key: 'customer_name', label: 'Customer', placeholder: 'Owens Corning' },
      { key: 'customer_po_number', label: 'PO number', placeholder: 'PO-9921' },
      {
        key: 'raw_json',
        label: 'Full order as JSON (optional)',
        hint: 'If filled in, this is used instead of the fields above.',
        multiline: true,
        placeholder: '{"customer_name": "...", "line_items": [{"raw_item_text": "...", "quantity": 1, "unit_price": 100}]}',
      },
    ],
  },
  {
    value: 'EXECUTE_INGEST_RFQ_EMAIL',
    label: 'Read a quote request email',
    submit: 'Read email',
    fields: [
      { key: 'sender_email', label: 'From (email)', placeholder: 'purchasing@owenscorning.com' },
      { key: 'subject_line', label: 'Subject', placeholder: 'RFQ: Wear plates' },
      { key: 'raw_rfq_body', label: 'Email text', multiline: true, placeholder: 'Paste the email here' },
    ],
  },
  {
    value: 'EXECUTE_WEAR_SCAN',
    label: 'Check customer equipment for wear',
    submit: 'Run wear check',
    fields: [
      { key: 'discount_pct', label: 'Volume discount (%)', type: 'number', step: '0.1', placeholder: '5.0' },
      { key: 'delivery_weeks', label: 'Delivery time (weeks)', type: 'number', placeholder: '3' },
    ],
  },
  {
    value: 'EXECUTE_LEAD_SCRAPE',
    label: 'Find new sales leads',
    submit: 'Find leads',
    fields: [
      { key: 'job_titles', label: 'Job titles', hint: 'Separate with commas.', placeholder: 'Maintenance Supervisor' },
      { key: 'industries', label: 'Industries', hint: 'Separate with commas.', placeholder: 'Cement, Mining' },
      { key: 'location', label: 'Location', placeholder: 'Texas, USA' },
    ],
  },
  {
    value: 'EXECUTE_OEM_UPGRADE',
    label: 'Write an upgrade pitch for an OEM part',
    submit: 'Write pitch',
    fields: [
      { key: 'current_part', label: 'Current part', placeholder: 'Chute liner' },
      { key: 'oem_material', label: 'OEM material', placeholder: 'A36 carbon steel' },
    ],
  },
  {
    value: 'STAGE_SAGE_ORDER',
    label: 'Stage a sales order for Sage 50',
    submit: 'Stage order',
    fields: [
      { key: 'item_id', label: 'Part number', placeholder: 'PLATE-300XL-38' },
      { key: 'quantity', label: 'Quantity', type: 'number', placeholder: '1' },
    ],
  },
  {
    value: 'EXECUTE_MONDAY_PULL',
    label: 'Pull items from a monday.com board',
    submit: 'Pull items',
    fields: [
      { key: 'board_id', label: 'Board ID', placeholder: '441920' },
      { key: 'limit', label: 'How many items', type: 'number', placeholder: '10' },
    ],
  },
  {
    value: 'EXECUTE_VENDOR_REPRICE',
    label: 'Check a supplier price is current',
    note: 'Prices older than 60 days are flagged.',
    submit: 'Check price',
    fields: [
      { key: 'vendor', label: 'Supplier', placeholder: 'NIBROCK' },
      { key: 'item_id', label: 'Part number', placeholder: 'PLATE-300XL-38' },
      { key: 'po_number', label: 'PO number', placeholder: 'PO-CHECK-8841' },
    ],
  },
  {
    value: 'EXECUTE_CRM_FORM_REQUEST',
    label: 'Send a lead the spec form',
    submit: 'Send form',
    fields: [
      { key: 'pulse_id', label: 'monday.com lead ID', type: 'number', placeholder: '98765' },
      { key: 'lead_email', label: 'Lead email', type: 'email', placeholder: 'procurement@plant.com' },
    ],
  },
  {
    value: 'EXECUTE_OUTREACH_EMAIL',
    label: 'Draft a first email to a plant contact',
    submit: 'Draft email',
    fields: [
      { key: 'name', label: 'Contact name', placeholder: 'Dave Miller' },
      { key: 'title', label: 'Job title', placeholder: 'Plant Maintenance Manager' },
      { key: 'company', label: 'Company or site', placeholder: 'Lehigh Hanson Cement' },
      { key: 'industry', label: 'Industry', placeholder: 'Cement & aggregates' },
      { key: 'email', label: 'Email', type: 'email', placeholder: 'dmiller@lehigh.com' },
    ],
  },
  {
    value: 'EXECUTE_THREE_WAY_MATCH',
    label: 'Check a supplier bill against its PO',
    submit: 'Check bill',
    fields: [
      { key: 'invoice_number', label: 'Bill number', placeholder: 'INV-NIBROCK-8840' },
      { key: 'vendor_name', label: 'Supplier', placeholder: 'NIBROCK' },
      { key: 'po_number', label: 'PO number', placeholder: 'PO-NIBROCK-9921' },
      { key: 'total_amount_billed', label: 'Total billed ($)', type: 'number', step: '0.01', placeholder: '709.00' },
    ],
  },
  {
    value: 'EXECUTE_SAGE_CSV_EXPORT',
    label: 'Make Sage 50 import files',
    note: 'Creates SO_IMPORT.CSV and PO_IMPORT.CSV for Sage 50.',
    submit: 'Make files',
    fields: [],
  },
  {
    value: 'EXECUTE_PACKING_LIST_DISPATCH',
    label: 'Send a packing list and dispatch',
    submit: 'Send packing list',
    fields: [
      { key: 'sales_order_number', label: 'Sales order number', placeholder: 'SO-12930' },
      { key: 'carrier_name', label: 'Carrier', placeholder: 'TForce Freight' },
      { key: 'recipient_email', label: 'Customer receiving email', type: 'email', placeholder: 'receiving@plant.com' },
    ],
  },
  {
    value: 'SCAN_INTERNAL_DOCUMENT',
    label: 'Read a PO, drawing or mill certificate',
    submit: 'Read document',
    fields: [
      { key: 'sample_id', label: 'Example document', options: SAMPLE_DOCS },
      { key: 'document_text', label: 'Or paste the document text', multiline: true, placeholder: 'PO text, drawing title block or MTR chemistry' },
    ],
  },
  {
    value: 'EXECUTE_CSV_IMPORT',
    label: 'Import catalog data from CSV',
    submit: 'Import CSV',
    fields: [
      { key: 'import_type', label: 'What is in the file', options: IMPORT_TYPES, initial: 'ITEMS' },
      {
        key: 'csv_text',
        label: 'CSV rows',
        hint: 'Leave empty to open the import screen in Orders & Sage.',
        multiline: true,
        placeholder: 'Paste CSV rows here',
      },
    ],
  },
  {
    value: 'RESOLVE_EXCEPTION',
    label: 'Resolve a flagged problem',
    submit: 'Resolve',
    fields: [
      { key: 'exception_id', label: 'Problem ID', placeholder: 'EXC-NIB-01' },
      { key: 'resolution_action', label: 'Resolution', placeholder: 'DISPATCH_DISPUTE_NOTICE' },
    ],
  },
];

const TASK_BY_VALUE = Object.fromEntries(TASKS.map((t) => [t.value, t]));
const TASK_OPTIONS = [{ value: '', label: 'Choose a task…' }, ...TASKS.map((t) => ({ value: t.value, label: t.label }))];
const taskLabel = (type) => TASK_BY_VALUE[type]?.label || type;

// Runs only inside click handlers; kept outside the component so the purity
// rule does not mistake it for render-time work.
const stagedPoNumber = () => `PO-STAGE-${Date.now().toString().slice(-4)}`;

const stripSymbols = (text) => String(text || '').replace(/[\p{Extended_Pictographic}️]/gu, '').trim();

function engineNote(model) {
  if (!model) return '';
  if (/deterministic|business (reasoning )?engine/i.test(model)) return 'Answered from company records (AI model not used)';
  return `Answered by ${model}`;
}

// Text sent back to the server as conversation history.
function historyText(m) {
  let text = m.content;
  if (m.attachment) text += `\n(Attached: ${m.attachment})`;
  if (m.data !== undefined) text += `\n${JSON.stringify(m.data, null, 2)}`;
  return text;
}

export default function ChatPage() {
  return (
    <Suspense fallback={<Skeleton lines={6} />}>
      <PromptFromUrl />
    </Suspense>
  );
}

// Other pages link here with ?prompt=... to pre-fill the question.
function PromptFromUrl() {
  const prompt = useSearchParams().get('prompt') || '';
  return <Assistant key={prompt} initialPrompt={prompt} />;
}

function Assistant({ initialPrompt }) {
  const router = useRouter();
  const listRef = useRef(null);
  const fileRef = useRef(null);

  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState(initialPrompt);
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [devBypass, setDevBypass] = useState(true);
  const [agenticMode, setAgenticMode] = useState(false);
  const [flash, setFlash] = useState('');

  const [templates, setTemplates] = useState(null);
  const [info, setInfo] = useState(null);

  const [task, setTask] = useState('');
  const [taskPayload, setTaskPayload] = useState({});
  const [taskError, setTaskError] = useState('');

  useEffect(() => {
    let alive = true;
    async function load() {
      const [sources, tmpls, summary] = await Promise.allSettled([
        fetchRAGSources(),
        fetchRAGTemplates(),
        fetchCacheSummary(),
      ]);
      if (!alive) return;
      setTemplates(tmpls.status === 'fulfilled' && Array.isArray(tmpls.value) ? tmpls.value : []);
      setInfo({
        kb: sources.status === 'fulfilled' ? sources.value : null,
        cache: summary.status === 'fulfilled' ? summary.value : null,
        failed: sources.status === 'rejected' && summary.status === 'rejected',
      });
    }
    load();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  function showFlash(text) {
    setFlash(text);
    setTimeout(() => setFlash(''), 3000);
  }

  const push = (msg) => setMessages((prev) => [...prev, msg]);

  async function send(text) {
    const q = (text ?? input).trim();
    if (!q && !file) return;

    push({ role: 'user', content: q || 'Please read the attached PDF.', attachment: file?.name });
    setInput('');
    setLoading(true);

    try {
      let uploadedPdfPath = null;
      if (file) {
        const up = await uploadChatPDF(file);
        uploadedPdfPath = up.file_path;
        setFile(null);
      }

      const res = await queryRAGChat({
        query: q || 'Analyze attached document.',
        conversation_history: messages.filter((m) => m.role !== 'system').map((m) => ({ role: m.role, content: historyText(m) })),
        dev_bypass: devBypass,
        agentic_mode: agenticMode,
        uploaded_pdf_path: uploadedPdfPath,
      });

      push({
        role: 'assistant',
        content: res.answer,
        citations: res.citations || [],
        suggested_actions: res.suggested_actions || [],
        model_used: res.model_used,
      });

      if (agenticMode && res.suggested_actions?.length > 0) {
        await runAction(res.suggested_actions[0]);
      }
    } catch (err) {
      push({
        role: 'assistant',
        error: true,
        content: `Sorry, I couldn't get an answer. ${err.message || ''}`.trim(),
      });
    } finally {
      setLoading(false);
    }
  }

  async function runAction(action) {
    const type = action.action_type;

    if (type === 'STAGE_OPERATIONS') {
      router.push('/operations');
      return;
    }
    if (type === 'EXECUTE_CSV_IMPORT' && !action.prefill_data?.csv_text) {
      router.push('/operations');
      return;
    }

    if (type === 'STAGE_SAGE_ORDER') {
      const prefill = action.prefill_data || {};
      await runAction({
        action_type: 'EXECUTE_END_TO_END_ORDER',
        prefill_data: {
          customer_name: prefill.customer_name || 'Allied Customer Staged',
          customer_po_number: stagedPoNumber(),
          line_items: [
            {
              raw_item_text: prefill.item_id || 'PLATE-300XL-38',
              quantity: Number(prefill.quantity) || 1,
              unit_price: Number(prefill.price) || 285.0,
              notes: prefill.notes || 'Staged via Copilot',
            },
          ],
        },
      });
      return;
    }

    if (type === 'COPY_PROPOSAL') {
      const text = action.prefill_data?.proposal_text || 'Allied Welding Systems Bid Proposal';
      try {
        await navigator.clipboard.writeText(text);
        showFlash('Proposal copied. Paste it into your email or document.');
      } catch {
        showFlash('Could not copy the proposal.');
      }
      return;
    }

    if (type.startsWith('EXECUTE_') || type === 'RESOLVE_EXCEPTION' || type === 'SCAN_INTERNAL_DOCUMENT') {
      setLoading(true);
      try {
        const res = await executeSystemAction(type, action.prefill_data);
        const data = res.data !== undefined ? res.data : res;
        const finished = !res.status || res.status === 'success';
        push({
          role: 'assistant',
          kind: 'task',
          content: finished
            ? `Finished: ${taskLabel(type)}.`
            : `${taskLabel(type)}: ${res.detail || res.message || res.status}`,
          data,
        });
      } catch (err) {
        push({ role: 'assistant', error: true, content: `${taskLabel(type)} failed. ${err.message || ''}`.trim() });
      } finally {
        setLoading(false);
      }
    }
  }

  function chooseTask(value) {
    setTask(value);
    setTaskError('');
    const initial = {};
    (TASK_BY_VALUE[value]?.fields || []).forEach((f) => {
      if (f.initial) initial[f.key] = f.initial;
    });
    setTaskPayload(initial);
  }

  async function runTask(e) {
    e.preventDefault();
    if (!task) return;

    if (task === 'EXECUTE_CSV_IMPORT' && !taskPayload.csv_text) {
      router.push('/operations');
      return;
    }

    let payload = { ...taskPayload };
    if (task === 'EXECUTE_LEAD_SCRAPE') {
      ['job_titles', 'industries'].forEach((k) => {
        if (typeof payload[k] === 'string') payload[k] = payload[k].split(',').map((s) => s.trim()).filter(Boolean);
      });
    }
    if (task === 'EXECUTE_END_TO_END_ORDER' && payload.raw_json?.trim()) {
      try {
        payload = JSON.parse(payload.raw_json);
      } catch {
        setTaskError('The order JSON is not valid. Check the brackets and quotes, or clear it and use the fields.');
        return;
      }
    }

    push({ role: 'user', content: `Run task: ${taskLabel(task)}` });
    const type = task;
    chooseTask('');
    await runAction({ action_type: type, prefill_data: payload });
  }

  const current = TASK_BY_VALUE[task];
  const canSend = !loading && (input.trim() || file);

  return (
    <Columns>
      <div>
        <section className={styles.chat} aria-label="Conversation">
          <div ref={listRef} className={styles.thread}>
            {messages.length === 0 ? (
              <div className={styles.welcome}>
                <h2 className={styles.welcomeTitle}>What do you need to know?</h2>
                <p className={styles.welcomeText}>
                  Ask about steel grades, welding procedures, past prices, supplier terms or mill certificates. Answers
                  list the records they came from.
                </p>
                {templates === null ? (
                  <div className={styles.chipSkeleton}>
                    <Skeleton lines={2} />
                  </div>
                ) : (
                  templates.length > 0 && (
                    <div className={styles.chips}>
                      {templates.map((t) => (
                        <button
                          key={t.id}
                          type="button"
                          className={styles.chip}
                          onClick={() => send(t.prompt)}
                          title={t.prompt}
                          disabled={loading}
                        >
                          {t.title}
                        </button>
                      ))}
                    </div>
                  )
                )}
              </div>
            ) : (
              messages.map((m, i) => <Message key={i} msg={m} onAction={runAction} busy={loading} />)
            )}

            {loading && (
              <div className={styles.typing} role="status">
                <Spinner small />
                Working on it…
              </div>
            )}
          </div>

          <form
            className={styles.composer}
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            {flash && (
              <p className={styles.flash} role="status">
                {flash}
              </p>
            )}
            {file && (
              <div className={styles.attached}>
                <Badge tone="neutral">{file.name}</Badge>
                <button type="button" className={styles.removeFile} onClick={() => setFile(null)}>
                  Remove
                </button>
              </div>
            )}
            <div className={styles.inputRow}>
              <button
                type="button"
                className={styles.attach}
                onClick={() => fileRef.current?.click()}
                aria-label="Attach a PDF"
                title="Attach a PDF"
                disabled={loading}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M21 11.5 12.5 20a5 5 0 0 1-7-7l8.5-8.5a3.5 3.5 0 0 1 5 5L10.5 18a2 2 0 0 1-3-3L15 7.5" />
                </svg>
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                className={styles.hidden}
                onChange={(e) => {
                  setFile(e.target.files?.[0] || null);
                  e.target.value = '';
                }}
              />
              <textarea
                className={styles.textbox}
                rows={1}
                placeholder="Ask a question…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (canSend) send();
                  }
                }}
                disabled={loading}
                aria-label="Your question"
              />
              <Button type="submit" variant="primary" disabled={!canSend}>
                Send
              </Button>
            </div>
            <div className={styles.options}>
              <label className={styles.option}>
                <input type="checkbox" checked={!devBypass} onChange={(e) => setDevBypass(!e.target.checked)} />
                Use the AI model if it&apos;s running
              </label>
              <label className={styles.option}>
                <input type="checkbox" checked={agenticMode} onChange={(e) => setAgenticMode(e.target.checked)} />
                Run the suggested action automatically
              </label>
            </div>
          </form>
        </section>
      </div>

      <div>
        <Card title="Run a task" description="Do a job directly, without asking a question first.">
          <form className={styles.taskForm} onSubmit={runTask}>
            <Field label="Task">
              <Select value={task} onChange={(e) => chooseTask(e.target.value)} options={TASK_OPTIONS} />
            </Field>
            {current?.note && <p className={styles.quiet}>{current.note}</p>}
            {current?.fields.map((f) => (
              <Field key={f.key} label={f.label} hint={f.hint}>
                {f.options ? (
                  <Select
                    value={taskPayload[f.key] || ''}
                    onChange={(e) => setTaskPayload({ ...taskPayload, [f.key]: e.target.value })}
                    options={f.options}
                  />
                ) : f.multiline ? (
                  <Textarea
                    value={taskPayload[f.key] || ''}
                    placeholder={f.placeholder}
                    onChange={(e) => setTaskPayload({ ...taskPayload, [f.key]: e.target.value })}
                  />
                ) : (
                  <Input
                    type={f.type || 'text'}
                    step={f.step}
                    value={taskPayload[f.key] || ''}
                    placeholder={f.placeholder}
                    onChange={(e) => setTaskPayload({ ...taskPayload, [f.key]: e.target.value })}
                  />
                )}
              </Field>
            ))}
            {taskError && <Notice tone="danger">{taskError}</Notice>}
            {current && (
              <Button type="submit" variant="primary" disabled={loading}>
                {task === 'EXECUTE_CSV_IMPORT' && !taskPayload.csv_text ? 'Open the import screen' : current.submit}
              </Button>
            )}
          </form>
        </Card>

        <Card title="What it can search">
          {!info ? (
            <Skeleton lines={4} />
          ) : info.failed ? (
            <Notice tone="warning">Could not load what the assistant can search. Questions still work.</Notice>
          ) : (
            <DetailList
              items={[
                ['Reference documents', info.kb?.total_documents],
                ['Recent orders', info.cache?.cached_orders_count],
                ['Suppliers', info.cache?.registered_vendors_count],
                ['Mill certificate heats', info.cache?.indexed_heat_numbers_count],
              ]}
            />
          )}
        </Card>
      </div>
    </Columns>
  );
}

function Message({ msg, onAction, busy }) {
  if (msg.role === 'user') {
    return (
      <div className={styles.userRow}>
        <div className={styles.userBubble}>
          <p className={styles.text}>{msg.content}</p>
          {msg.attachment && <span className={styles.attachNote}>Attached: {msg.attachment}</span>}
        </div>
      </div>
    );
  }

  if (msg.error) {
    return (
      <div className={styles.assistantRow}>
        <Notice tone="danger">{msg.content}</Notice>
      </div>
    );
  }

  const citations = msg.citations || [];
  const actions = msg.suggested_actions || [];

  return (
    <div className={styles.assistantRow}>
      <div className={styles.answer}>
        <RichText text={msg.content} />

        {msg.data !== undefined && (
          <details className={styles.details}>
            <summary>Show the result</summary>
            <pre className={styles.code}>{JSON.stringify(msg.data, null, 2)}</pre>
          </details>
        )}

        {actions.length > 0 && (
          <div className={styles.actions}>
            {actions.map((a, i) => (
              <Button key={i} size="sm" onClick={() => onAction(a)} disabled={busy}>
                {stripSymbols(a.button_label)}
              </Button>
            ))}
          </div>
        )}

        {citations.length > 0 && (
          <details className={styles.sources}>
            <summary>
              {citations.length} source{citations.length === 1 ? '' : 's'}
            </summary>
            <ol className={styles.sourceList}>
              {citations.map((c, i) => (
                <li key={`${c.source_id}-${i}`}>
                  <details className={styles.source}>
                    <summary>
                      <span className={styles.sourceTitle}>{c.title}</span>
                      <span className={styles.sourceMeta}>
                        {SOURCE_TEXT[c.source_type] || c.source_type} · {Math.round((c.relevance_score || 0) * 100)}% match
                      </span>
                    </summary>
                    <div className={styles.sourceBody}>
                      <p>{c.excerpt}</p>
                      <p className={styles.quiet}>Record {c.source_id}</p>
                      {c.metadata && Object.keys(c.metadata).length > 0 && (
                        <details className={styles.details}>
                          <summary>Full record</summary>
                          <pre className={styles.code}>{JSON.stringify(c.metadata, null, 2)}</pre>
                        </details>
                      )}
                    </div>
                  </details>
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
      {msg.model_used && <span className={styles.engine}>{engineNote(msg.model_used)}</span>}
    </div>
  );
}

// Answers come back as light markdown: **bold** and ``` code blocks. Render
// just those, as React nodes, so nothing from the server is injected as HTML.
function RichText({ text }) {
  const parts = String(text || '').split(/```[a-z]*\n?([\s\S]*?)```/i);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <pre key={i} className={styles.code}>
        {part.trim()}
      </pre>
    ) : part.trim() ? (
      <p key={i} className={styles.text}>
        {part
          .trim()
          .split(/(\*\*[^*]+\*\*)/g)
          .map((chunk, j) =>
            chunk.startsWith('**') && chunk.endsWith('**') ? <strong key={j}>{chunk.slice(2, -2)}</strong> : chunk
          )}
      </p>
    ) : null
  );
}
