'use client';

import { useEffect, useState } from 'react';
import { fetchTargetAccounts, calculateWearROI, generateLeadOutreach } from '@/lib/api';
import {
  Page, Card, Button, Badge, StatGrid, Stat, Tabs, DataTable, EmptyState, Notice, Field, Input, Select,
  Textarea, DetailList, Skeleton, Spinner, money,
} from '@/components/ui';
import styles from './leads.module.css';

const INDUSTRIES = [
  { value: 'ALL', label: 'All industries' },
  { value: 'Asphalt', label: 'Asphalt & roofing' },
  { value: 'Glass', label: 'Glass & mineral wool' },
  { value: 'Cement', label: 'Cement & clinker' },
  { value: 'Gypsum', label: 'Gypsum & wallboard' },
];

const EQUIPMENT = ['Hammermill Screen', 'Slurry Material Hose', 'Screw Conveyor Flight', 'Chute Impact Liner'].map((v) => ({
  value: v,
  label: v,
}));

const STATUS_TONE = { ACTIVE: 'success', QUALIFIED: 'info' };

const DEFAULT_ROI = {
  equipment_type: 'Hammermill Screen',
  current_material: 'AR400 Carbon Steel',
  current_part_cost: 1200,
  current_lifespan_months: 3,
  allied_upgrade_material: 'Allied 300XL Chromium Carbide Overlay',
  allied_part_cost: 2800,
  allied_lifespan_months: 12,
  replacement_labor_cost: 2500,
  downtime_cost_per_hour: 1500,
  downtime_hours_per_swap: 6,
};

// Calculator inputs, in the order they are shown.
const ROI_FIELDS = [
  { key: 'current_material', label: 'Current material', type: 'text' },
  { key: 'allied_upgrade_material', label: 'Allied material', type: 'text' },
  { key: 'current_part_cost', label: 'Current part cost ($)' },
  { key: 'allied_part_cost', label: 'Allied part cost ($)' },
  { key: 'current_lifespan_months', label: 'Current part lasts (months)' },
  { key: 'allied_lifespan_months', label: 'Allied part lasts (months)' },
  { key: 'replacement_labor_cost', label: 'Labour and crane per swap ($)' },
  { key: 'downtime_cost_per_hour', label: 'Downtime cost per hour ($)' },
  { key: 'downtime_hours_per_swap', label: 'Downtime hours per swap' },
];

export default function SalesLeadsPage() {
  const [tab, setTab] = useState('accounts');

  // Target accounts
  const [targets, setTargets] = useState(null);
  const [targetsError, setTargetsError] = useState('');
  const [industry, setIndustry] = useState('ALL');

  // Outreach email draft
  const [selectedTarget, setSelectedTarget] = useState(null);
  const [emailDraft, setEmailDraft] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [copied, setCopied] = useState(false);

  // Cost-of-ownership calculator
  const [roiParams, setRoiParams] = useState(DEFAULT_ROI);
  const [roiResult, setRoiResult] = useState(null);
  const [roiError, setRoiError] = useState('');
  const [calcLoading, setCalcLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const data = await fetchTargetAccounts();
        if (!alive) return;
        setTargets(Array.isArray(data) ? data : []);
        setTargetsError('');
      } catch (err) {
        if (!alive) return;
        setTargets([]);
        setTargetsError(err.message || 'Could not load accounts.');
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, []);

  // Recalculate whenever an input changes, after a short pause in typing.
  useEffect(() => {
    let alive = true;
    const t = setTimeout(async () => {
      setCalcLoading(true);
      try {
        const res = await calculateWearROI(roiParams);
        if (!alive) return;
        setRoiResult(res);
        setRoiError('');
      } catch (err) {
        if (alive) setRoiError(err.message || 'Could not work out the savings.');
      } finally {
        if (alive) setCalcLoading(false);
      }
    }, 300);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [roiParams]);

  function handleRoiChange(field, value) {
    setRoiParams((p) => ({ ...p, [field]: Number(value) || value }));
  }

  async function handleOpenOutreach(target) {
    setSelectedTarget(target);
    setCopied(false);
    setDrafting(true);
    try {
      const res = await generateLeadOutreach({
        name: target.key_contact,
        title: target.contact_title,
        company: target.company,
        industry: target.industry,
        email: `${target.key_contact.toLowerCase().replace(' ', '.')}@${target.company.toLowerCase().replace(/[^a-z]/g, '')}.com`,
      });
      setEmailDraft(res.email_body);
    } catch {
      setEmailDraft(
        `Hi ${target.key_contact},\n\nWe specialize in extending wear life on ${(target.primary_wear_bottlenecks || []).join(', ')}...`
      );
    } finally {
      setDrafting(false);
    }
  }

  async function copyDraft() {
    try {
      await navigator.clipboard.writeText(emailDraft);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const list = targets || [];
  const filtered = industry === 'ALL' ? list : list.filter((t) => (t.industry || '').toLowerCase().includes(industry.toLowerCase()));
  const totalSpend = list.reduce((sum, t) => sum + (Number(t.estimated_annual_spend) || 0), 0);
  const activeCount = list.filter((t) => t.status === 'ACTIVE').length;

  const columns = [
    {
      key: 'company',
      label: 'Company',
      render: (t) => (
        <div className={styles.cellMain}>
          <strong>{t.company}</strong>
          <span className={styles.muted}>{t.plant_name} · {t.location}</span>
        </div>
      ),
    },
    {
      key: 'contact',
      label: 'Contact',
      render: (t) => (
        <div className={styles.cellMain}>
          <span>{t.key_contact}</span>
          <span className={styles.muted}>{t.contact_title}</span>
        </div>
      ),
    },
    {
      key: 'wear',
      label: 'Parts that wear out',
      render: (t) => <span className={styles.wearList}>{(t.primary_wear_bottlenecks || []).join(', ') || '–'}</span>,
    },
    { key: 'spend', label: 'Est. spend / yr', align: 'right', render: (t) => money(t.estimated_annual_spend) },
    { key: 'status', label: 'Status', render: (t) => <Badge tone={STATUS_TONE[t.status] || 'warning'}>{t.status}</Badge> },
    {
      key: 'action',
      label: '',
      align: 'right',
      render: (t) => <Button size="sm" onClick={() => handleOpenOutreach(t)}>Draft email</Button>,
    },
  ];

  return (
    <Page>
      <StatGrid>
        <Stat label="Target accounts" value={targets ? list.length : null} />
        <Stat label="Already customers" value={targets ? activeCount : null} />
        <Stat label="Combined est. spend / yr" value={targets ? money(totalSpend) : null} hint="Sum of each account's estimate" />
      </StatGrid>

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { key: 'accounts', label: 'Accounts', count: targets ? list.length : undefined },
          { key: 'calculator', label: 'Savings calculator' },
        ]}
      />

      {tab === 'accounts' && (
        <Card
          title="Plants with heavy wear"
          description="Industrial plants that replace wear parts often. Draft an introduction email to the key contact."
          actions={
            <div className={styles.filter}>
              <Select aria-label="Industry" value={industry} onChange={(e) => setIndustry(e.target.value)} options={INDUSTRIES} />
            </div>
          }
          flush
        >
          {targetsError && <div className={styles.pad}><Notice tone="danger">{targetsError}</Notice></div>}
          {!targets ? (
            <div className={styles.pad}><Skeleton lines={5} /></div>
          ) : filtered.length === 0 ? (
            <EmptyState
              title={list.length === 0 ? 'No target accounts yet' : 'No accounts in this industry'}
              action={industry !== 'ALL' ? <Button size="sm" onClick={() => setIndustry('ALL')}>Show all</Button> : undefined}
            />
          ) : (
            <DataTable columns={columns} rows={filtered} rowKey={(t) => t.account_id} />
          )}
        </Card>
      )}

      {tab === 'calculator' && (
        <div className={styles.calc}>
          <Card title="The customer's numbers" description="Change any figure and the savings update.">
            <div className={styles.formGrid}>
              <Field label="Equipment part" className={styles.full}>
                <Select
                  value={roiParams.equipment_type}
                  onChange={(e) => handleRoiChange('equipment_type', e.target.value)}
                  options={EQUIPMENT}
                />
              </Field>
              {ROI_FIELDS.map((f) => (
                <Field key={f.key} label={f.label}>
                  <Input
                    type={f.type || 'number'}
                    inputMode={f.type ? undefined : 'decimal'}
                    value={roiParams[f.key]}
                    onChange={(e) => handleRoiChange(f.key, e.target.value)}
                  />
                </Field>
              ))}
            </div>
          </Card>

          <Card
            title="What they save with Allied"
            description="Per year, including parts, labour and downtime."
            actions={calcLoading ? <Spinner small /> : null}
          >
            {roiError && <Notice tone="danger">{roiError}</Notice>}
            {!roiResult && !roiError && <Skeleton lines={4} />}
            {roiResult && (
              <div className={styles.results}>
                <div className={styles.resultStats}>
                  <Stat label="Saved per year" value={money(roiResult.net_annual_savings)} tone="success" />
                  <Stat label="Lower running cost" value={`${roiResult.savings_percentage}%`} />
                  <Stat label="Lasts longer" value={`${roiResult.lifespan_multiplier}x`} />
                  <Stat label="Pays for itself in" value={`${roiResult.payback_period_days} days`} />
                </div>
                <DetailList
                  items={[
                    ['Yearly cost today', money(roiResult.current_annual_tco)],
                    ['Yearly cost with Allied', money(roiResult.allied_annual_tco)],
                  ]}
                />
                {roiResult.executive_summary && (
                  <div className={styles.summary}>
                    <span className={styles.summaryLabel}>Summary for the customer</span>
                    <p>{roiResult.executive_summary}</p>
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      )}

      {selectedTarget && (
        <div className={styles.backdrop} onClick={() => setSelectedTarget(null)}>
          <div
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="outreach-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.dialogHead}>
              <div>
                <h2 id="outreach-title" className={styles.dialogTitle}>Email to {selectedTarget.key_contact}</h2>
                <p className={styles.muted}>{selectedTarget.company}. Edit the draft, then copy it into your email.</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setSelectedTarget(null)}>Close</Button>
            </div>

            <div className={styles.dialogBody}>
              {drafting ? (
                <div className={styles.drafting}>
                  <Spinner />
                  <span>Writing a draft…</span>
                </div>
              ) : (
                <Textarea value={emailDraft} onChange={(e) => setEmailDraft(e.target.value)} rows={12} aria-label="Email draft" />
              )}
            </div>

            <div className={styles.dialogFoot}>
              {copied && <span className={styles.copied}>Copied</span>}
              <Button onClick={copyDraft} disabled={drafting}>Copy text</Button>
              <Button variant="primary" onClick={() => setSelectedTarget(null)}>Done</Button>
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
