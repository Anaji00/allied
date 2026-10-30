'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { fetchAutomationStatus, fetchOutbox, pollMailboxNow } from '@/lib/api';
import {
  Page, Card, Columns, Button, Badge, StatGrid, Stat, DataTable, EmptyState, Notice, Skeleton,
} from '@/components/ui';
import styles from './automation.module.css';

/**
 * What runs by itself, and what still lands on a person.
 *
 * Every other screen reports on records. This one reports on the automation,
 * because the question staff actually ask is not "what happened to order
 * 12946" but "do I still have to do this bit?".
 */

// The hand-work left on each order once the core system is running, itemised
// so nobody has to take the total on trust. Keyed to the automated link that
// removes each one.
const HAND_WORK = [
  { key: 'mailbox', minutes: 2, task: 'Forward the order email into the system', done: 'Collect orders straight from the mailbox' },
  { key: 'sage_write', minutes: 2, task: 'Click import in Sage', done: 'Write the order straight into Sage' },
  { key: 'invoice_scanning', minutes: 8, task: 'Key in a supplier bill so it can be checked', done: 'Check every supplier bill the moment it is read' },
  { key: 'outbound_email', minutes: 5, task: 'Email the purchase orders to suppliers', done: 'Send purchase orders and dispute letters' },
];

// Always automatic once the core system runs.
const ALWAYS_DONE = [
  { task: 'Read the order and match every line to the catalogue', note: 'Including customer part names and drawing numbers' },
  { task: 'Split it between suppliers and raise a purchase order each' },
  { task: 'Run every check and stop the order if one fails', note: 'Margin floor, stale pricing, mill certificates, duplicate orders' },
  { task: 'Produce the Sage documents, packing slip and customs papers' },
];

const REVIEW = { minutes: 5, task: 'Read the order and approve it' };

// Only a leading article is lowercased. Lowercasing the whole first letter
// turned "Sage 50 desktop" into "sage 50 desktop", which reads as a typo
// rather than as a product name.
function lowerArticle(text) {
  return text.replace(/\.$/, '').replace(/^(A|An|The)/, (m) => m.toLowerCase());
}

function MailState({ message }) {
  if (message.status === 'SENT') return <Badge tone="success">Sent</Badge>;
  if (message.status === 'FAILED') return <Badge tone="danger">Failed</Badge>;
  return <Badge tone="warning">Written, not sent</Badge>;
}

const OUTBOX_COLUMNS = [
  {
    key: 'category',
    label: 'Kind',
    render: (m) => <span className={styles.kind}>{(m.category || '').replace(/_/g, ' ').toLowerCase()}</span>,
  },
  { key: 'reference', label: 'About', render: (m) => m.reference || '–' },
  {
    key: 'to',
    label: 'To',
    render: (m) => (
      <span className={styles.toCell}>
        {(m.intended_to || []).join(', ') || '–'}
        {m.redirected && <Badge tone="info">Redirected</Badge>}
      </span>
    ),
  },
  {
    key: 'status',
    label: 'State',
    render: (m) => (
      <div className={styles.stateCell}>
        <MailState message={m} />
        {m.status !== 'SENT' && m.detail && <span className={styles.note}>{m.detail}</span>}
      </div>
    ),
  },
];

export default function AutomationPage() {
  const [status, setStatus] = useState(null);
  const [outbox, setOutbox] = useState([]);
  const [error, setError] = useState(null);
  const [polling, setPolling] = useState(false);
  const [pollResult, setPollResult] = useState(null);

  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const [s, o] = await Promise.all([fetchAutomationStatus(), fetchOutbox(25)]);
        if (!alive) return;
        setStatus(s);
        setOutbox(o.messages || []);
        setError(null);
      } catch (err) {
        if (alive) setError(err.message || 'Could not reach the system.');
      }
    }
    tick();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  async function checkMailbox() {
    setPolling(true);
    setPollResult(null);
    try {
      const res = await pollMailboxNow();
      setPollResult(res);
      reload();
    } catch (err) {
      setPollResult({ ok: false, detail: err.message });
    } finally {
      setPolling(false);
    }
  }

  if (error && !status) {
    return (
      <Page>
        <Notice tone="danger" title="The system is not responding" action={<Button onClick={reload}>Try again</Button>}>
          {error}
        </Notice>
      </Page>
    );
  }

  if (!status) {
    return (
      <Page>
        <StatGrid>
          {[0, 1, 2].map((i) => (
            <div key={i} className={styles.statSkeleton}><Skeleton lines={2} /></div>
          ))}
        </StatGrid>
        <Card><Skeleton lines={5} /></Card>
      </Page>
    );
  }

  const liveKeys = new Set(status.links.filter((l) => l.live).map((l) => l.key));
  const doneForYou = HAND_WORK.filter((h) => liveKeys.has(h.key));
  const stillYours = HAND_WORK.filter((h) => !liveKeys.has(h.key));
  const removedPct = status.busy_work_removed_pct;
  const held = outbox.filter((m) => m.status !== 'SENT').length;
  const atFloor = status.hand_minutes_remaining <= status.hand_minutes_if_all_connected;

  return (
    <Page>
      {error && <Notice tone="warning">Could not refresh: {error}</Notice>}

      <StatGrid>
        <Stat
          label="Minutes by hand, per order"
          value={status.hand_minutes_remaining}
          hint={`Was about ${status.baseline_minutes_per_order} min`}
        />
        <Stat label="Busy work automated" value={`${removedPct}%`} />
        <Stat
          label="Connections live"
          value={`${status.links_live} of ${status.links_total}`}
          tone={status.links_live === status.links_total ? 'success' : undefined}
        />
      </StatGrid>

      <Card>
        <div className={styles.progressTop}>
          <strong>{removedPct}% of the busy work is automatic</strong>
          <span className={styles.note}>
            {atFloor
              ? 'This is as low as it goes. A person still approves each order.'
              : `Drops to ${status.hand_minutes_if_all_connected} min once every connection is live.`}
          </span>
        </div>
        <div className={styles.bar} role="img" aria-label={`${removedPct}% of the busy work is automatic`}>
          <div className={styles.barFill} style={{ width: `${Math.min(100, removedPct)}%` }} />
        </div>
      </Card>

      <div className={styles.twoUp}>
        <Card title="Done for you" description="No action needed." actions={<Badge tone="success">Automatic</Badge>}>
          <ul className={styles.list}>
            {ALWAYS_DONE.map((d) => (
              <li key={d.task} className={styles.item}>
                <span className={styles.tick} aria-hidden="true">✓</span>
                <div>
                  <div className={styles.task}>{d.task}</div>
                  {d.note && <div className={styles.note}>{d.note}</div>}
                </div>
              </li>
            ))}
            {doneForYou.map((h) => (
              <li key={h.key} className={styles.item}>
                <span className={styles.tick} aria-hidden="true">✓</span>
                <div>
                  <div className={styles.task}>{h.done}</div>
                  <div className={styles.note}>Saves {h.minutes} min an order</div>
                </div>
              </li>
            ))}
          </ul>
        </Card>

        <Card
          title="Still done by hand"
          description="What a person does on each order today."
          actions={<Badge tone="warning">{status.hand_minutes_remaining} min</Badge>}
        >
          <ul className={styles.list}>
            {stillYours.map((h) => {
              const link = status.links.find((l) => l.key === h.key);
              return (
                <li key={h.key} className={styles.item}>
                  <span className={styles.mins}>{h.minutes}m</span>
                  <div>
                    <div className={styles.task}>{h.task}</div>
                    <div className={styles.note}>
                      Goes away once connected{link?.needs ? `. Needs ${lowerArticle(link.needs)}.` : '.'}
                    </div>
                  </div>
                </li>
              );
            })}
            <li className={styles.item}>
              <span className={styles.mins}>{REVIEW.minutes}m</span>
              <div>
                <div className={styles.task}>{REVIEW.task}</div>
                <div className={styles.note}>This one stays. The system recommends; a person decides.</div>
              </div>
            </li>
          </ul>
        </Card>
      </div>

      <Columns>
        <Card
          title="Mail the system has written"
          description={
            status.mail.send_enabled
              ? 'Messages are being delivered.'
              : 'Messages are written and held here. Nothing has been sent.'
          }
          actions={
            <>
              {held > 0 && <Badge tone="warning">{held} held</Badge>}
              <Button
                size="sm"
                onClick={checkMailbox}
                loading={polling}
                disabled={!status.mailbox.enabled}
                title={status.mailbox.enabled ? 'Read the order mailbox now' : status.mailbox.detail}
              >
                {polling ? 'Checking…' : 'Check mailbox now'}
              </Button>
            </>
          }
          flush
        >
          {pollResult && (
            <div className={styles.pollNote}>
              <Notice tone={pollResult.ok ? 'success' : 'warning'}>
                {pollResult.detail}
                {pollResult.ok ? ` ${pollResult.ingested} message(s) read, ${pollResult.orders_created} order(s) created.` : ''}
              </Notice>
            </div>
          )}
          {outbox.length === 0 ? (
            <EmptyState title="Nothing written yet" description="Disputes and purchase orders will appear here." />
          ) : (
            <DataTable columns={OUTBOX_COLUMNS} rows={outbox} rowKey={(m) => m.message_id} />
          )}
        </Card>

        <Card title="Connections" description="What is wired up, and what each one is waiting for.">
          <ul className={styles.links}>
            {status.links.map((l) => (
              <li key={l.key} className={styles.link}>
                <div className={styles.linkTop}>
                  <span className={styles.linkName}>{l.name}</span>
                  <Badge tone={l.live ? 'success' : 'neutral'}>{l.live ? 'Live' : 'Not connected'}</Badge>
                </div>
                <p className={styles.note}>{l.detail}</p>
                {!l.live && l.needs && <p className={styles.needs}>Needs: {l.needs}</p>}
              </li>
            ))}
          </ul>
        </Card>
      </Columns>

      <p className={styles.footnote}>
        The two-hour starting figure is an estimate of Allied&rsquo;s hand-work, not a measurement. Every other
        figure here is worked out from it. See <Link href="/health">System health</Link> for the technical side.
      </p>
    </Page>
  );
}
