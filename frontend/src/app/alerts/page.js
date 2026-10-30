'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Badge, Button, Card, Columns, DataTable, DetailList, EmptyState, Field, Input, Notice, Page, Select,
  Skeleton, Tabs, Toolbar, dateTime,
} from '@/components/ui';
import {
  acknowledgeAlert, fetchAlerts, fetchContacts, fetchSummary, runChecksNow, saveContact,
  sendSummaryNow, sendTestAlert,
} from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import styles from './alerts.module.css';

const SEVERITY = {
  critical: ['danger', 'Urgent'],
  warning: ['warning', 'Behind'],
  info: ['info', 'FYI'],
};

const CHANNEL_LABEL = { email: 'Email', push: 'Phone push', sms: 'Text', _team_chat: 'Team chat', _push: 'Phone push' };

/** Whole-alert delivery keys ('_team_chat', '_push') get a label; anything else is a person's email. */
const DELIVERY_KEY_LABEL = { _team_chat: 'Team chat', _push: 'Phone push' };
const deliveryWho = (key) => DELIVERY_KEY_LABEL[key] || key;

/** Only same-site paths: '//host' and '/\host' would leave the site. */
function isInternalLink(link) {
  return typeof link === 'string' && link.startsWith('/') && !link.startsWith('//') && !link.startsWith('/\\');
}

const CHECK_LABEL = {
  due_soon: 'due soon',
  overdue: 'overdue',
  escalated: 'passed up to a manager',
  review_waiting: 'waiting for review',
  project_overdue: 'projects behind',
  shipments: 'shipments',
};

const ROLE_TAGS = [
  { value: 'leadership', label: 'Leadership' },
  { value: 'engineer', label: 'Engineer' },
  { value: 'operations', label: 'Operations' },
  { value: 'staff', label: 'Staff' },
];

const CHANNELS = [
  { value: 'email', label: 'Email' },
  { value: 'push', label: 'Phone push' },
  { value: 'sms', label: 'Text message' },
];

const E164 = /^\+[1-9]\d{6,14}$/;

/** Runs an async call and tracks busy / error / result for one action. */
function useAction(fn) {
  const [state, setState] = useState({ busy: false, error: '', data: null });
  async function run(...args) {
    setState({ busy: true, error: '', data: null });
    try {
      const data = await fn(...args);
      setState({ busy: false, error: '', data });
    } catch (e) {
      setState({ busy: false, error: e.message, data: null });
    }
  }
  return [state, run];
}

/** A delivery result can be a plain string or { channel: result }. */
function deliveryText(value) {
  if (value == null) return '–';
  if (typeof value !== 'object') return String(value);
  const parts = Object.entries(value).map(([ch, v]) => `${CHANNEL_LABEL[ch] || ch}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);
  return parts.join(' · ') || '–';
}

export default function AlertsPage() {
  const { hasRole } = useAuth();
  const isManager = hasRole?.('MANAGER');
  const isOperator = hasRole?.('OPERATOR');
  const [tab, setTab] = useState('mine');
  const [openCount, setOpenCount] = useState(null);
  const [channels, setChannels] = useState(null);
  const [test, runTest] = useAction(sendTestAlert);
  const [checks, runChecks] = useAction(runChecksNow);
  const [refresh, setRefresh] = useState(0);

  async function after(run, ...args) {
    await run(...args);
    setRefresh((n) => n + 1);
  }

  const tabs = [
    { key: 'mine', label: 'My alerts', count: openCount || undefined },
    { key: 'summary', label: 'Daily summary' },
  ];
  if (isManager) tabs.push({ key: 'who', label: 'Who gets told' });

  return (
    <Page
      actions={(
        <>
          <Button loading={test.busy} onClick={() => after(runTest, 'warning')}>Send me a test alert</Button>
          {isOperator && <Button loading={checks.busy} onClick={() => after(runChecks)}>Check schedules now</Button>}
        </>
      )}
    >
      {test.error && <Notice tone="danger" title="Test alert failed">{test.error}</Notice>}
      {test.data && <TestResult data={test.data} />}
      {checks.error && <Notice tone="danger" title="Could not run the checks">{checks.error}</Notice>}
      {checks.data && <CheckResult counts={checks.data} />}

      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === 'mine' && (
        <MyAlerts
          isManager={isManager}
          refresh={refresh}
          onLoaded={(res) => { setOpenCount(res.open); setChannels(res.channels); }}
        />
      )}
      {tab === 'summary' && <DailySummary isManager={isManager} channels={channels} />}
      {tab === 'who' && isManager && <WhoGetsTold />}
    </Page>
  );
}

function TestResult({ data }) {
  if (!data.id || !data.delivered) {
    return (
      <Notice tone="warning" title="No test alert was sent">
        You may not be set up to receive alerts yet. Ask an admin to add you under Who gets told.
      </Notice>
    );
  }
  const entries = Object.entries(data.delivered);
  return (
    <Notice tone="success" title="Test alert sent">
      {entries.length === 0
        ? 'It is in your alerts below. No email, push or text was sent.'
        : entries.map(([who, v]) => `${deliveryWho(who)}: ${deliveryText(v)}`).join('  |  ')}
    </Notice>
  );
}

function CheckResult({ counts }) {
  const parts = Object.entries(counts).filter(([, n]) => n > 0);
  const total = parts.reduce((sum, [, n]) => sum + n, 0);
  return (
    <Notice tone={total ? 'info' : 'success'} title="Schedules checked">
      {total === 0
        ? 'No new alerts were needed. People are not told twice about the same thing.'
        : `Sent ${total} new alert${total === 1 ? '' : 's'}: ${parts.map(([k, n]) => `${n} ${CHECK_LABEL[k] || k}`).join(', ')}.`}
    </Notice>
  );
}

/* ------------------------------------------------------------ my alerts */

function MyAlerts({ isManager, refresh, onLoaded }) {
  const router = useRouter();
  const [show, setShow] = useState('open');
  const [everyone, setEveryone] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(null);
  const [ackError, setAckError] = useState(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetchAlerts({ openOnly: show === 'open', everyone: everyone && isManager });
        if (!alive) return;
        const sorted = [...(res.items || [])].sort((a, b) => {
          if (!a.acknowledged_at !== !b.acknowledged_at) return a.acknowledged_at ? 1 : -1;
          return String(b.created_at).localeCompare(String(a.created_at));
        });
        setItems(sorted);
        setError('');
        onLoaded?.(res);
      } catch (e) {
        if (alive) setError(e.message);
      }
    }
    load();
    return () => {
      alive = false;
    };
    // onLoaded is a fresh function each render; reload only on filter changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [show, everyone, isManager, reloadKey, refresh]);

  async function handle(id) {
    setBusy(id);
    setAckError(null);
    try {
      await acknowledgeAlert(id);
      setReloadKey((k) => k + 1);
    } catch (e) {
      // Shown on the alert itself (e.g. 403 "sent to someone else", 404 gone),
      // kept apart from load errors so a reload doesn't wipe it.
      setAckError({ id, text: e.message || 'Request failed' });    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={everyone ? "Everyone's alerts" : 'My alerts'}
      description="Mark an alert handled once it is dealt with, so nobody chases it again."
      actions={(
        <Toolbar className={styles.filters}>
          <Select
            aria-label="Which alerts"
            value={show}
            onChange={(e) => setShow(e.target.value)}
            options={[{ value: 'open', label: 'Open' }, { value: 'all', label: 'All' }]}
          />
          {isManager && (
            <label className={styles.check}>
              <input type="checkbox" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
              Everyone&apos;s
            </label>
          )}
        </Toolbar>
      )}
    >
      {error && <Notice tone="danger" action={<Button size="sm" onClick={() => setReloadKey((k) => k + 1)}>Try again</Button>}>{error}</Notice>}
      {!items && !error ? (
        <Skeleton lines={4} />
      ) : !items ? null : items.length === 0 ? (
        <EmptyState
          title={show === 'open' ? 'Nothing open. You are all caught up.' : 'No alerts yet.'}
          description="Alerts appear here when a deadline is close or passed, or a shipment needs attention."
        />
      ) : (
        <ul className={styles.list}>
          {items.map((a) => {
            const [tone, label] = SEVERITY[a.severity] || ['neutral', a.severity];
            const handled = Boolean(a.acknowledged_at);
            const deliveries = Object.entries(a.deliveries || {});
            return (
              <li key={a.id} className={handled ? `${styles.item} ${styles.itemDone}` : styles.item}>
                <div className={styles.itemHead}>
                  <Badge tone={handled ? 'neutral' : tone}>{label}</Badge>
                  <span className={styles.title}>{a.title}</span>
                </div>
                {a.body && <p className={styles.body}>{a.body}</p>}
                <span className={styles.meta}>
                  {dateTime(a.created_at)}
                  {everyone && a.recipients?.length > 0 && ` · to ${a.recipients.join(', ')}`}
                  {handled && ` · handled ${dateTime(a.acknowledged_at)}${a.acknowledged_by ? ` by ${a.acknowledged_by}` : ''}`}
                </span>
                {deliveries.length > 0 && (
                  <details className={styles.delivery}>
                    <summary>How it was sent</summary>
                    <ul>
                      {deliveries.map(([who, v]) => (
                        <li key={who}><strong>{deliveryWho(who)}</strong> {deliveryText(v)}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {ackError?.id === a.id && (
                  <Notice tone="danger" title="Could not mark it handled">{ackError.text}</Notice>
                )}
                {(isInternalLink(a.link) || !handled) && (
                  <Toolbar>
                    {isInternalLink(a.link) && <Button size="sm" onClick={() => router.push(a.link)}>Open</Button>}
                    {!handled && (
                      <Button size="sm" variant="primary" loading={busy === a.id} onClick={() => handle(a.id)}>Mark handled</Button>
                    )}
                  </Toolbar>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/* -------------------------------------------------------- daily summary */

const SUMMARY_SECTIONS = [
  ['behind', 'Behind schedule', 'danger'],
  ['shipments', 'Shipments needing attention', 'warning'],
  ['review', 'Design documents waiting for review', 'info'],
  ['due_week', 'Due this week', 'neutral'],
];

function DailySummary({ isManager, channels }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [sent, send] = useAction(sendSummaryNow);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetchSummary();
        if (!alive) return;
        setData(res);
        setError('');
      } catch (e) {
        if (alive) setError(e.message);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const hour = channels?.daily_summary_hour;
  const when = hour != null ? `every weekday at ${String(hour).padStart(2, '0')}:00` : 'every weekday morning';
  const nothing = data && SUMMARY_SECTIONS.every(([key]) => !data[key]?.length) && !data.urgent_open;

  return (
    <Card
      title="Today's summary"
      description={`What leadership receives by email ${when}. This is how it looks right now.`}
      actions={isManager && (
        <Button variant="primary" loading={sent.busy} disabled={!data} onClick={() => send()}>Send it now</Button>
      )}
    >
      <div className={styles.stack}>
        {sent.error && <Notice tone="danger" title="Could not send the summary">{sent.error}</Notice>}
        {sent.data && (
          sent.data.id
            ? <Notice tone="success">Summary sent. It is also in each recipient&apos;s alerts.</Notice>
            : <Notice tone="warning">Nothing was sent. No one is set to receive the summary; add leadership under Who gets told.</Notice>
        )}
        {error && <Notice tone="danger" action={<Button size="sm" onClick={() => setReloadKey((k) => k + 1)}>Try again</Button>}>{error}</Notice>}
        {!data && !error && <Skeleton lines={6} />}
        {data && data.urgent_open > 0 && (
          <Notice tone="danger" title={`${data.urgent_open} urgent alert${data.urgent_open === 1 ? '' : 's'} not yet handled`}>
            Someone still needs to mark {data.urgent_open === 1 ? 'it' : 'them'} handled in My alerts.
          </Notice>
        )}
        {nothing && <EmptyState title="Nothing is behind schedule." description="Nothing needs leadership today." />}
        {data && SUMMARY_SECTIONS.map(([key, title, tone]) => (
          data[key]?.length > 0 && (
            <section key={key} className={styles.section}>
              <div className={styles.sectionHead}>
                <h3 className={styles.sectionTitle}>{title}</h3>
                <Badge tone={tone}>{data[key].length}</Badge>
              </div>
              <ul className={styles.bullets}>
                {data[key].map((line, i) => <li key={i}>{line}</li>)}
              </ul>
            </section>
          )
        ))}
        {data?.text && (
          <details className={styles.delivery}>
            <summary>See the email text</summary>
            <pre className={styles.pre}>{data.text}</pre>
          </details>
        )}
      </div>
    </Card>
  );
}

/* --------------------------------------------------------- who gets told */

const EMPTY_CONTACT = { email: '', name: '', role_tag: 'staff', phone: '', channels: ['email'], daily_summary: false, active: true };

function onOff(on, onText = 'On', offText = 'Off') {
  return <Badge tone={on ? 'success' : 'neutral'}>{on ? onText : offText}</Badge>;
}

function emailBadge(mode) {
  if (mode === 'LIVE') return onOff(true);
  if (mode === 'REDIRECTED') return <Badge tone="warning">Test mode</Badge>;
  return <Badge tone="neutral">Off (drafts only)</Badge>;
}

function WhoGetsTold() {
  const { hasRole } = useAuth();
  const isAdmin = hasRole?.('ADMIN');
  const [reloadKey, setReloadKey] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [form, setForm] = useState(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetchContacts();
        if (!alive) return;
        setData(res);
        setError('');
      } catch (e) {
        if (alive) setError(e.message);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  const ch = data?.channels;
  const contacts = [...(data?.contacts || [])].sort((a, b) => String(a.name).localeCompare(String(b.name)));

  const columns = [
    {
      key: 'name',
      label: 'Person',
      render: (c) => (
        <div className={styles.person}>
          <strong>{c.name || '–'}</strong>
          <span className={styles.meta}>{c.email}</span>
        </div>
      ),
    },
    { key: 'role_tag', label: 'Group', render: (c) => ROLE_TAGS.find((r) => r.value === c.role_tag)?.label || c.role_tag },
    { key: 'phone', label: 'Phone', render: (c) => c.phone || '–' },
    {
      key: 'channels',
      label: 'Told by',
      render: (c) => (c.channels || []).map((x) => CHANNELS.find((o) => o.value === x)?.label || x).join(', ') || '–',
    },
    { key: 'daily_summary', label: 'Summary', render: (c) => (c.daily_summary ? 'Yes' : 'No') },
    { key: 'active', label: 'Active', render: (c) => onOff(c.active, 'Yes', 'Paused') },
  ];
  if (isAdmin) {
    columns.push({
      key: 'edit',
      label: '',
      align: 'right',
      render: (c) => (
        <Button size="sm" variant="ghost" onClick={() => setForm({ ...EMPTY_CONTACT, ...c, phone: c.phone || '', channels: c.channels || ['email'] })}>
          Edit
        </Button>
      ),
    });
  }

  return (
    <Columns>
      <div>
        <Card title="How alerts reach people">
          <ul className={styles.bullets}>
            <li><strong>Email</strong> for every alert.</li>
            <li><strong>Phone push</strong> for urgent alerts and for work that is behind.</li>
            <li><strong>Text messages</strong> only for urgent alerts.</li>
            <li>Pushes and texts wait during quiet hours and weekends, unless the alert is urgent.</li>
          </ul>
        </Card>
        <Card
          title="People"
          description="Who gets told, and how."
          flush
          actions={isAdmin && !form && <Button size="sm" variant="primary" onClick={() => setForm(EMPTY_CONTACT)}>Add person</Button>}
        >
          {error && (
            <div className={styles.pad}>
              <Notice tone="danger" action={<Button size="sm" onClick={() => setReloadKey((k) => k + 1)}>Try again</Button>}>{error}</Notice>
            </div>
          )}
          {!data && !error ? (
            <div className={styles.pad}><Skeleton lines={4} /></div>
          ) : data ? (
            <DataTable rows={contacts} rowKey={(c) => c.email} empty="No one is set up yet. Alerts go to each job's owner by email." columns={columns} />
          ) : null}
        </Card>
        {isAdmin && form && (
          <ContactForm
            key={form.email || 'new'}
            initial={form}
            onCancel={() => setForm(null)}
            onSaved={() => { setForm(null); setReloadKey((k) => k + 1); }}
          />
        )}
      </div>
      <div>
        <Card title="Channels">
          {!ch ? (
            error ? <EmptyState title="Channel status is not available." /> : <Skeleton lines={5} />
          ) : (
            <div className={styles.stack}>
              <ul className={styles.channels}>
                <li>
                  <div className={styles.channelHead}><span>Email</span>{emailBadge(ch.email?.mode)}</div>
                  {ch.email?.detail && <span className={styles.meta}>{ch.email.detail}</span>}
                </li>
                <li>
                  <div className={styles.channelHead}><span>Phone push</span>{onOff(ch.push?.on)}</div>
                  {ch.push?.detail && <span className={styles.meta}>{ch.push.detail}</span>}
                </li>
                <li>
                  <div className={styles.channelHead}><span>Text messages</span>{onOff(ch.sms?.on)}</div>
                  {ch.sms?.detail && <span className={styles.meta}>{ch.sms.detail}</span>}
                </li>
                <li>
                  <div className={styles.channelHead}><span>Team chat</span>{onOff(ch.team_chat?.on)}</div>
                </li>
              </ul>
              <DetailList items={[
                ['Leadership', ch.leadership?.length ? ch.leadership.join(', ') : 'No one yet'],
                ['Quiet hours', ch.quiet_hours],
                ['Daily summary', ch.daily_summary_hour != null ? `${String(ch.daily_summary_hour).padStart(2, '0')}:00 on weekdays` : '–'],
              ]} />
            </div>
          )}
        </Card>
      </div>
    </Columns>
  );
}

function ContactForm({ initial, onCancel, onSaved }) {
  const [form, setForm] = useState(initial);
  const [state, setState] = useState({ busy: false, error: '' });
  const editing = Boolean(initial.email);

  function toggleChannel(value, on) {
    const next = on ? [...new Set([...form.channels, value])] : form.channels.filter((c) => c !== value);
    setForm({ ...form, channels: next });
  }

  async function submit(e) {
    e.preventDefault();
    const email = form.email.trim();
    const phone = form.phone.trim().replace(/[\s()-]/g, '');
    let problem = '';
    if (!email || !email.includes('@')) problem = 'Enter an email address.';
    else if (!form.name.trim()) problem = 'Enter a name.';
    else if (phone && !E164.test(phone)) problem = 'Write the phone number with + and the country code, like +12055550100.';
    else if (form.channels.includes('sms') && !phone) problem = 'Text messages need a phone number.';
    else if (form.channels.length === 0) problem = 'Pick at least one way to tell them.';
    if (problem) {
      setState({ busy: false, error: problem });
      return;
    }
    setState({ busy: true, error: '' });
    try {
      await saveContact({
        email,
        name: form.name.trim(),
        role_tag: form.role_tag,
        phone: phone || null,
        channels: form.channels,
        daily_summary: form.daily_summary,
        active: form.active,
      });
      onSaved();
    } catch (err) {
      setState({ busy: false, error: err.message });
    }
  }

  return (
    <Card title={editing ? `Edit ${initial.name || initial.email}` : 'Add a person'}>
      <form className={styles.stack} onSubmit={submit}>
        <div className={styles.pair}>
          <Field label="Email">
            <Input type="email" value={form.email} disabled={editing} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field label="Name">
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
        </div>
        <div className={styles.pair}>
          <Field label="Group">
            <Select value={form.role_tag} onChange={(e) => setForm({ ...form, role_tag: e.target.value })} options={ROLE_TAGS} />
          </Field>
          <Field label="Mobile phone" hint="With + and country code, like +12055550100.">
            <Input type="tel" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+12055550100" />
          </Field>
        </div>
        <fieldset className={styles.fieldset}>
          <legend className={styles.legend}>Tell them by</legend>
          <div className={styles.checks}>
            {CHANNELS.map((c) => (
              <label key={c.value} className={styles.check}>
                <input type="checkbox" checked={form.channels.includes(c.value)} onChange={(e) => toggleChannel(c.value, e.target.checked)} />
                {c.label}
              </label>
            ))}
          </div>
        </fieldset>
        <div className={styles.checks}>
          <label className={styles.check}>
            <input type="checkbox" checked={form.daily_summary} onChange={(e) => setForm({ ...form, daily_summary: e.target.checked })} />
            Gets the daily summary
          </label>
          <label className={styles.check}>
            <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
            Active (untick to pause all alerts)
          </label>
        </div>
        {state.error && <Notice tone="danger">{state.error}</Notice>}
        <Toolbar>
          <Button type="submit" variant="primary" loading={state.busy}>Save</Button>
          <Button variant="ghost" disabled={state.busy} onClick={onCancel}>Cancel</Button>
        </Toolbar>
      </form>
    </Card>
  );
}
