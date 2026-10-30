'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Badge,
  Card,
  Columns,
  DetailList,
  EmptyState,
  Notice,
  Page,
  Skeleton,
  Stat,
  StatGrid,
} from '@/components/ui';
import { fetchAutomationStatus, fetchExceptions, fetchHealth } from '@/lib/api';
import styles from './page.module.css';

const REFRESH_MS = 30000;

const SHORTCUTS = [
  { href: '/operations', label: 'Orders & Sage', hint: 'Order emails, supplier bills, Sage files' },
  { href: '/documents', label: 'Documents', hint: 'Read a PO, drawing or mill certificate' },
  { href: '/sales/pipeline', label: 'Wear & quotes', hint: 'Replacement parts coming due' },
  { href: '/chat', label: 'Ask the assistant', hint: 'Specs, procedures, past orders' },
];

function pct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : '–';
}

export default function OverviewPage() {
  const [automation, setAutomation] = useState(null);
  const [health, setHealth] = useState(null);
  const [exceptions, setExceptions] = useState(null);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    let alive = true;
    async function tick() {
      const [auto, hp, exc] = await Promise.allSettled([fetchAutomationStatus(), fetchHealth(), fetchExceptions()]);
      if (!alive) return;
      if (auto.status === 'fulfilled') setAutomation(auto.value);
      if (hp.status === 'fulfilled') setHealth(hp.value);
      if (exc.status === 'fulfilled') setExceptions(exc.value || []);
      setErrors({
        automation: auto.status === 'rejected' ? auto.reason?.message : '',
        health: hp.status === 'rejected' ? hp.reason?.message : '',
        exceptions: exc.status === 'rejected' ? exc.reason?.message : '',
      });
    }
    tick();
    const t = setInterval(tick, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const minMargin = health?.components?.compliance_rules?.min_gross_margin_pct;
  const openExceptions = exceptions ? exceptions.filter((e) => e.status === 'OPEN').length : null;

  const offline = errors.automation && errors.health && errors.exceptions;

  return (
    <Page
      actions={
        <Link href="/operations" className={styles.primaryLink}>
          Go to Orders &amp; Sage
        </Link>
      }
    >
      {offline && (
        <Notice tone="danger" title="Can’t reach the server right now.">
          Figures below may be out of date. This page retries every 30 seconds.
        </Notice>
      )}

      <StatGrid>
        <Stat
          label="Needs attention"
          value={openExceptions}
          tone={openExceptions ? 'warning' : openExceptions === 0 ? 'success' : undefined}
          hint={errors.exceptions ? 'Could not load' : undefined}
        />
        <Stat label="Minimum margin allowed" value={minMargin != null ? pct(minMargin) : null} />
      </StatGrid>

      <Columns>
        <div>
          <Card
            title="Customer orders"
            description="Each order is split across the suppliers that fill it, with Allied-branded delivery slips."
          >
            {/* There is no orders list endpoint yet, so this page shows no order or money figures
                rather than made-up ones. Real orders live on Orders & Sage. */}
            <EmptyState
              title="Orders are listed on Orders & Sage."
              description="Order emails, supplier bills and the sales waiting for Sage are tracked there."
              action={
                <Link href="/operations" className={styles.textLink}>
                  Open Orders &amp; Sage
                </Link>
              }
            />
          </Card>
        </div>

        <div>
          {openExceptions > 0 && (
            <Notice
              tone="warning"
              title={`${openExceptions} ${openExceptions === 1 ? 'order needs' : 'orders need'} a person`}
              action={
                <Link href="/operations" className={styles.textLink}>
                  Review
                </Link>
              }
            >
              Price changes, low margins or unknown part numbers.
            </Notice>
          )}

          <Card
            title="What runs by itself"
            actions={
              <Link href="/automation" className={styles.textLink}>
                Details
              </Link>
            }
          >
            {automation ? (
              <div className={styles.autoBody}>
                <div className={styles.autoHead}>
                  <span className={styles.autoPct}>{automation.busy_work_removed_pct}%</span>
                  <span className={styles.autoText}>
                    of the busy work is automatic. About {automation.hand_minutes_remaining} minutes per order is
                    still done by hand, down from {automation.baseline_minutes_per_order}.
                  </span>
                </div>
                <ul className={styles.links}>
                  {(automation.links || []).map((l) => (
                    <li key={l.key} className={styles.linkRow}>
                      <div className={styles.linkText}>
                        <span className={styles.linkName}>{l.name}</span>
                        <span className={styles.linkDetail}>{l.live ? l.detail : l.needs || l.detail}</span>
                      </div>
                      <Badge tone={l.live ? 'success' : 'neutral'}>{l.live ? 'On' : 'Not yet'}</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            ) : errors.automation ? (
              <EmptyState title="Couldn’t load automation status." description={errors.automation} />
            ) : (
              <Skeleton lines={4} />
            )}
          </Card>

          <Card
            title="System"
            actions={
              <Link href="/health" className={styles.textLink}>
                Full check
              </Link>
            }
          >
            {health ? (
              <DetailList
                items={[
                  [
                    'Sage database',
                    health.components?.sage_odbc_reader?.mode === 'live_odbc' ? 'Connected' : 'Sample data',
                  ],
                  [
                    'Sage orders',
                    health.components?.sage_com_sdk_writer?.mode === 'live_com' ? 'Written directly' : 'Import files',
                  ],
                  [
                    'Catalogs',
                    `${health.components?.in_memory_catalogs?.items_loaded ?? '–'} products, ${
                      health.components?.in_memory_catalogs?.customers_loaded ?? '–'
                    } customers, ${health.components?.in_memory_catalogs?.vendors_loaded ?? '–'} suppliers`,
                  ],
                  [
                    'Delivery slips',
                    health.components?.compliance_rules?.blind_shipping_default
                      ? 'Supplier names hidden'
                      : 'Supplier names shown',
                  ],
                ]}
              />
            ) : errors.health ? (
              <EmptyState title="Couldn’t load system status." description={errors.health} />
            ) : (
              <Skeleton lines={4} />
            )}
          </Card>

          <Card title="Shortcuts" flush>
            <ul className={styles.shortcuts}>
              {SHORTCUTS.map((s) => (
                <li key={s.href}>
                  <Link href={s.href} className={styles.shortcut}>
                    <span className={styles.shortcutLabel}>{s.label}</span>
                    <span className={styles.shortcutHint}>{s.hint}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </Columns>
    </Page>
  );
}
