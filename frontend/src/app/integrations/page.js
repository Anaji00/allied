'use client';

import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { fetchMondayEvents, fetchMondaySetup, replayMondayEvent, simulateMondayStatus } from '@/lib/api';
import styles from './integrations.module.css';

const FILTERS = [
  { key: '', label: 'All' },
  { key: 'FAILED', label: 'Needs a person' },
  { key: 'DEAD', label: 'Gave up' },
  { key: 'RETRY', label: 'Retrying' },
  { key: 'DONE', label: 'Done' },
  { key: 'IGNORED', label: 'Ignored' },
];

const STATUS_TEXT = {
  PENDING: 'Waiting',
  PROCESSING: 'Working',
  RETRY: 'Retrying',
  DONE: 'Done',
  IGNORED: 'Ignored',
  FAILED: 'Needs a person',
  DEAD: 'Gave up',
};

const ACTION_TEXT = {
  CREATE_ORDER: 'Create order',
  RELEASE_PO: 'Release POs',
  DISPATCH: 'Dispatch',
};

export default function IntegrationsPage() {
  const { hasRole } = useAuth();
  const canAct = hasRole?.('OPERATOR');
  const [filter, setFilter] = useState('');
  const [data, setData] = useState(null);
  const [setup, setSetup] = useState(null);
  // Poll errors are per panel and replaced by every poll; action errors
  // (replay, test event) stay until the next action so the poll can't hide them.
  const [pollErrors, setPollErrors] = useState({ events: '', setup: '' });
  const [actionMsg, setActionMsg] = useState(null);
  const [busy, setBusy] = useState(null);
  // label '' means "the first status the board routes".
  const [test, setTest] = useState({ item: '', label: '', name: '' });

  // Bumped after an action so the list refreshes without waiting for the timer.
  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    async function tick() {
      // Loaded independently: one failing call must not hide the other panel.
      const [events, cfg] = await Promise.allSettled([
        fetchMondayEvents({ limit: 100, status: filter }),
        fetchMondaySetup(),
      ]);
      if (!alive) return;
      if (events.status === 'fulfilled') setData(events.value);
      if (cfg.status === 'fulfilled') setSetup(cfg.value);
      setPollErrors({
        events: events.status === 'rejected' ? events.reason?.message || 'Could not load events' : '',
        setup: cfg.status === 'rejected' ? cfg.reason?.message || 'Could not load setup' : '',
      });
    }
    tick();
    const t = setInterval(tick, 10000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [filter, reloadKey]);

  async function replay(id) {
    setBusy(id);
    setActionMsg(null);
    try {
      await replayMondayEvent(id);
      setActionMsg({ ok: true, text: `Event #${id} replayed.` });
      load();
    } catch (e) {
      setActionMsg({ ok: false, text: e.message });
    } finally {
      setBusy(null);
    }
  }

  async function runTest(e) {
    e.preventDefault();
    if (!test.item) return;
    setBusy('test');
    setActionMsg(null);
    try {
      await simulateMondayStatus(test.item, testLabel, test.name || undefined);
      setActionMsg({ ok: true, text: `Test event sent: item ${test.item} → “${testLabel}”. It appears in the list below.` });
      load();
    } catch (err) {
      setActionMsg({ ok: false, text: err.message });
    } finally {
      setBusy(null);
    }
  }

  const stats = data?.stats || {};
  const labels = setup?.routing
    ? [...setup.routing.create_order_labels, ...setup.routing.release_po_labels, ...setup.routing.dispatch_labels]
    : [];
  const labelOptions = labels.length ? labels : ['ready for sage'];
  // Always one of the dropdown's options, so what is sent is what is shown.
  const testLabel = labelOptions.includes(test.label) ? test.label : labelOptions[0];

  return (
    <div className={styles.page}>
      {actionMsg && (
        <div className={actionMsg.ok ? styles.success : styles.error} role={actionMsg.ok ? 'status' : 'alert'}>
          {actionMsg.text}
        </div>
      )}

      <section className={styles.stats}>
        <Stat label="Needs attention" value={stats.needs_attention} tone={stats.needs_attention ? 'bad' : 'ok'} />
        <Stat label="Retrying" value={stats.RETRY} tone={stats.RETRY ? 'warn' : undefined} />
        <Stat label="Done" value={stats.DONE} />
        <Stat label="Ignored" value={stats.IGNORED} hint="Changes that are not triggers" />
      </section>

      <div className={styles.columns}>
        <section className={styles.card}>
          <div className={styles.cardHead}>
            <h2>Events from the board</h2>
            <div className={styles.filters} role="tablist">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  role="tab"
                  aria-selected={filter === f.key}
                  className={`${styles.filter} ${filter === f.key ? styles.filterOn : ''}`}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {pollErrors.events && (
            <div className={styles.error} role="alert">
              Could not load events: {pollErrors.events}
              {data ? ' Showing the last list loaded.' : ''} It retries every 10 seconds.
            </div>
          )}
          {!data ? (
            !pollErrors.events && <p className={styles.empty}>Loading…</p>
          ) : data.events.length === 0 ? (
            <p className={styles.empty}>No events yet. Once the webhook is registered, every board change shows up here.</p>
          ) : (
            <ul className={styles.list}>
              {data.events.map((ev) => (
                <li key={ev.id} className={styles.row}>
                  <span className={`${styles.badge} ${styles[`s${ev.status}`] || ''}`}>{STATUS_TEXT[ev.status] || ev.status}</span>
                  <div className={styles.rowMain}>
                    <div className={styles.rowTitle}>
                      {ev.item_name || `Item ${ev.item_id || '?'}`}
                      {ev.label && <span className={styles.label}>→ {ev.label}</span>}
                    </div>
                    <div className={styles.rowMeta}>
                      {ACTION_TEXT[ev.action] || ev.event_type} · #{ev.id} · {fmt(ev.received_at)}
                      {ev.attempts > 1 && ` · ${ev.attempts} attempts`}
                      {ev.status === 'RETRY' && ev.next_attempt_at && ` · next try ${fmt(ev.next_attempt_at)}`}
                    </div>
                    {ev.last_error && <div className={styles.rowError}>{ev.last_error}</div>}
                    {!ev.last_error && ev.result?.sales_order_number && (
                      <div className={styles.rowOk}>Sales order {ev.result.sales_order_number}</div>
                    )}
                    {!ev.last_error && ev.result?.reason && <div className={styles.rowMeta}>{ev.result.reason}</div>}
                    {!ev.last_error && ev.result?.skipped && <div className={styles.rowMeta}>{ev.result.skipped}</div>}
                  </div>
                  {canAct && ['FAILED', 'DEAD'].includes(ev.status) && (
                    <button type="button" className="btnSecondary" disabled={busy === ev.id} onClick={() => replay(ev.id)}>
                      {busy === ev.id ? 'Replaying…' : 'Replay'}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <aside className={styles.side}>
          <section className={styles.card}>
            <h2>Setup</h2>
            {pollErrors.setup && (
              <div className={styles.error} role="alert">
                Could not load setup: {pollErrors.setup}
              </div>
            )}
            {setup ? (
              <ul className={styles.checks}>
                {setup.checks.map((c) => (
                  <li key={c.key} className={styles.check}>
                    <span className={c.ok ? styles.tick : styles.cross} aria-hidden="true">{c.ok ? '✓' : '•'}</span>
                    <div>
                      <div className={styles.checkLabel}>{c.label}</div>
                      {!c.ok && <div className={styles.checkHint}>{c.hint}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              !pollErrors.setup && <p className={styles.empty}>Loading…</p>
            )}
            {setup && (
              <p className={styles.small}>
                Trigger column <code>{setup.routing.status_column}</code> · API {setup.api_version} · retry worker{' '}
                {setup.worker_running ? 'running' : 'stopped'}
              </p>
            )}
          </section>

          {canAct && (
            <section className={styles.card}>
              <h2>Send a test event</h2>
              <p className={styles.small}>Runs through the same path as a real board change.</p>
              <form className={styles.form} onSubmit={runTest}>
                <label>
                  Item id
                  <input className="formInput" inputMode="numeric" value={test.item} onChange={(e) => setTest({ ...test, item: e.target.value.replace(/\D/g, '') })} />
                </label>
                <label>
                  Item name <span className={styles.optional}>Customer - PO - Item</span>
                  <input className="formInput" value={test.name} onChange={(e) => setTest({ ...test, name: e.target.value })} />
                </label>
                <label>
                  New status
                  <select className="formSelect" value={testLabel} onChange={(e) => setTest({ ...test, label: e.target.value })}>
                    {labelOptions.map((l) => (
                      <option key={l} value={l}>{l}</option>
                    ))}
                  </select>
                </label>
                <button type="submit" className="btnPrimary" disabled={!test.item || busy === 'test'}>
                  {busy === 'test' ? 'Sending…' : 'Send test'}
                </button>
              </form>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}

function Stat({ label, value, tone, hint }) {
  return (
    <div className={`${styles.stat} ${tone ? styles[tone] : ''}`} title={hint}>
      <span className={styles.statValue}>{value ?? '–'}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  );
}

function fmt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
