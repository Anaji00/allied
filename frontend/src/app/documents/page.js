'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { uploadDocumentScan, fetchDocumentSamples } from '@/lib/api';
import {
  Badge,
  Button,
  Card,
  Columns,
  DataTable,
  DetailList,
  EmptyState,
  Field,
  Notice,
  Page,
  Select,
  Skeleton,
  Spinner,
  money,
} from '@/components/ui';
import styles from './documents.module.css';

// Used only when the server's list of examples cannot be loaded. The text is
// still sent to the real reader, so the result is genuine.
const FALLBACK_SAMPLES = [
  {
    sample_id: 'SAMPLE-PO-OC',
    title: 'Customer PO — Owens Corning Edmonton',
    document_type: 'CUSTOMER_PO',
    sample_filename: 'PO_OwensCorning_Edmonton_9912.pdf',
    sample_text: `PURCHASE ORDER\nBUYER: OWENS CORNING (EDMONTON PLANT)\nPO NUMBER: PO-OC-EDM-9912\nTERMS: NET 150 DAYS | SHIP VIA: MOTOR FRT (BLIND SHIPMENT ONLY)\nDESTINATION: 831 HAYTER RD NW, EDMONTON, AB T6S 1A1\n\nLINE ITEMS:\n1. 2 EA | OC755507 | 2-1/2" DIA HOLES HAMMERMILL SCREEN ALLIED 300XL | $3,772.91 EA | DWG# OC-ED-218-R2\n2. 4 EA | BAR-34X2-41 | 3/4" X 2" X 41-3/4" C1045 MIDDLE BRACE BARS | $225.00 EA\nHEAT NUMBER REQUIRED ON MTR: HT-300XL-8819`,
  },
  {
    sample_id: 'SAMPLE-DWG-CAD',
    title: 'Drawing — OC-ED-218-R2',
    document_type: 'CAD_DRAWING',
    sample_filename: 'CAD_Blueprint_OC_ED_218_R2.pdf',
    sample_text: `ALLIED WELDING SYSTEMS - ENGINEERING DRAWING TAKEOFF\nDWG #: OC-ED-218-R2 | REV: 2 | SAP CODE: 755507\nTITLE: 2-1/2" DIA HOLES HAMMER MILL SCREEN (ALLIED 300XL)\nCUSTOMER: OWENS CORNING EDMONTON\nMATERIAL: 3/8" ALLIED 300XL CHROMIUM CARBIDE OVERLAY ON A36 BASE\nDIMENSIONS: 41" ARC LENGTH X 24" WIDTH X 3/8" THICKNESS\nTOTAL WEIGHT: 145.0 LBS\nNOTES: PLASMA CUT FROM BASE STEEL SIDE TOWARD CLAD OVERLAY.`,
  },
  {
    sample_id: 'SAMPLE-MTR-MILL',
    title: 'Mill certificate — Heat HT-300XL-8819',
    document_type: 'MTR_HEAT_CERT',
    sample_filename: 'MTR_Certified_HT_300XL_8819.pdf',
    sample_text: `CERTIFIED MATERIAL TEST REPORT (MTR) & QUALITY CERTIFICATE\nSUPPLIER: ALLIED CERTIFIED MILL PARTNER\nHEAT NUMBER: HT-300XL-8819\nMATERIAL SPEC: ALLIED 300XL CHROMIUM CARBIDE WEAR OVERLAY (ASTM A532)\nCHEMICAL COMPOSITION (%):\n  Carbon (C): 4.80%\n  Chromium (Cr): 29.50%\n  Manganese (Mn): 1.40%\n  Silicon (Si): 0.85%\n  Iron (Fe): BALANCE\nMECHANICAL PROPERTIES:\n  HARDNESS: 60.5 HRC (650 HBW)\n  DENSITY: 0.288 LBS/CU.IN\nSTATUS: FULLY COMPLIANT WITH ASTM WEAR ENVELOPE.`,
  },
];

const DOC_TYPES = [
  { value: 'AUTO', label: 'Work it out for me' },
  { value: 'CUSTOMER_PO', label: 'Customer purchase order' },
  { value: 'VENDOR_INVOICE', label: 'Supplier bill' },
  { value: 'CAD_DRAWING', label: 'Drawing' },
  { value: 'MTR_HEAT_CERT', label: 'Mill test certificate' },
  { value: 'RFQ_PROPOSAL', label: 'Request for quote' },
  { value: 'GENERAL_DOC', label: 'Other document' },
];

const DOC_TYPE_TEXT = Object.fromEntries(DOC_TYPES.map((t) => [t.value, t.label]));

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tiff,.txt';

// A note is only a pass if it reads like one. Showing "could not read the
// drawing number" next to an OK is how a warning gets read as a reassurance.
const PROBLEM_NOTE = /could not|cannot|not read|needs a person|no allied|must not|unresolved|by hand|does not resolve/i;

const stripSymbols = (text) => String(text || '').replace(/[\p{Extended_Pictographic}️]/gu, '').trim();

function displayValue(v) {
  if (v == null || v === '') return '–';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function percent(v) {
  const n = Number(v);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '–';
}

export default function DocumentsPage() {
  const fileInputRef = useRef(null);

  const [samples, setSamples] = useState(null);
  const [samplesFallback, setSamplesFallback] = useState(false);
  const [docType, setDocType] = useState('AUTO');
  const [dragActive, setDragActive] = useState(false);
  const [scanning, setScanning] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const data = await fetchDocumentSamples();
        if (!alive) return;
        const ok = Array.isArray(data) && data.length > 0;
        setSamples(ok ? data : FALLBACK_SAMPLES);
        setSamplesFallback(!ok);
      } catch {
        if (!alive) return;
        setSamples(FALLBACK_SAMPLES);
        setSamplesFallback(true);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, []);

  async function scan(file, type, name) {
    setScanning(name);
    setError('');
    setCopied('');
    try {
      const res = await uploadDocumentScan(file, type);
      setResult(res);
    } catch (err) {
      setResult(null);
      setError(`We couldn't read "${name}". ${err.message || 'Please try again.'}`);
    } finally {
      setScanning('');
    }
  }

  function handleFile(file) {
    if (!file) return;
    scan(file, docType, file.name);
  }

  function handleSample(sample) {
    const file = new File([sample.sample_text], sample.sample_filename, { type: 'text/plain' });
    scan(file, sample.document_type, sample.title);
  }

  function onDragOver(e) {
    e.preventDefault();
    if (!dragActive) setDragActive(true);
  }

  function onDragLeave(e) {
    if (!e.currentTarget.contains(e.relatedTarget)) setDragActive(false);
  }

  function onDrop(e) {
    e.preventDefault();
    setDragActive(false);
    if (scanning) return;
    handleFile(e.dataTransfer.files?.[0]);
  }

  async function copyText() {
    try {
      await navigator.clipboard.writeText(result.raw_transcribed_text || '');
      setCopied('Copied');
    } catch {
      setCopied('Copy failed');
    }
    setTimeout(() => setCopied(''), 2500);
  }

  return (
    <Page>
      <Columns side="left">
        <div>
          <Card title="Upload a document" description="Customer POs, supplier bills, drawings and mill certificates.">
            <div className={styles.upload}>
              <Field label="What kind of document is it?">
                <Select value={docType} onChange={(e) => setDocType(e.target.value)} options={DOC_TYPES} />
              </Field>

              <div
                className={`${styles.dropzone} ${dragActive ? styles.dropzoneActive : ''}`}
                onDragOver={onDragOver}
                onDragEnter={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
              >
                <svg className={styles.dropIcon} viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
                </svg>
                <p className={styles.dropTitle}>Drag a file here</p>
                <p className={styles.dropHint}>PDF, image (PNG, JPG, TIFF) or text file</p>
                <Button
                  variant="primary"
                  onClick={() => fileInputRef.current?.click()}
                  loading={Boolean(scanning)}
                  disabled={Boolean(scanning)}
                >
                  {scanning ? 'Reading…' : 'Choose a file'}
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  className={styles.hiddenInput}
                  accept={ACCEPT}
                  onChange={(e) => {
                    handleFile(e.target.files?.[0]);
                    e.target.value = '';
                  }}
                />
              </div>
            </div>
          </Card>

          <Card title="No file to hand?" description="Try one of these examples.">
            {!samples ? (
              <Skeleton lines={3} />
            ) : (
              <div className={styles.samples}>
                {samples.map((s) => (
                  <button
                    key={s.sample_id}
                    type="button"
                    className={styles.sample}
                    onClick={() => handleSample(s)}
                    disabled={Boolean(scanning)}
                  >
                    <span className={styles.sampleTitle}>{s.title}</span>
                    <span className={styles.sampleMeta}>{DOC_TYPE_TEXT[s.document_type] || 'Document'}</span>
                  </button>
                ))}
                {samplesFallback && (
                  <p className={styles.quiet}>The server&apos;s example list didn&apos;t load, so these are built-in copies.</p>
                )}
              </div>
            )}
          </Card>
        </div>

        <div>
          {error && <Notice tone="danger" title="Something went wrong">{error}</Notice>}

          {scanning ? (
            <Card title={<span className={styles.wrap}>Reading {scanning}</span>} description="Finding part numbers, quantities and prices.">
              <div className={styles.reading}>
                <Spinner />
                <Skeleton lines={6} />
              </div>
            </Card>
          ) : result ? (
            <ScanResult result={result} copied={copied} onCopy={copyText} />
          ) : (
            !error && (
              <Card>
                <EmptyState
                  title="Nothing read yet"
                  description="Upload a document or pick an example. What we find (customer, PO number, items and prices) will show here."
                />
              </Card>
            )
          )}
        </div>
      </Columns>
    </Page>
  );
}

function ScanResult({ result, copied, onCopy }) {
  const fields = Object.entries(result.fields || {});
  const lines = result.line_items || [];
  const notes = result.compliance_notes || [];
  const actions = result.suggested_actions || [];
  const rec = result.reconciliation;

  const lineColumns = [
    { key: 'item_id', label: 'Part', render: (l) => <span className={styles.mono}>{l.item_id || 'Custom'}</span> },
    {
      key: 'description',
      label: 'Description',
      render: (l) => (
        <div className={styles.lineDesc}>
          <span>{l.description}</span>
          {(l.dimensions || l.drawing_ref || l.heat_number) && (
            <span className={styles.quiet}>
              {[l.dimensions, l.drawing_ref && `Drawing ${l.drawing_ref}`, l.heat_number && `Heat ${l.heat_number}`]
                .filter(Boolean)
                .join(' · ')}
            </span>
          )}
          {Number(l.confidence) < 0.8 && <Badge tone="warning">Please check</Badge>}
        </div>
      ),
    },
    { key: 'quantity', label: 'Qty', align: 'right', render: (l) => Number(l.quantity) },
    { key: 'unit_price', label: 'Unit price', align: 'right', render: (l) => (l.unit_price ? money(l.unit_price) : '–') },
    { key: 'preferred_vendor', label: 'Supplier', render: (l) => l.preferred_vendor || 'Stock' },
  ];

  return (
    <>
      <Card
        title={<span className={styles.wrap}>{result.filename}</span>}
        description={`Read with ${percent(result.confidence_score)} confidence`}
        actions={<Badge tone="info">{DOC_TYPE_TEXT[result.document_type] || result.document_type || 'Document'}</Badge>}
      >
        <div className={styles.result}>
          {rec && <Reconciliation rec={rec} />}

          {fields.length > 0 ? (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>Details found</h3>
              <DetailList
                items={fields.map(([key, f]) => [
                  (f.field_name || key).replace(/_/g, ' '),
                  <span key={key} className={styles.fieldValue}>
                    {displayValue(f.normalized_value ?? f.raw_value)}
                    {Number(f.confidence) < 0.8 && <Badge tone="warning">Please check</Badge>}
                  </span>,
                ])}
              />
            </section>
          ) : (
            <p className={styles.quiet}>No labelled details were found in this document.</p>
          )}

          {notes.length > 0 && (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>Checks</h3>
              <ul className={styles.notes}>
                {notes.map((note, i) => {
                  const problem = PROBLEM_NOTE.test(note);
                  return (
                    <li key={i} className={styles.note}>
                      <Badge tone={problem ? 'warning' : 'success'}>{problem ? 'Check' : 'OK'}</Badge>
                      <span>{note}</span>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          {actions.length > 0 && (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>Next steps</h3>
              <div className={styles.actions}>
                {actions.map((a, i) => (
                  <Link key={i} href={a.route_url} className={i === 0 ? styles.linkPrimary : styles.linkSecondary}>
                    {stripSymbols(a.label)}
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
      </Card>

      <Card title={`Items found (${lines.length})`} flush>
        <DataTable
          columns={lineColumns}
          rows={lines}
          rowKey={(l, i) => `${l.item_id || 'line'}-${i}`}
          empty="No line items were found in this document."
        />
      </Card>

      <Card
        title="Full text"
        description="Everything the reader picked up, word for word."
        actions={
          <Button size="sm" onClick={onCopy}>
            {copied || 'Copy text'}
          </Button>
        }
      >
        <details className={styles.fullText}>
          <summary>Show text</summary>
          <pre className={styles.pre}>{result.raw_transcribed_text || 'No text was read.'}</pre>
        </details>
        <p className={styles.meta}>
          Reference {result.scan_id}
          {result.processing_time_ms != null && ` · read in ${Math.round(result.processing_time_ms)} ms`}
          {result.ocr_engine && ` · ${result.ocr_engine}`}
        </p>
      </Card>
    </>
  );
}

function Reconciliation({ rec }) {
  const ok = rec.reconciliation_status === 'RECONCILED_APPROVED';
  const issues = rec.discrepancies || [];
  return (
    <div className={styles.recon}>
      <Notice tone={ok ? 'success' : 'danger'} title={ok ? 'Matches the purchase order. Safe to pay.' : `Overbilled by ${money(rec.cost_variance_amount || 0)}. Payment held.`}>
        PO {rec.po_number} · approved {money(rec.authorized_po_total || 0)} · billed {money(rec.actual_billed_total || 0)}
      </Notice>
      {issues.length > 0 && (
        <ul className={styles.issues}>
          {issues.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}
      {rec.dispute_email_draft && (
        <p className={styles.quiet}>
          A dispute letter has been written and is waiting in{' '}
          <Link href="/automation" className={styles.inlineLink}>
            What runs by itself
          </Link>
          .
        </p>
      )}
    </div>
  );
}
