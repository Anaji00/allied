'use client';

import { useCallback, useEffect, useState } from 'react';
import { fetchAuditLog } from '@/lib/api';
import { Page, Card, Button, Badge, DataTable, Notice, Field, Select, Toolbar, Skeleton } from '@/components/ui';
import styles from './audit.module.css';

const RESOURCES = [
  { value: 'ALL', label: 'Everything' },
  { value: 'ORDER', label: 'Orders' },
  { value: 'RECONCILIATION', label: 'Supplier bill checks' },
  { value: 'EXCEPTION', label: 'Resolved problems' },
  { value: 'SAGE_EXPORT', label: 'Sage exports' },
  { value: 'COMPLIANCE_NCR', label: 'Quality holds' },
  { value: 'LOGISTICS_DISPATCH', label: 'Shipping and customs' },
];

const LIMITS = [25, 50, 100].map((n) => ({ value: n, label: `Last ${n}` }));

function actionTone(action = '') {
  if (action.includes('FLAGGED') || action.includes('NCR') || action.includes('HALT')) return 'danger';
  if (action.includes('APPROVED') || action.includes('EXPORT') || action.includes('CREATED')) return 'success';
  return 'info';
}

function readable(code) {
  return code ? code.replace(/_/g, ' ').toLowerCase() : '–';
}

const COLUMNS = [
  {
    key: 'timestamp',
    label: 'When',
    render: (log) => (
      <span className={styles.when}>{log.timestamp ? new Date(log.timestamp).toLocaleString() : 'Just now'}</span>
    ),
  },
  {
    key: 'action',
    label: 'What happened',
    render: (log) => (
      <Badge tone={actionTone(log.action)} className={styles.action}>{readable(log.action)}</Badge>
    ),
  },
  {
    key: 'resource',
    label: 'Record',
    render: (log) => (
      <div className={styles.stack}>
        <span className={styles.resource}>{readable(log.resource_type)}</span>
        {log.resource_id && <span className={styles.mono}>{log.resource_id}</span>}
      </div>
    ),
  },
  {
    key: 'actor',
    label: 'Who',
    render: (log) => (
      <div className={styles.stack}>
        <span>{log.actor_email || '–'}</span>
        {log.actor_role && <span className={styles.muted}>{log.actor_role}</span>}
      </div>
    ),
  },
  { key: 'facility', label: 'Yard', render: (log) => <Badge>{log.facility || 'HQ'}</Badge> },
  { key: 'detail', label: 'Details', render: (log) => <span className={styles.detail}>{log.detail}</span> },
];

export default function AdminAuditPage() {
  const [logs, setLogs] = useState(null);
  const [error, setError] = useState('');
  const [resourceFilter, setResourceFilter] = useState('ALL');
  const [limit, setLimit] = useState(50);
  const [refreshing, setRefreshing] = useState(false);

  const [reloadKey, setReloadKey] = useState(0);
  const refresh = useCallback(() => {
    setRefreshing(true);
    setReloadKey((k) => k + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const data = await fetchAuditLog(resourceFilter === 'ALL' ? null : resourceFilter, limit);
        if (!alive) return;
        setLogs(Array.isArray(data) ? data : []);
        setError('');
      } catch (err) {
        if (alive) setError(err.message || 'Could not load the audit trail.');
      } finally {
        if (alive) setRefreshing(false);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, [resourceFilter, limit, reloadKey]);

  function exportAuditJson() {
    const blob = new Blob([JSON.stringify(logs || [], null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `allied_audit_trail_${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Page>
      <Card flush>
        <Toolbar className={styles.toolbar}>
          <Field label="Show" className={styles.filter}>
            <Select value={resourceFilter} onChange={(e) => setResourceFilter(e.target.value)} options={RESOURCES} />
          </Field>
          <Field label="How many" className={styles.limit}>
            <Select value={limit} onChange={(e) => setLimit(Number(e.target.value))} options={LIMITS} />
          </Field>
          <div className={styles.buttons}>
            <Button onClick={refresh} loading={refreshing}>Refresh</Button>
            <Button onClick={exportAuditJson} disabled={!logs || logs.length === 0}>Download</Button>
          </div>
        </Toolbar>

        {error && (
          <div className={styles.pad}>
            <Notice tone="danger" action={<Button size="sm" onClick={refresh}>Try again</Button>}>{error}</Notice>
          </div>
        )}

        {!logs && !error ? (
          <div className={styles.pad}><Skeleton lines={6} /></div>
        ) : logs ? (
          <DataTable
            columns={COLUMNS}
            rows={logs}
            rowKey={(log, i) => log.event_id ?? i}
            empty="No activity matches this filter."
          />
        ) : null}
      </Card>
    </Page>
  );
}
