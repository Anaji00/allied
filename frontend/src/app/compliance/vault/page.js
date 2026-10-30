'use client';

import { useCallback, useEffect, useState } from 'react';
import { fetchNCRs, dispositionNCR, fetchMTRByHeat } from '@/lib/api';
import {
  Page, Card, Columns, Button, Badge, StatGrid, Stat, Tabs, EmptyState, Notice, Field, Input, Select, Textarea,
  DetailList, Skeleton,
} from '@/components/ui';
import styles from './vault.module.css';

const DEFAULT_HEAT = 'HT-300XL-8819';

const STATUS = {
  QUARANTINED: { tone: 'danger', text: 'On hold' },
  REJECTED: { tone: 'neutral', text: 'Rejected, returned' },
  CONCESSION_APPROVED: { tone: 'success', text: 'Accepted with concession' },
};

const ACTIONS = [
  { value: 'RETURN_TO_SUPPLIER', label: 'Reject the lot and return it to the supplier' },
  { value: 'APPROVE_CONCESSION', label: 'Accept it for non-critical use (concession)' },
  { value: 'REQUEST_LAB_RETEST', label: 'Keep on hold and send for a lab retest' },
];

const CHEMISTRY = [
  ['carbon_pct', 'Carbon (C)'],
  ['chromium_pct', 'Chromium (Cr)'],
  ['manganese_pct', 'Manganese (Mn)'],
  ['silicon_pct', 'Silicon (Si)'],
  ['molybdenum_pct', 'Molybdenum (Mo)'],
  ['nickel_pct', 'Nickel (Ni)'],
  ['phosphorus_pct', 'Phosphorus (P)'],
  ['sulfur_pct', 'Sulfur (S)'],
];

function psi(v) {
  return v == null ? null : `${Number(v).toLocaleString()} PSI`;
}

export default function ComplianceVaultPage() {
  // Non-conformance reports
  const [ncrs, setNcrs] = useState(null);
  const [ncrError, setNcrError] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [reloadKey, setReloadKey] = useState(0);
  const reloadNcrs = useCallback(() => setReloadKey((k) => k + 1), []);

  // Disposition dialog
  const [selectedNcr, setSelectedNcr] = useState(null);
  const [dispositionNotes, setDispositionNotes] = useState('');
  const [dispositionAction, setDispositionAction] = useState('RETURN_TO_SUPPLIER');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');

  // Heat number lookup
  const [searchHeat, setSearchHeat] = useState(DEFAULT_HEAT);
  const [mtrResult, setMtrResult] = useState(null);
  const [mtrError, setMtrError] = useState('');
  const [mtrLoading, setMtrLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const data = await fetchNCRs();
        if (!alive) return;
        setNcrs(Array.isArray(data) ? data : []);
        setNcrError('');
      } catch (err) {
        if (!alive) return;
        setNcrs((prev) => prev || []);
        setNcrError(err.message || 'Could not load quality holds.');
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  // Show a certificate straight away so the panel is never blank.
  useEffect(() => {
    let alive = true;
    async function first() {
      try {
        const data = await fetchMTRByHeat(DEFAULT_HEAT);
        if (alive) setMtrResult(data);
      } catch (err) {
        if (alive) setMtrError(err.message);
      } finally {
        if (alive) setMtrLoading(false);
      }
    }
    first();
    return () => {
      alive = false;
    };
  }, []);

  async function handleSearchMtr(e) {
    e?.preventDefault();
    const heat = searchHeat.trim();
    if (!heat) return;
    setMtrLoading(true);
    setMtrError('');
    try {
      const data = await fetchMTRByHeat(heat);
      setMtrResult(data);
    } catch (err) {
      setMtrResult(null);
      setMtrError(err.message || `No certificate found for ${heat}.`);
    } finally {
      setMtrLoading(false);
    }
  }

  function openDisposition(n) {
    setSelectedNcr(n);
    setSubmitError('');
  }

  async function handleDispositionSubmit() {
    if (!selectedNcr) return;
    setSubmitting(true);
    setSubmitError('');
    try {
      await dispositionNCR(selectedNcr.ncr_id, {
        action: dispositionAction,
        disposition_notes: dispositionNotes || 'Dispositioned via Compliance Console',
        actor_email: 'qa_lead@alliedweldingsystems.com',
      });
      setSelectedNcr(null);
      setDispositionNotes('');
      reloadNcrs();
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const list = ncrs || [];
  const count = (s) => list.filter((n) => n.status === s).length;
  const filtered = statusFilter === 'ALL' ? list : list.filter((n) => (n.status || '').toUpperCase() === statusFilter);
  const quarantined = count('QUARANTINED');

  const chem = mtrResult?.chemical_analysis || {};
  const mech = mtrResult?.mechanical_tests || {};
  const chemRows = CHEMISTRY.filter(([k]) => chem[k] != null);

  return (
    <Page>
      <StatGrid>
        <Stat label="Lots on hold" value={ncrs ? quarantined : null} tone={quarantined ? 'danger' : undefined} />
        <Stat label="Rejected and returned" value={ncrs ? count('REJECTED') : null} />
        <Stat label="Accepted with concession" value={ncrs ? count('CONCESSION_APPROVED') : null} />
      </StatGrid>

      <Columns>
        <Card
          title="Quality holds"
          description="Material lots that failed a chemistry or hardness limit. Decide what happens to each one."
          flush
        >
          <div className={styles.tabs}>
            <Tabs
              value={statusFilter}
              onChange={setStatusFilter}
              tabs={[
                { key: 'ALL', label: 'All', count: list.length },
                { key: 'QUARANTINED', label: 'On hold', count: quarantined },
                { key: 'REJECTED', label: 'Rejected', count: count('REJECTED') },
                { key: 'CONCESSION_APPROVED', label: 'Concessions', count: count('CONCESSION_APPROVED') },
              ]}
            />
          </div>

          {ncrError && (
            <div className={styles.pad}>
              <Notice tone="danger" action={<Button size="sm" onClick={reloadNcrs}>Try again</Button>}>{ncrError}</Notice>
            </div>
          )}

          {!ncrs ? (
            <div className={styles.pad}><Skeleton lines={5} /></div>
          ) : filtered.length === 0 ? (
            <EmptyState title={statusFilter === 'QUARANTINED' ? 'Nothing is on hold' : 'No records here'} />
          ) : (
            <ul className={styles.ncrList}>
              {filtered.map((n) => {
                const st = STATUS[n.status] || { tone: 'neutral', text: n.status };
                return (
                  <li key={n.ncr_id} className={styles.ncr}>
                    <div className={styles.ncrTop}>
                      <div className={styles.ncrTitle}>
                        <strong>Heat {n.heat_number}</strong>
                        <span className={styles.muted}>{n.ncr_id} · {n.item_id}</span>
                      </div>
                      <Badge tone={st.tone}>{st.text}</Badge>
                    </div>
                    <p className={styles.meta}>
                      {n.supplier} · {n.material_spec}
                    </p>
                    {(n.failure_reasons || []).length > 0 && (
                      <ul className={styles.reasons}>
                        {n.failure_reasons.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    )}
                    {n.disposition_notes && (
                      <p className={styles.muted}>
                        Decision: {n.disposition_notes}{n.resolved_by ? ` (${n.resolved_by})` : ''}
                      </p>
                    )}
                    {n.status === 'QUARANTINED' && (
                      <div className={styles.ncrActions}>
                        <Button size="sm" variant="primary" onClick={() => openDisposition(n)}>Decide</Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card title="Look up a heat number" description="Check a mill certificate against its ASTM limits.">
          <form className={styles.search} onSubmit={handleSearchMtr}>
            <Input
              aria-label="Heat number"
              value={searchHeat}
              onChange={(e) => setSearchHeat(e.target.value)}
              placeholder="e.g. HT-300XL-8819"
            />
            <Button type="submit" loading={mtrLoading}>Check</Button>
          </form>

          <div className={styles.result}>
            {mtrLoading && !mtrResult ? (
              <Skeleton lines={5} />
            ) : mtrError ? (
              <Notice tone="warning">{mtrError}</Notice>
            ) : mtrResult ? (
              <>
                <div className={styles.mtrHead}>
                  <div className={styles.ncrTitle}>
                    <strong>{mtrResult.heat_number}</strong>
                    <span className={styles.muted}>{mtrResult.material_spec}</span>
                  </div>
                  <Badge tone={mtrResult.verified_compliant ? 'success' : 'danger'}>
                    {mtrResult.verified_compliant ? 'Within limits' : 'Out of limits'}
                  </Badge>
                </div>

                <DetailList
                  items={[
                    ['Part', mtrResult.item_id],
                    ['Mill', mtrResult.mill_supplier],
                  ]}
                />

                <h3 className={styles.subhead}>Chemistry (% by weight)</h3>
                {chemRows.length ? (
                  <DetailList items={chemRows.map(([k, label]) => [label, `${chem[k]}%`])} />
                ) : (
                  <p className={styles.muted}>Not recorded on this certificate.</p>
                )}

                <h3 className={styles.subhead}>Mechanical tests</h3>
                <DetailList
                  items={[
                    ['Hardness', mech.hardness_rating],
                    ['Yield strength', psi(mech.yield_strength_psi)],
                    ['Tensile strength', psi(mech.tensile_strength_psi)],
                    ['Elongation', mech.elongation_pct != null ? `${mech.elongation_pct}%` : null],
                  ]}
                />

                {(mtrResult.compliance_notes || []).length > 0 && (
                  <p className={styles.notes}>{mtrResult.compliance_notes.join(' ')}</p>
                )}
              </>
            ) : null}
          </div>
        </Card>
      </Columns>

      {selectedNcr && (
        <div className={styles.backdrop} onClick={() => setSelectedNcr(null)}>
          <div
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="ncr-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.dialogHead}>
              <h2 id="ncr-title" className={styles.dialogTitle}>Decide on heat {selectedNcr.heat_number}</h2>
              <p className={styles.muted}>
                {selectedNcr.ncr_id} · {selectedNcr.item_id} from {selectedNcr.supplier}
              </p>
            </div>

            <div className={styles.dialogBody}>
              <Field label="What should happen to this lot?">
                <Select value={dispositionAction} onChange={(e) => setDispositionAction(e.target.value)} options={ACTIONS} />
              </Field>
              <Field label="Reason" hint="Saved to the audit trail.">
                <Textarea
                  value={dispositionNotes}
                  onChange={(e) => setDispositionNotes(e.target.value)}
                  placeholder="Why this decision was made"
                  rows={4}
                />
              </Field>
              {submitError && <Notice tone="danger">{submitError}</Notice>}
            </div>

            <div className={styles.dialogFoot}>
              <Button onClick={() => setSelectedNcr(null)}>Cancel</Button>
              <Button variant="primary" loading={submitting} onClick={handleDispositionSubmit}>
                {submitting ? 'Saving…' : 'Save decision'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
