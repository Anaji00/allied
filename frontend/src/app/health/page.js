'use client';

import { useCallback, useEffect, useState } from 'react';
import { fetchHealth } from '@/lib/api';
import {
  Page, Card, Button, Badge, StatGrid, Stat, Notice, DetailList, Skeleton, dateTime,
} from '@/components/ui';
import styles from './health.module.css';

const OK_STATES = ['connected', 'ready', 'healthy', 'ok'];

function StateBadge({ value }) {
  if (!value) return <Badge>Unknown</Badge>;
  const ok = OK_STATES.includes(String(value).toLowerCase());
  return <Badge tone={ok ? 'success' : 'danger'}>{ok ? 'Working' : value}</Badge>;
}

function modeText(mode) {
  if (mode === 'simulation') return 'Practice mode (no live writes)';
  if (mode === 'live_odbc' || mode === 'live_com') return 'Live';
  return mode || '–';
}

export default function HealthPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [checkedAt, setCheckedAt] = useState(null);
  // When the data on screen was last fetched successfully.
  const [okAt, setOkAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  // Bumped by the "Check now" button so the effect re-runs immediately.
  const [reloadKey, setReloadKey] = useState(0);
  const checkNow = useCallback(() => {
    setRefreshing(true);
    setReloadKey((k) => k + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const res = await fetchHealth();
        if (!alive) return;
        setData(res);
        setOkAt(new Date().toISOString());
        setError('');
      } catch (e) {
        if (alive) setError(e.message || 'Could not reach the system.');
      } finally {
        if (alive) {
          setCheckedAt(new Date().toISOString());
          setRefreshing(false);
        }
      }
    }
    tick();
    const t = setInterval(tick, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [reloadKey]);

  const comps = data?.components || {};
  const reader = comps.sage_odbc_reader || {};
  const writer = comps.sage_com_sdk_writer || {};
  const catalogs = comps.in_memory_catalogs || {};
  const rules = comps.compliance_rules || {};
  const healthy = data && String(data.status).toLowerCase() === 'healthy';
  // The latest poll failed, so what is on screen is an older reading.
  const stale = Boolean(data && error);

  return (
    <Page
      actions={
        <div className={styles.actions}>
          {checkedAt && <span className={styles.checked}>Last checked {dateTime(checkedAt)}</span>}
          <Button onClick={checkNow} loading={refreshing}>Check now</Button>
        </div>
      }
    >
      {error && (
        <Notice tone="danger" title="The system is not responding">
          {error}. It is checked again every 30 seconds.
        </Notice>
      )}

      {!data && !error && (
        <Card>
          <Skeleton lines={4} />
        </Card>
      )}

      {data && (
        <div className={stale ? styles.stale : styles.fresh}>
          {stale ? (
            <p className={styles.staleLabel}>
              Below is the last good reading, from {dateTime(okAt)}. It may be out of date.
            </p>
          ) : (
            <Notice tone={healthy ? 'success' : 'warning'} title={healthy ? 'Everything is working' : 'Something needs a look'}>
              {data.simulation_mode
                ? 'Sage is in practice mode: files are produced, but nothing is written into Sage automatically.'
                : 'Sage is connected live.'}
            </Notice>
          )}

          <StatGrid>
            <Stat label="Parts in the catalogue" value={catalogs.items_loaded} />
            <Stat label="Customers" value={catalogs.customers_loaded} />
            <Stat label="Suppliers" value={catalogs.vendors_loaded} />
            <Stat label="Alternative part names" value={catalogs.aliases_loaded} hint="Nicknames customers use for parts" />
          </StatGrid>

          <div className={styles.grid}>
            <Card
              title="Reading from Sage"
              description="Looks up parts, customers and order history. Read-only."
              actions={<StateBadge value={reader.status} />}
            >
              <DetailList
                items={[
                  ['Mode', modeText(reader.mode)],
                  ['Data source', reader.dsn],
                ]}
              />
            </Card>

            <Card
              title="Writing to Sage"
              description="Creates sales orders and the matching supplier purchase orders."
              actions={<StateBadge value={writer.status} />}
            >
              <DetailList
                items={[
                  ['Mode', modeText(writer.mode)],
                  ['Orders written since start-up', writer.transactions_executed],
                ]}
              />
            </Card>

            <Card title="Order safeguards" description="Rules every order is checked against.">
              <DetailList
                items={[
                  ['Lowest allowed margin', rules.min_gross_margin_pct != null ? `${rules.min_gross_margin_pct}%` : null],
                  ['Supplier hidden on packing slips', rules.blind_shipping_default == null ? null : rules.blind_shipping_default ? 'Yes, by default' : 'No'],
                ]}
              />
            </Card>

            <Card title="Service" description="The engine behind every screen.">
              <DetailList
                items={[
                  ['Name', data.service],
                  ['Environment', data.environment],
                  ['Reported at', dateTime(data.timestamp)],
                ]}
              />
            </Card>
          </div>
        </div>
      )}
    </Page>
  );
}
