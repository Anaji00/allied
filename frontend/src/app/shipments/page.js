'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Badge, Button, Card, Columns, DetailList, EmptyState, Field, Input, Notice, Page, Select,
  Skeleton, Stat, StatGrid, Tabs, Textarea, Toolbar, dateTime,
} from '@/components/ui';
import { addShipment, fetchShipments, readForwarderEmail, updateShipment } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import styles from './shipments.module.css';

const BEFORE_DEPARTURE = ['BOOKED', 'CARGO_READY', 'GATE_IN'];
const UNDER_WAY = ['DEPARTED', 'TRANSSHIPMENT'];
const FINISHED = ['GATE_OUT', 'DELIVERED', 'EMPTY_RETURNED'];

const STATUS_LABEL = {
  BOOKED: 'Booked',
  CARGO_READY: 'Ready to collect',
  GATE_IN: 'At origin port',
  DEPARTED: 'On the water / in the air',
  TRANSSHIPMENT: 'Changing ship or plane',
  ARRIVED: 'Arrived at destination',
  DISCHARGED: 'Unloaded at port',
  CUSTOMS_RELEASED: 'Cleared customs',
  GATE_OUT: 'Left the port',
  DELIVERED: 'Delivered',
  EMPTY_RETURNED: 'Finished (container returned)',
  EXCEPTION: 'Problem reported',
  UNKNOWN: 'Unclear',
};

const DEPARTED_BY_MODE = { SEA: 'On the water', AIR: 'In the air', TRUCK: 'On the road' };
const MODE_LABEL = { SEA: 'Sea', AIR: 'Air', TRUCK: 'Truck' };
const SOURCE_LABEL = { manual: 'Entered by hand', email: 'From forwarder email', api: 'From tracking' };

const DEFAULT_STATUSES = Object.keys(STATUS_LABEL).filter((s) => s !== 'UNKNOWN');

const WORKING = 'Reading the email… this uses the local AI and can take up to a minute.';

function statusLabel(status, mode) {
  if (status === 'DEPARTED' && DEPARTED_BY_MODE[mode]) return DEPARTED_BY_MODE[mode];
  return STATUS_LABEL[status] || status || '–';
}

function statusTone(status) {
  if (status === 'EXCEPTION') return 'danger';
  if (FINISHED.includes(status)) return 'success';
  if (UNDER_WAY.includes(status)) return 'info';
  return 'neutral';
}

function identifier(s) {
  return s.container_no || s.awb || s.reference || `Shipment ${s.id}`;
}

function todayIso() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function addDaysIso(iso, days) {
  const [y, m, d] = iso.split('-').map(Number);
  const dt = new Date(y, m - 1, d + days);
  const pad = (n) => String(n).padStart(2, '0');
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
}

/** YYYY-MM-DD shown as a short local date without shifting a day. */
function fmtDate(value) {
  if (!value) return '–';
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return dateTime(value);
  const d = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: sameYear ? undefined : 'numeric' });
}

function capitalise(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function flagTone(flag) {
  return flag === 'port free time ending' ? 'danger' : 'warning';
}

function isLate(s) {
  return (s.flags || []).includes('late');
}

const statusOptions = (statuses) => statuses.map((s) => ({ value: s, label: STATUS_LABEL[s] || s }));

export default function ShipmentsPage() {
  const { hasRole } = useAuth();
  const canEdit = hasRole?.('OPERATOR');
  const [includeFinished, setIncludeFinished] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [view, setView] = useState('attention');
  const [openId, setOpenId] = useState(null);
  const deepLink = useRef({ checked: false, scrollTo: null });

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetchShipments(includeFinished);
        if (!alive) return;
        setData(res);
        setError('');
        if (!deepLink.current.checked) {
          deepLink.current.checked = true;
          const id = Number(new URLSearchParams(window.location.search).get('id'));
          if (id) {
            const found = res.shipments.find((s) => s.id === id);
            if (found) {
              setOpenId(id);
              // Open the view that actually lists it: 'attention' and 'active'
              // both leave finished shipments out.
              if (FINISHED.includes(found.status)) setView('finished');
              else if (!found.flags?.length) setView('active');
              deepLink.current.scrollTo = id;
            } else if (!includeFinished) {
              // It may be finished; look again with finished shipments included.
              deepLink.current.checked = false;
              setIncludeFinished(true);
            }
          }
        }
      } catch (e) {
        if (alive) setError(e.message);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, [includeFinished, reloadKey]);

  useEffect(() => {
    const id = deepLink.current.scrollTo;
    if (id && openId === id) {
      deepLink.current.scrollTo = null;
      document.getElementById(`shipment-${id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }
  }, [openId, data]);

  const reload = () => setReloadKey((k) => k + 1);
  const statuses = data?.statuses?.length ? data.statuses : DEFAULT_STATUSES;
  const all = data?.shipments || [];
  const active = all.filter((s) => !FINISHED.includes(s.status));
  const attention = active.filter((s) => s.flags?.length);
  const today = todayIso();
  const weekEnd = addDaysIso(today, 7);
  const arriving = active.filter(
    (s) => s.eta_current && s.eta_current >= today && s.eta_current <= weekEnd
      && (BEFORE_DEPARTURE.includes(s.status) || UNDER_WAY.includes(s.status)),
  );
  const onTheWay = active.filter((s) => UNDER_WAY.includes(s.status));
  const lateCount = active.filter(isLate).length;

  const tabs = [
    { key: 'attention', label: 'Needs attention', count: data ? attention.length : undefined },
    { key: 'active', label: 'All active', count: data ? active.length : undefined },
  ];
  if (includeFinished) tabs.push({ key: 'finished', label: 'Finished', count: data ? all.length - active.length : undefined });
  const currentView = !includeFinished && view === 'finished' ? 'active' : view;
  const rows = currentView === 'attention' ? attention
    : currentView === 'finished' ? all.filter((s) => FINISHED.includes(s.status))
      : active;

  return (
    <Page>
      {error && (
        <Notice tone="danger" title="Could not load shipments" action={<Button size="sm" onClick={reload}>Try again</Button>}>
          {error}
        </Notice>
      )}

      <StatGrid>
        <Stat label="Needs attention" value={data ? attention.length : undefined} tone={attention.length ? 'warning' : undefined} hint="Late, slipping, quiet or near port charges" />
        <Stat label="Late" value={data ? lateCount : undefined} tone={lateCount ? 'danger' : undefined} hint="Past the expected arrival date" />
        <Stat label="Arriving this week" value={data ? arriving.length : undefined} hint="Expected in the next 7 days" />
        <Stat label="On the way" value={data ? onTheWay.length : undefined} tone="info" hint="Left the origin, not yet arrived" />
      </StatGrid>

      <Columns>
        <div>
          <Card
            title="Shipments"
            description="Tap a shipment to see its history and record a change."
            actions={(
              <label className={styles.check}>
                <input type="checkbox" checked={includeFinished} onChange={(e) => setIncludeFinished(e.target.checked)} />
                Show finished
              </label>
            )}
          >
            <div className={styles.stack}>
              <Tabs tabs={tabs} value={currentView} onChange={setView} />
              {!data && !error ? (
                <Skeleton lines={5} />
              ) : !data ? (
                <EmptyState title="Shipments could not be loaded." description="Check the connection and try again." />
              ) : rows.length === 0 ? (
                <EmptyState
                  title={currentView === 'attention' ? 'Nothing needs attention.' : 'No shipments here yet.'}
                  description={currentView === 'attention'
                    ? 'Every active shipment is on schedule and up to date.'
                    : 'Add a shipment, or paste a forwarder email to update one.'}
                />
              ) : (
                <ul className={styles.list}>
                  {rows.map((s) => (
                    <ShipmentRow
                      key={s.id}
                      shipment={s}
                      open={openId === s.id}
                      onToggle={() => setOpenId(openId === s.id ? null : s.id)}
                      statuses={statuses}
                      canEdit={canEdit}
                      onChanged={reload}
                    />
                  ))}
                </ul>
              )}
            </div>
          </Card>
        </div>
        <div>
          {canEdit ? (
            <>
              <ForwarderEmailCard onApplied={(id) => { setOpenId(id); reload(); }} />
              <AddShipmentCard statuses={statuses} onAdded={(id) => { setView('active'); setOpenId(id); reload(); }} />
            </>
          ) : (
            <Card title="Making changes">
              <p className={styles.small}>You can see every shipment. Adding shipments and recording changes needs an operator account.</p>
            </Card>
          )}
        </div>
      </Columns>
    </Page>
  );
}

/* ------------------------------------------------------------------ row */

function ShipmentRow({ shipment: s, open, onToggle, statuses, canEdit, onChanged }) {
  const etaMoved = s.eta_original && s.eta_current && s.eta_original !== s.eta_current;
  return (
    <li id={`shipment-${s.id}`} className={styles.item}>
      <button type="button" className={styles.itemHead} onClick={onToggle} aria-expanded={open}>
        <span className={styles.itemTop}>
          <span className={styles.ident}>{identifier(s)}</span>
          <Badge tone={statusTone(s.status)}>{statusLabel(s.status, s.mode)}</Badge>
        </span>
        <span className={styles.route}>
          {s.origin || '?'} <span aria-hidden="true">&rarr;</span><span className={styles.srOnly}>to</span> {s.destination || '?'}
        </span>
        <span className={styles.meta}>
          {[s.forwarder || 'No forwarder', MODE_LABEL[s.mode] || s.mode, s.owner_email ? `Owner: ${s.owner_email}` : 'No owner'].filter(Boolean).join(' · ')}
        </span>
        <span className={styles.eta}>
          Arrives {fmtDate(s.eta_current)}
          {etaMoved && <span className={styles.was}> (first promised {fmtDate(s.eta_original)})</span>}
        </span>
        {s.flags?.length > 0 && (
          <span className={styles.flags}>
            {s.flags.map((f) => <Badge key={f} tone={flagTone(f)}>{capitalise(f)}</Badge>)}
          </span>
        )}
      </button>
      {open && <ShipmentDetails key={s.id} shipment={s} statuses={statuses} canEdit={canEdit} onChanged={onChanged} />}
    </li>
  );
}

/* -------------------------------------------------------------- details */

function ShipmentDetails({ shipment: s, statuses, canEdit, onChanged }) {
  const [form, setForm] = useState({ status: s.status, eta: '', free_time_ends: '', note: '' });
  const [state, setState] = useState({ busy: false, error: '', done: '' });
  const events = s.events || [];

  async function save(e) {
    e.preventDefault();
    const changes = {
      status: form.status !== s.status ? form.status : undefined,
      eta: form.eta || undefined,
      free_time_ends: form.free_time_ends || undefined,
      note: form.note.trim() || undefined,
    };
    if (!Object.values(changes).some(Boolean)) {
      setState({ busy: false, error: 'Change the status, a date or write a note first.', done: '' });
      return;
    }
    setState({ busy: true, error: '', done: '' });
    try {
      await updateShipment(s.id, changes);
      setState({ busy: false, error: '', done: 'Saved. The history below now includes this change.' });
      setForm({ status: changes.status || s.status, eta: '', free_time_ends: '', note: '' });
      onChanged();
    } catch (err) {
      setState({ busy: false, error: err.message, done: '' });
    }
  }

  return (
    <div className={styles.details}>
      <div className={styles.detailsCol}>
        <DetailList items={[
          ['Forwarder', s.forwarder],
          ['Carrier', s.carrier],
          ['Booking ref', s.reference],
          ['Container', s.container_no],
          ['Air waybill', s.awb],
          ['Leaves', fmtDate(s.etd)],
          ['Arrives', fmtDate(s.eta_current)],
          ['First promised', fmtDate(s.eta_original)],
          ['Port free time ends', fmtDate(s.free_time_ends)],
          ['Last update', dateTime(s.last_update_at)],
        ]} />
        {s.flags?.includes('port free time ending') && (
          <Notice tone="danger" title="Port charges are close">
            Free time at the port ends {fmtDate(s.free_time_ends)}. After that the port charges storage every day until it is collected.
          </Notice>
        )}
        <div>
          <p className={styles.subhead}>History</p>
          {events.length === 0 ? (
            <p className={styles.small}>No updates recorded yet.</p>
          ) : (
            <ol className={styles.timeline}>
              {events.map((ev, i) => (
                <li key={`${ev.at}-${i}`} className={styles.event}>
                  <span className={styles.dot} aria-hidden="true" />
                  <div className={styles.eventBody}>
                    <div className={styles.eventHead}>
                      <strong>{statusLabel(ev.status, s.mode)}</strong>
                      <span className={styles.meta}>{dateTime(ev.at)}</span>
                    </div>
                    {ev.description && <p className={styles.eventText}>{ev.description}</p>}
                    <span className={styles.meta}>
                      {SOURCE_LABEL[ev.source] || ev.source}
                      {ev.eta && ` · expected ${fmtDate(ev.eta)}`}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      {canEdit && (
        <form className={styles.detailsCol} onSubmit={save}>
          <p className={styles.subhead}>Record a change</p>
          <Field label="Status">
            <Select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} options={statusOptions(statuses)} />
          </Field>
          <div className={styles.pair}>
            <Field label="New arrival date">
              <Input type="date" value={form.eta} onChange={(e) => setForm({ ...form, eta: e.target.value })} />
            </Field>
            <Field label="Port free time ends">
              <Input type="date" value={form.free_time_ends} onChange={(e) => setForm({ ...form, free_time_ends: e.target.value })} />
            </Field>
          </div>
          <Field label="Note" hint="What happened, in a sentence. It goes in the history.">
            <Textarea rows={3} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </Field>
          {state.error && <Notice tone="danger">{state.error}</Notice>}
          {state.done && <Notice tone="success">{state.done}</Notice>}
          <Toolbar>
            <Button type="submit" variant="primary" loading={state.busy}>Save change</Button>
          </Toolbar>
        </form>
      )}
    </div>
  );
}

/* ------------------------------------------------------- forwarder email */

function ForwarderEmailCard({ onApplied }) {
  const [mail, setMail] = useState({ sender: '', subject: '', body: '' });
  const [state, setState] = useState({ busy: false, error: '', data: null });

  async function read() {
    setState({ busy: true, error: '', data: null });
    try {
      const data = await readForwarderEmail(mail.sender, mail.subject, mail.body);
      setState({ busy: false, error: '', data });
      if (data.applied) {
        setMail({ sender: '', subject: '', body: '' });
        if (data.shipment?.id) onApplied(data.shipment.id);
      }
    } catch (e) {
      setState({ busy: false, error: e.message, data: null });
    }
  }

  const result = state.data;
  const reading = result?.reading;

  return (
    <Card
      title="Paste a forwarder email"
      description="The local AI reads the status, dates and reference numbers and updates the matching shipment. It can take up to a minute."
    >
      <div className={styles.stack}>
        <Field label="From"><Input value={mail.sender} onChange={(e) => setMail({ ...mail, sender: e.target.value })} placeholder="ops@forwarder.com" /></Field>
        <Field label="Subject"><Input value={mail.subject} onChange={(e) => setMail({ ...mail, subject: e.target.value })} /></Field>
        <Field label="Message"><Textarea rows={6} value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} /></Field>
        <Toolbar>
          <Button variant="primary" loading={state.busy} disabled={!mail.body.trim()} onClick={read}>Read email</Button>
        </Toolbar>
        {state.busy && <Notice tone="info">{WORKING}</Notice>}
        {state.error && <Notice tone="danger" title="Could not read the email">{state.error}</Notice>}
        {result?.applied && (
          <Notice tone="success" title={`Updated ${result.shipment ? identifier(result.shipment) : 'the shipment'}`}>
            {result.shipment
              ? `It is now "${statusLabel(result.shipment.status, result.shipment.mode)}", arriving ${fmtDate(result.shipment.eta_current)}. The shipment is open in the list.`
              : 'The shipment was updated.'}
          </Notice>
        )}
        {result && !result.applied && (
          <>
            <Notice tone="warning" title="Nothing was changed">
              {result.reason === 'no matching shipment'
                ? 'No shipment matched the numbers in this email. Add the shipment first, or check the reference.'
                : `The email was not clear enough to apply on its own (${result.reason}).`}
              {' '}Someone has been alerted to look at it.
            </Notice>
            {reading && (
              <DetailList items={[
                ['What it says', reading.summary],
                ['Status', statusLabel(reading.status)],
                ['References', reading.references?.join(', ') || '–'],
                ['Leaves', fmtDate(reading.etd)],
                ['Arrives', fmtDate(reading.eta)],
                ['Free time ends', fmtDate(reading.last_free_day)],
                ['How sure', reading.confidence != null ? `${Math.round(reading.confidence * 100)}%` : '–'],
              ]} />
            )}
          </>
        )}
      </div>
    </Card>
  );
}

/* --------------------------------------------------------- add shipment */

const EMPTY_SHIPMENT = {
  forwarder: '', mode: 'SEA', reference: '', container_no: '', awb: '', carrier: '',
  origin: '', destination: '', etd: '', eta: '', free_time_ends: '', status: 'BOOKED', owner_email: '',
};

const MODES = [
  { value: 'SEA', label: 'Sea' },
  { value: 'AIR', label: 'Air' },
  { value: 'TRUCK', label: 'Truck' },
];

function AddShipmentCard({ statuses, onAdded }) {
  const { employeeEmail } = useAuth();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_SHIPMENT);
  const [state, setState] = useState({ busy: false, error: '', done: '' });
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  function start() {
    setForm({ ...EMPTY_SHIPMENT, owner_email: employeeEmail || '' });
    setState({ busy: false, error: '', done: '' });
    setOpen(true);
  }

  async function submit(e) {
    e.preventDefault();
    if (!form.container_no.trim() && !form.awb.trim() && !form.reference.trim()) {
      setState({ busy: false, error: 'Give at least one number: container, air waybill or booking reference.', done: '' });
      return;
    }
    setState({ busy: true, error: '', done: '' });
    const payload = Object.fromEntries(
      Object.entries(form).map(([k, v]) => [k, typeof v === 'string' && v.trim() === '' ? null : v.trim()]),
    );
    try {
      const created = await addShipment(payload);
      setState({ busy: false, error: '', done: `Added ${identifier(created || payload)}.` });
      setOpen(false);
      if (created?.id) onAdded(created.id);
    } catch (err) {
      setState({ busy: false, error: err.message, done: '' });
    }
  }

  return (
    <Card
      title="Add a shipment"
      description="For goods booked with a forwarder. Dates can be filled in later."
      actions={!open && <Button size="sm" onClick={start}>Add shipment</Button>}
    >
      {state.done && !open && <Notice tone="success">{state.done}</Notice>}
      {!open && !state.done && <p className={styles.small}>Container, air waybill or booking number is enough to start.</p>}
      {open && (
        <form className={styles.stack} onSubmit={submit}>
          <div className={styles.pair}>
            <Field label="Forwarder"><Input value={form.forwarder} onChange={set('forwarder')} /></Field>
            <Field label="How it travels"><Select value={form.mode} onChange={set('mode')} options={MODES} /></Field>
          </div>
          {form.mode === 'AIR' ? (
            <Field label="Air waybill"><Input value={form.awb} onChange={set('awb')} placeholder="176-12345675" /></Field>
          ) : (
            <Field label="Container number"><Input value={form.container_no} onChange={set('container_no')} placeholder="MSCU1234567" /></Field>
          )}
          <div className={styles.pair}>
            <Field label="Booking reference"><Input value={form.reference} onChange={set('reference')} /></Field>
            <Field label="Carrier"><Input value={form.carrier} onChange={set('carrier')} placeholder="Shipping line or airline" /></Field>
          </div>
          <div className={styles.pair}>
            <Field label="From"><Input value={form.origin} onChange={set('origin')} placeholder="Shanghai" /></Field>
            <Field label="To"><Input value={form.destination} onChange={set('destination')} placeholder="Savannah" /></Field>
          </div>
          <div className={styles.pair}>
            <Field label="Leaves"><Input type="date" value={form.etd} onChange={set('etd')} /></Field>
            <Field label="Arrives"><Input type="date" value={form.eta} onChange={set('eta')} /></Field>
          </div>
          <div className={styles.pair}>
            <Field label="Port free time ends"><Input type="date" value={form.free_time_ends} onChange={set('free_time_ends')} /></Field>
            <Field label="Status"><Select value={form.status} onChange={set('status')} options={statusOptions(statuses)} /></Field>
          </div>
          <Field label="Owner" hint="Gets the alerts for this shipment.">
            <Input type="email" value={form.owner_email} onChange={set('owner_email')} />
          </Field>
          {state.error && <Notice tone="danger">{state.error}</Notice>}
          <Toolbar>
            <Button type="submit" variant="primary" loading={state.busy}>Add shipment</Button>
            <Button variant="ghost" disabled={state.busy} onClick={() => setOpen(false)}>Cancel</Button>
          </Toolbar>
        </form>
      )}
    </Card>
  );
}
