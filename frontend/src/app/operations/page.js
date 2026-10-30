'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Badge,
  Button,
  Card,
  Columns,
  DataTable,
  DetailList,
  EmptyState,
  Field,
  Input,
  Notice,
  Page,
  Select,
  Skeleton,
  Stat,
  StatGrid,
  Tabs,
  Textarea,
  money,
} from '@/components/ui';
import {
  fetchInboxFeed,
  simulateInboundEmail,
  fetchReconciliationHistory,
  matchVendorInvoice,
  fetchSageExportSummary,
  generateSageBatchBundle,
  fetchExceptions,
  resolveException,
  simulateMondayStatusTrigger,
  fetchCSVTemplates,
  importCSVData,
  uploadCSVFile,
  downloadSageCsv,
} from '@/lib/api';
import styles from './operations.module.css';

const REFRESH_MS = 30000;
const TEST_PULSE_ID = 441920;

const SUPPLIERS = [
  { value: 'NIBROCK', label: 'Nibrock Mining & Wear' },
  { value: 'ZIBO', label: 'Zibo Industrial Ceramics' },
  { value: 'KWS', label: 'KWS Specialty Fabrication' },
];

const CSV_TYPES = [
  { value: 'ITEMS', label: 'Products' },
  { value: 'CUSTOMERS', label: 'Customer accounts' },
  { value: 'VENDORS', label: 'Suppliers' },
  { value: 'ORDERS_BATCH', label: 'A batch of customer orders' },
];

const MONDAY_TESTS = [
  {
    status: 'Approved for Production',
    title: 'Job approved for production',
    text: 'Creates the purchase orders for the suppliers on the job (Nibrock, Zibo, KWS).',
  },
  {
    status: 'Dispatched / Shipped',
    title: 'Job dispatched or shipped',
    text: 'Creates the Allied delivery slip and prepares tracking details for the customer.',
  },
];

function poText(pos) {
  if (Array.isArray(pos)) return pos.join(', ');
  if (pos && typeof pos === 'object') {
    return Object.entries(pos)
      .map(([vendor, po]) => `${vendor}: ${po}`)
      .join(', ');
  }
  return String(pos || '');
}

export default function OperationsPage() {
  const [tab, setTab] = useState('inbox');
  const [busy, setBusy] = useState(null);
  const [flash, setFlash] = useState(null);

  // Server data. null means "not loaded yet".
  const [inbox, setInbox] = useState(null);
  const [bills, setBills] = useState(null);
  const [sage, setSage] = useState(null);
  const [exceptions, setExceptions] = useState(null);
  const [errors, setErrors] = useState({});
  const [sageFiles, setSageFiles] = useState(null);

  // Spreadsheet import
  const [csvType, setCsvType] = useState('ITEMS');
  const [csvText, setCsvText] = useState('');
  const [csvTemplates, setCsvTemplates] = useState({});
  const [csvResult, setCsvResult] = useState(null);

  const [simEmail, setSimEmail] = useState({
    sender_email: 'purchasing@owenscorning.com',
    sender_name: 'Mark Richardson (Owens Corning)',
    subject: 'PO-9925: Clad Wear Plates for Chute #4',
    body_text:
      'Please stage 4 units of PLATE-300XL-38 for immediate delivery to Edmonton Plant. PO Ref: OC-9925.',
  });

  const [simInvoice, setSimInvoice] = useState({
    invoice_number: 'INV-NIBROCK-9901',
    vendor_name: 'NIBROCK',
    po_number: 'PO-NIBROCK-9921',
    total_amount_billed: '709.00',
    item_id: 'PLATE-300XL-38',
    quantity_billed: '4',
    unit_cost_billed: '128.50',
    freight_billed: '195.00',
  });

  const [preview, setPreview] = useState(null);

  // Bumped after an action so the lists refresh straight away.
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    async function tick() {
      const [i, r, s, e] = await Promise.allSettled([
        fetchInboxFeed(),
        fetchReconciliationHistory(),
        fetchSageExportSummary(),
        fetchExceptions(),
      ]);
      if (!alive) return;
      if (i.status === 'fulfilled') setInbox(i.value || []);
      if (r.status === 'fulfilled') setBills(r.value || []);
      if (s.status === 'fulfilled') setSage(s.value || {});
      if (e.status === 'fulfilled') setExceptions(e.value || []);
      setErrors({
        inbox: i.status === 'rejected' ? i.reason?.message : '',
        bills: r.status === 'rejected' ? r.reason?.message : '',
        sage: s.status === 'rejected' ? s.reason?.message : '',
        exceptions: e.status === 'rejected' ? e.reason?.message : '',
      });
    }
    tick();
    const t = setInterval(tick, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [reloadKey]);

  // Success messages fade on their own; errors stay until dismissed.
  useEffect(() => {
    if (!flash || flash.tone !== 'success') return undefined;
    const t = setTimeout(() => setFlash(null), 5000);
    return () => clearTimeout(t);
  }, [flash]);

  useEffect(() => {
    if (!preview) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setPreview(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [preview]);

  const ok = (text) => setFlash({ tone: 'success', text });
  const fail = (err) => setFlash({ tone: 'danger', text: err?.message || String(err) });

  async function run(key, fn) {
    setBusy(key);
    try {
      await fn();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }

  function handleSimulateEmail(e) {
    e.preventDefault();
    run('email', async () => {
      const res = await simulateInboundEmail(simEmail);
      const pos = poText(res.purchase_orders);
      ok(`Order created: sales order ${res.sales_order_number}${pos ? `, supplier POs ${pos}` : ''}.`);
      reload();
    });
  }

  function handleCheckBill(e) {
    e.preventDefault();
    run('bill', async () => {
      const qty = Number(simInvoice.quantity_billed) || 1;
      const unit = Number(simInvoice.unit_cost_billed) || 100;
      const res = await matchVendorInvoice({
        invoice_number: simInvoice.invoice_number,
        vendor_name: simInvoice.vendor_name,
        po_number: simInvoice.po_number,
        lines: [{ item_id: simInvoice.item_id, quantity_billed: qty, unit_cost_billed: unit, total_amount: qty * unit }],
        freight_billed: Number(simInvoice.freight_billed) || 0,
        total_amount_billed: Number(simInvoice.total_amount_billed) || 100,
      });
      ok(
        res.reconciliation_status === 'RECONCILED_APPROVED'
          ? 'Bill matches the purchase order. Cleared to pay.'
          : `Bill checked: ${res.reconciliation_status}. See the list for the difference.`
      );
      reload();
    });
  }

  function handleSageFiles() {
    run('sage', async () => {
      const res = await generateSageBatchBundle();
      setSageFiles(res.files || {});
      ok('Sage import files created. Preview below.');
    });
  }

  function handleResolve(excId, action, label) {
    run(`exc-${excId}`, async () => {
      await resolveException(excId, {
        action_type: action,
        resolution_notes: `Resolved by operator via Operations Hub (${action})`,
      });
      ok(`Done: ${label}.`);
      reload();
    });
  }

  function handleMondayTest(status) {
    run(`monday-${status}`, async () => {
      await simulateMondayStatusTrigger(TEST_PULSE_ID, status);
      ok(`Test sent: “${status}” for board item #${TEST_PULSE_ID}.`);
      reload();
    });
  }

  function handleDownload(key, kind) {
    run(key, async () => {
      await downloadSageCsv(kind);
      ok(kind === 'sales-orders' ? 'SO_IMPORT.CSV downloaded.' : 'PO_IMPORT.CSV downloaded.');
    });
  }

  /** Loads the sample rows for a type into the box (replacing what is there). */
  async function loadTemplate(type) {
    setCsvType(type);
    if (csvTemplates[type]) {
      setCsvText(csvTemplates[type]);
      return;
    }
    try {
      const res = await fetchCSVTemplates();
      if (res?.templates) {
        setCsvTemplates(res.templates);
        setCsvText(res.templates[type] || '');
      }
    } catch (err) {
      setFlash({ tone: 'danger', text: `Couldn’t load the sample: ${err.message}` });
    }
  }

  /**
   * Switching type keeps pasted rows. The sample is only swapped in when the
   * box is empty or still holds the previous type's untouched sample.
   */
  function changeCsvType(type) {
    const untouched = !csvText.trim() || csvText === csvTemplates[csvType];
    if (untouched) {
      loadTemplate(type);
    } else {
      setCsvType(type);
    }
  }

  function handleImport(e) {
    e.preventDefault();
    if (!csvText.trim()) {
      setFlash({ tone: 'warning', text: 'Paste some rows or load the sample first.' });
      return;
    }
    run('csv', async () => {
      const res = await importCSVData(csvType, csvText);
      setCsvResult(res);
      reload();
    });
  }

  function handleUpload(e) {
    const input = e.target;
    const file = input.files?.[0];
    if (!file) return;
    run('csv', async () => {
      try {
        const res = await uploadCSVFile(file, csvType);
        setCsvResult(res);
        reload();
      } finally {
        input.value = '';
      }
    });
  }

  function changeTab(key) {
    setTab(key);
    if (key === 'csv' && Object.keys(csvTemplates).length === 0) loadTemplate('ITEMS');
  }

  const openExceptions = exceptions ? exceptions.filter((x) => x.status === 'OPEN') : [];
  const resolvedExceptions = exceptions ? exceptions.filter((x) => x.status !== 'OPEN') : [];

  const tabs = [
    { key: 'inbox', label: 'Order emails', count: inbox?.length },
    { key: 'attention', label: 'Needs attention', count: exceptions ? openExceptions.length : undefined },
    { key: 'bills', label: 'Supplier bills', count: bills?.length },
    { key: 'sage', label: 'Sage files' },
    { key: 'csv', label: 'Update lists' },
    { key: 'monday', label: 'monday.com tests' },
  ];

  /* ---------- table columns ---------- */

  const inboxColumns = [
    {
      key: 'customer',
      label: 'Customer',
      render: (m) => (
        <div className={styles.stack}>
          <strong className={styles.wide}>{m.customer_name}</strong>
          <span className={styles.muted}>PO {m.customer_po_number || '–'}</span>
        </div>
      ),
    },
    { key: 'so', label: 'Sales order', render: (m) => <span className={styles.nowrap}>{m.sales_order_number}</span> },
    { key: 'pos', label: 'Supplier POs', render: (m) => poText(m.purchase_orders) || <span className={styles.muted}>From stock</span> },
    { key: 'lines', label: 'Lines', align: 'right', render: (m) => m.line_items_count },
    {
      key: 'status',
      label: 'Status',
      render: (m) =>
        m.status === 'PROCESSED_ORDER_CREATED' ? <Badge tone="success">Order created</Badge> : <Badge>{m.status}</Badge>,
    },
    {
      key: 'reply',
      label: '',
      align: 'right',
      render: (m) =>
        m.customer_acknowledgment_email ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              setPreview({ title: `Confirmation to ${m.customer_name}`, text: m.customer_acknowledgment_email })
            }
          >
            View reply
          </Button>
        ) : null,
    },
  ];

  const billColumns = [
    {
      key: 'invoice',
      label: 'Bill',
      render: (b) => (
        <div className={styles.stack}>
          <strong className={styles.nowrap}>{b.invoice_number}</strong>
          <span className={styles.muted}>{b.vendor_name}</span>
        </div>
      ),
    },
    { key: 'po', label: 'Our PO', render: (b) => <span className={styles.nowrap}>{b.po_number}</span> },
    { key: 'ordered', label: 'Ordered', align: 'right', render: (b) => <span className={styles.num}>{money(b.authorized_po_total)}</span> },
    { key: 'billed', label: 'Billed', align: 'right', render: (b) => <span className={styles.num}>{money(b.actual_billed_total)}</span> },
    {
      key: 'diff',
      label: 'Difference',
      align: 'right',
      render: (b) => {
        const d = Number(b.cost_variance_amount);
        return d > 0 ? <span className={styles.numBad}>+{money(d)}</span> : <span className={styles.num}>{money(0)}</span>;
      },
    },
    {
      key: 'result',
      label: 'Result',
      render: (b) =>
        b.reconciliation_status === 'RECONCILED_APPROVED' ? (
          <Badge tone="success">Cleared to pay</Badge>
        ) : (
          <Badge tone="danger">Price mismatch</Badge>
        ),
    },
    {
      key: 'dispute',
      label: '',
      align: 'right',
      render: (b) =>
        b.dispute_email_draft ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              setPreview({ title: `Dispute email to ${b.vendor_name} (${b.invoice_number})`, text: b.dispute_email_draft })
            }
          >
            View dispute email
          </Button>
        ) : null,
    },
  ];

  /* ---------- render ---------- */

  return (
    <Page>
      {flash && (
        <Notice
          tone={flash.tone}
          action={
            <Button size="sm" variant="ghost" onClick={() => setFlash(null)}>
              Dismiss
            </Button>
          }
        >
          {flash.text}
        </Notice>
      )}

      <StatGrid>
        <Stat label="Order emails" value={inbox ? inbox.length : '–'} />
        <Stat
          label="Needs attention"
          value={exceptions ? openExceptions.length : '–'}
          tone={!exceptions ? undefined : openExceptions.length ? 'warning' : 'success'}
        />
        <Stat label="Supplier bills checked" value={bills ? bills.length : '–'} />
        <Stat label="Sales waiting for Sage" value={sage ? money(sage.total_sales_volume || 0) : '–'} />
      </StatGrid>

      <Tabs tabs={tabs} value={tab} onChange={changeTab} />

      {tab === 'inbox' && (
        <Columns>
          <div>
            <Card
              title="Order emails received"
              description="Customer POs that arrive by email become a sales order and supplier POs automatically."
              flush
            >
              <Loadable data={inbox} error={errors.inbox} flush>
                <DataTable
                  columns={inboxColumns}
                  rows={inbox}
                  rowKey={(m, i) => m.email_id || i}
                  empty="No order emails yet."
                />
              </Loadable>
            </Card>
          </div>
          <div>
            <Card title="Try a sample order email" description="Runs the same steps as a real email from a customer.">
              <form className={styles.form} onSubmit={handleSimulateEmail}>
                <Field label="From (email)">
                  <Input
                    type="email"
                    value={simEmail.sender_email}
                    onChange={(e) => setSimEmail({ ...simEmail, sender_email: e.target.value })}
                    required
                  />
                </Field>
                <Field label="From (name)">
                  <Input
                    value={simEmail.sender_name}
                    onChange={(e) => setSimEmail({ ...simEmail, sender_name: e.target.value })}
                    required
                  />
                </Field>
                <Field label="Subject">
                  <Input
                    value={simEmail.subject}
                    onChange={(e) => setSimEmail({ ...simEmail, subject: e.target.value })}
                    required
                  />
                </Field>
                <Field label="Message">
                  <Textarea
                    rows={4}
                    value={simEmail.body_text}
                    onChange={(e) => setSimEmail({ ...simEmail, body_text: e.target.value })}
                    required
                  />
                </Field>
                <Button type="submit" variant="primary" loading={busy === 'email'} disabled={busy !== null}>
                  Process sample email
                </Button>
              </form>
            </Card>
          </div>
        </Columns>
      )}

      {tab === 'attention' && (
        <Card
          title="Orders needing attention"
          description="Price increases, low margins or part numbers we don’t recognise. Pick what to do with each one."
        >
          <Loadable data={exceptions} error={errors.exceptions}>
            {openExceptions.length === 0 ? (
              <EmptyState title="Nothing needs attention." description="New issues show up here as orders come in." />
            ) : (
              <ul className={styles.issues}>
                {openExceptions.map((x) => (
                  <li key={x.exception_id} className={styles.issue}>
                    <div className={styles.issueHead}>
                      <span className={styles.issueTitle}>{x.title}</span>
                      <Badge tone={x.severity === 'CRITICAL' ? 'danger' : 'warning'}>
                        {x.severity === 'CRITICAL' ? 'Urgent' : 'Review'}
                      </Badge>
                    </div>
                    <p className={styles.issueText}>{x.description}</p>
                    <p className={styles.issueMeta}>
                      {x.vendor_or_customer} · Ref {x.reference_id}
                    </p>
                    {x.available_actions?.length > 0 && (
                      <div className={styles.issueActions}>
                        {x.available_actions.map((a, idx) => (
                          <Button
                            key={`${a.action}-${idx}`}
                            size="sm"
                            variant={idx === 0 ? 'primary' : 'secondary'}
                            loading={busy === `exc-${x.exception_id}`}
                            disabled={busy !== null}
                            onClick={() => handleResolve(x.exception_id, a.action, a.label)}
                          >
                            {a.label}
                          </Button>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {resolvedExceptions.length > 0 && (
              <details className={styles.resolved}>
                <summary>Resolved ({resolvedExceptions.length})</summary>
                <ul className={styles.issues}>
                  {resolvedExceptions.map((x) => (
                    <li key={x.exception_id} className={styles.issueDone}>
                      <div className={styles.issueHead}>
                        <span className={styles.issueTitle}>{x.title}</span>
                        <Badge tone="success">Resolved</Badge>
                      </div>
                      <p className={styles.issueMeta}>
                        {x.vendor_or_customer} · Ref {x.reference_id}
                        {x.resolution_notes ? ` · ${x.resolution_notes}` : ''}
                      </p>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Loadable>
        </Card>
      )}

      {tab === 'bills' && (
        <Columns>
          <div>
            <Card
              title="Supplier bills checked"
              description="Each bill is compared with the PO we sent. Price rises and surprise freight are flagged."
              flush
            >
              <Loadable data={bills} error={errors.bills} flush>
                <DataTable
                  columns={billColumns}
                  rows={bills}
                  rowKey={(b, i) => b.reconciliation_id || i}
                  empty="No bills checked yet."
                />
              </Loadable>
            </Card>
          </div>
          <div>
            <Card title="Check a supplier bill" description="Enter what the supplier billed. We compare it with our PO.">
              <form className={styles.form} onSubmit={handleCheckBill}>
                <Field label="Supplier">
                  <Select
                    options={SUPPLIERS}
                    value={simInvoice.vendor_name}
                    onChange={(e) => setSimInvoice({ ...simInvoice, vendor_name: e.target.value })}
                  />
                </Field>
                <div className={styles.pair}>
                  <Field label="Bill number">
                    <Input
                      value={simInvoice.invoice_number}
                      onChange={(e) => setSimInvoice({ ...simInvoice, invoice_number: e.target.value })}
                      required
                    />
                  </Field>
                  <Field label="Our PO number">
                    <Input
                      value={simInvoice.po_number}
                      onChange={(e) => setSimInvoice({ ...simInvoice, po_number: e.target.value })}
                      required
                    />
                  </Field>
                </div>
                <div className={styles.pair}>
                  <Field label="Part number">
                    <Input
                      value={simInvoice.item_id}
                      onChange={(e) => setSimInvoice({ ...simInvoice, item_id: e.target.value })}
                      required
                    />
                  </Field>
                  <Field label="Quantity">
                    <Input
                      type="number"
                      min="1"
                      step="1"
                      value={simInvoice.quantity_billed}
                      onChange={(e) => setSimInvoice({ ...simInvoice, quantity_billed: e.target.value })}
                      required
                    />
                  </Field>
                </div>
                <div className={styles.pair}>
                  <Field label="Unit price ($)">
                    <Input
                      type="number"
                      step="0.01"
                      value={simInvoice.unit_cost_billed}
                      onChange={(e) => setSimInvoice({ ...simInvoice, unit_cost_billed: e.target.value })}
                      required
                    />
                  </Field>
                  <Field label="Freight ($)">
                    <Input
                      type="number"
                      step="0.01"
                      value={simInvoice.freight_billed}
                      onChange={(e) => setSimInvoice({ ...simInvoice, freight_billed: e.target.value })}
                      required
                    />
                  </Field>
                </div>
                <Field label="Bill total ($)">
                  <Input
                    type="number"
                    step="0.01"
                    value={simInvoice.total_amount_billed}
                    onChange={(e) => setSimInvoice({ ...simInvoice, total_amount_billed: e.target.value })}
                    required
                  />
                </Field>
                <Button type="submit" variant="primary" loading={busy === 'bill'} disabled={busy !== null}>
                  Check bill
                </Button>
              </form>
            </Card>
          </div>
        </Columns>
      )}

      {tab === 'sage' && (
        <>
          <Card
            title="Sage 50 import files"
            description="Ready-to-import files for Sage 50. Sales post to 40000-00-FAB and steel materials to 50000-00-STEEL."
            actions={
              <Button variant="primary" loading={busy === 'sage'} disabled={busy !== null} onClick={handleSageFiles}>
                Create files now
              </Button>
            }
          >
            <Loadable data={sage} error={errors.sage}>
              <div className={styles.sageGrid}>
                <div className={styles.sageBox}>
                  <DetailList
                    items={[
                      ['Sales orders ready', sage?.sales_orders_count ?? 0],
                      ['Sales total', money(sage?.total_sales_volume || 0)],
                    ]}
                  />
                  <Button
                    loading={busy === 'dl-so'}
                    disabled={busy !== null}
                    onClick={() => handleDownload('dl-so', 'sales-orders')}
                  >
                    Download sales orders (SO_IMPORT.CSV)
                  </Button>
                </div>
                <div className={styles.sageBox}>
                  <DetailList
                    items={[
                      ['Supplier PO lines ready', sage?.purchase_orders_count ?? 0],
                      ['Amount payable', money(sage?.total_purchase_volume || 0)],
                    ]}
                  />
                  <Button
                    loading={busy === 'dl-po'}
                    disabled={busy !== null}
                    onClick={() => handleDownload('dl-po', 'purchase-orders')}
                  >
                    Download supplier POs (PO_IMPORT.CSV)
                  </Button>
                </div>
              </div>
            </Loadable>
          </Card>

          {sageFiles && (
            <Card title="Preview of the files just created" description="Exactly what Sage will import.">
              <div className={styles.previews}>
                <div>
                  <h3 className={styles.previewLabel}>SO_IMPORT.CSV (sales orders)</h3>
                  <pre className={styles.code}>{sageFiles['SO_IMPORT.CSV'] || 'Empty'}</pre>
                </div>
                <div>
                  <h3 className={styles.previewLabel}>PO_IMPORT.CSV (supplier POs)</h3>
                  <pre className={styles.code}>{sageFiles['PO_IMPORT.CSV'] || 'Empty'}</pre>
                </div>
              </div>
            </Card>
          )}
        </>
      )}

      {tab === 'csv' && (
        <Card
          title="Update lists from a spreadsheet"
          description="Add or change products, customers or suppliers, or bring in a batch of orders, from a CSV file."
        >
          <form className={styles.form} onSubmit={handleImport}>
            <div className={styles.csvBar}>
              <Field label="What are you updating?" className={styles.csvType}>
                <Select options={CSV_TYPES} value={csvType} onChange={(e) => changeCsvType(e.target.value)} />
              </Field>
              <div className={styles.csvButtons}>
                <Button onClick={() => loadTemplate(csvType)}>Load sample rows</Button>
                <label className={styles.uploadButton}>
                  Upload CSV file
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    className={styles.hiddenInput}
                    onChange={handleUpload}
                    disabled={busy !== null}
                  />
                </label>
              </div>
            </div>
            <Field label="Rows (first line is the column headings)" className={styles.csvField}>
              <Textarea
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                placeholder="Paste rows from your spreadsheet here…"
                spellCheck={false}
              />
            </Field>
            <div className={styles.submitRow}>
              <Button type="submit" variant="primary" loading={busy === 'csv'} disabled={busy !== null}>
                Import {CSV_TYPES.find((t) => t.value === csvType)?.label.toLowerCase()}
              </Button>
            </div>
          </form>

          {csvResult && (
            <div className={styles.result}>
              <Notice
                tone={csvResult.status === 'SUCCESS' ? 'success' : 'warning'}
                title={csvResult.status === 'SUCCESS' ? 'Import finished' : 'Import finished with warnings'}
              >
                {csvResult.message} {csvResult.rows_processed ?? 0} rows read, {csvResult.records_created ?? 0} added,{' '}
                {csvResult.records_updated ?? 0} updated.
              </Notice>
              {csvResult.errors?.length > 0 && (
                <ul className={styles.errorList}>
                  {csvResult.errors.map((err, idx) => (
                    <li key={idx}>{err}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </Card>
      )}

      {tab === 'monday' && (
        <>
          <Notice tone="info" action={<Link href="/integrations" className={styles.textLink}>Open monday.com link</Link>}>
            These send a test status change for board item #{TEST_PULSE_ID}. Real board events are listed on the monday.com
            link page.
          </Notice>
          <div className={styles.testGrid}>
            {MONDAY_TESTS.map((t) => (
              <Card key={t.status} title={t.title} description={`Board status: “${t.status}”`}>
                <div className={styles.testBody}>
                  <p className={styles.testText}>{t.text}</p>
                  <Button
                    loading={busy === `monday-${t.status}`}
                    disabled={busy !== null}
                    onClick={() => handleMondayTest(t.status)}
                  >
                    Send test
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}

      {preview && (
        <div className={styles.overlay} onClick={() => setPreview(null)}>
          <div
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="preview-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.dialogHead}>
              <h2 id="preview-title" className={styles.dialogTitle}>
                {preview.title}
              </h2>
              <Button size="sm" onClick={() => setPreview(null)} autoFocus>
                Close
              </Button>
            </div>
            <pre className={styles.dialogText}>{preview.text}</pre>
          </div>
        </div>
      )}
    </Page>
  );
}

/** Skeleton while loading, a notice if the fetch failed with nothing to show. */
function Loadable({ data, error, flush = false, children }) {
  if (data != null) return children;
  const body =
    error ? (
      <Notice tone="danger" title="Couldn’t load this list.">
        {error} It will retry shortly.
      </Notice>
    ) : (
      <Skeleton lines={4} />
    );
  return flush ? <div className={styles.pad}>{body}</div> : body;
}
