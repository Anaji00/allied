'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { fetchPredictiveWear, draftQuoteFromWear } from '@/lib/api';
import {
  Page, Card, Button, Badge, StatGrid, Stat, Tabs, DataTable, EmptyState, Notice, DetailList, Skeleton, money,
} from '@/components/ui';
import styles from './pipeline.module.css';

function wearLevel(eq) {
  const pct = Number(eq.estimated_wear_pct) || 0;
  if (eq.alert_level === 'CRITICAL_REPLACE' || pct >= 95) return 'critical';
  if (eq.alert_level === 'WARNING_75_PCT' || pct >= 75) return 'warning';
  return 'ok';
}

const LEVEL = {
  critical: { tone: 'danger', text: 'Replace now' },
  warning: { tone: 'warning', text: 'Reorder soon' },
  ok: { tone: 'success', text: 'Good' },
};

export default function SalesPipelinePage() {
  const router = useRouter();
  const [equipment, setEquipment] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [filter, setFilter] = useState('all');

  const [draftedQuote, setDraftedQuote] = useState(null);
  const [draftingTag, setDraftingTag] = useState(null);
  const [draftError, setDraftError] = useState('');

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const data = await fetchPredictiveWear();
        if (!alive) return;
        setEquipment(Array.isArray(data) ? data : []);
        setLoadError('');
      } catch (e) {
        if (!alive) return;
        setEquipment([]);
        setLoadError(e.message || 'Could not load equipment.');
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, []);

  async function handleDraftQuote(eq) {
    setDraftingTag(eq.equipment_tag);
    setDraftError('');
    try {
      const data = await draftQuoteFromWear({
        equipment_tag: eq.equipment_tag,
        apply_volume_discount_pct: 5.0,
        target_delivery_weeks: 2,
      });
      setDraftedQuote(data);
    } catch (err) {
      setDraftError(`Could not create the quote for ${eq.equipment_tag}: ${err.message}`);
    } finally {
      setDraftingTag(null);
    }
  }

  function turnIntoOrder() {
    const q = draftedQuote;
    setDraftedQuote(null);
    const promptText = `Process order for customer ${q.customer_name || 'Allied Customer'}: 1x ${q.suggested_sku} at $${q.quoted_unit_price} for equipment ${q.equipment_tag}`;
    router.push(`/chat?prompt=${encodeURIComponent(promptText)}`);
  }

  const list = equipment || [];
  const counts = { all: list.length, warning: 0, critical: 0 };
  list.forEach((eq) => {
    const lvl = wearLevel(eq);
    if (lvl !== 'ok') counts[lvl] += 1;
  });
  const rows = filter === 'all' ? list : list.filter((eq) => wearLevel(eq) === filter);

  const columns = [
    {
      key: 'part',
      label: 'Part',
      render: (eq) => (
        <div className={styles.cellMain}>
          <strong>{eq.component_name}</strong>
          <span className={styles.muted}>{eq.equipment_tag}</span>
        </div>
      ),
    },
    {
      key: 'customer',
      label: 'Customer',
      render: (eq) => (
        <div className={styles.cellMain}>
          <span>{eq.customer_name}</span>
          <span className={styles.muted}>{eq.plant_location}</span>
        </div>
      ),
    },
    {
      key: 'wear',
      label: 'Wear',
      render: (eq) => {
        const lvl = wearLevel(eq);
        const pct = Number(eq.estimated_wear_pct) || 0;
        return (
          <div className={styles.wear}>
            <div className={styles.wearTop}>
              <strong>{pct.toFixed(0)}%</strong>
              <Badge tone={LEVEL[lvl].tone}>{LEVEL[lvl].text}</Badge>
            </div>
            <div className={styles.bar}>
              <div className={`${styles.barFill} ${styles[`fill_${lvl}`]}`} style={{ width: `${Math.min(pct, 100)}%` }} />
            </div>
            <span className={styles.muted}>
              {eq.days_in_service} of {eq.expected_lifespan_days} days
            </span>
          </div>
        );
      },
    },
    {
      key: 'last',
      label: 'Last order',
      render: (eq) => (
        <div className={styles.cellMain}>
          <span>{eq.last_service_order || '–'}</span>
          <span className={styles.muted}>{money(eq.historical_unit_price)} each</span>
        </div>
      ),
    },
    {
      key: 'action',
      label: '',
      align: 'right',
      render: (eq) => (
        <Button size="sm" onClick={() => handleDraftQuote(eq)} loading={draftingTag === eq.equipment_tag} disabled={draftingTag != null}>
          Create quote
        </Button>
      ),
    },
  ];

  return (
    <Page>
      {loadError && <Notice tone="danger" title="Could not load equipment">{loadError}</Notice>}
      {draftError && (
        <Notice tone="danger" action={<Button size="sm" variant="ghost" onClick={() => setDraftError('')}>Dismiss</Button>}>
          {draftError}
        </Notice>
      )}

      <StatGrid>
        <Stat label="Machines tracked" value={equipment ? counts.all : null} />
        <Stat label="Reorder soon (75%+ worn)" value={equipment ? counts.warning : null} tone={counts.warning ? 'warning' : undefined} />
        <Stat label="Replace now (95%+ worn)" value={equipment ? counts.critical : null} tone={counts.critical ? 'danger' : undefined} />
      </StatGrid>

      <Card
        title="Customer equipment"
        description="Parts we supply, and how worn they are. Create a quote once a part passes 75% so the customer can reorder before it fails."
        flush
      >
        <div className={styles.tabs}>
          <Tabs
            value={filter}
            onChange={setFilter}
            tabs={[
              { key: 'all', label: 'All', count: counts.all },
              { key: 'warning', label: 'Reorder soon', count: counts.warning },
              { key: 'critical', label: 'Replace now', count: counts.critical },
            ]}
          />
        </div>
        {!equipment ? (
          <div className={styles.pad}><Skeleton lines={5} /></div>
        ) : rows.length === 0 ? (
          <EmptyState
            title={list.length === 0 ? 'No equipment is being tracked yet' : 'Nothing in this group'}
            description={list.length === 0 ? 'Tracked customer parts will show up here.' : undefined}
          />
        ) : (
          <DataTable columns={columns} rows={rows} rowKey={(eq) => eq.equipment_tag} />
        )}
      </Card>

      {draftedQuote && (
        <div className={styles.backdrop} onClick={() => setDraftedQuote(null)}>
          <div
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="quote-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className={styles.dialogHead}>
              <div>
                <h2 id="quote-title" className={styles.dialogTitle}>Quote {draftedQuote.quote_number} created</h2>
                <p className={styles.muted}>Saved to the sales board for review.</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setDraftedQuote(null)} aria-label="Close">
                Close
              </Button>
            </div>

            <div className={styles.dialogBody}>
              <DetailList
                items={[
                  ['Customer', draftedQuote.customer_name],
                  ['Plant', draftedQuote.plant_location],
                  ['Equipment', `${draftedQuote.equipment_tag} (${draftedQuote.component_name})`],
                  ['Replacement part', draftedQuote.suggested_sku],
                ]}
              />

              {draftedQuote.urgency_rationale && <Notice tone="warning">{draftedQuote.urgency_rationale}</Notice>}

              <div className={styles.prices}>
                <DetailList
                  items={[
                    ['Regular price', money(draftedQuote.regular_price)],
                    ['Early reorder discount', `${draftedQuote.discount_applied_pct}%`],
                    ['Quoted price', <strong key="q">{money(draftedQuote.quoted_unit_price)}</strong>],
                  ]}
                />
              </div>
            </div>

            <div className={styles.dialogFoot}>
              <Button onClick={() => setDraftedQuote(null)}>Done</Button>
              <Button variant="primary" onClick={turnIntoOrder}>Turn into an order</Button>
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}
