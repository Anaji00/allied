'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  Badge, Button, Card, DetailList, EmptyState, Field, Notice, Page, Skeleton, Stat, StatGrid, Tabs, Textarea,
  Toolbar, dateTime,
} from '@/components/ui';
import { downloadSubmissionFile, fetchAlerts, fetchProjects, reviewSubmission, submitDesignDocs, syncProjects } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import styles from './projects.module.css';

const cx = (...parts) => parts.filter(Boolean).join(' ');

const SUBMISSION = {
  SUBMITTED: ['Waiting for review', 'info'],
  APPROVED: ['Approved', 'success'],
  CHANGES_REQUESTED: ['Changes requested', 'warning'],
};

/** Runs an async call and tracks busy / error / result. run() resolves to the data, or null on error. */
function useAction(fn) {
  const [state, setState] = useState({ busy: false, error: '', data: null });
  async function run(...args) {
    setState({ busy: true, error: '', data: null });
    try {
      const data = await fn(...args);
      setState({ busy: false, error: '', data });
      return data;
    } catch (e) {
      setState({ busy: false, error: e.message, data: null });
      return null;
    }
  }
  return [state, run];
}

/* --------------------------------------------------------------- helpers */

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function lower(value) {
  return String(value || '').trim().toLowerCase();
}

function includesEmail(list, email) {
  return Boolean(email) && (list || []).some((e) => lower(e) === email);
}

/** 'YYYY-MM-DD' as e.g. "Nov 12", with the year only when it is not this year. */
function shortDate(iso, today) {
  if (!iso) return 'No date';
  const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return String(iso);
  const thisYear = today ? Number(String(today).slice(0, 4)) : new Date().getFullYear();
  const opts = { month: 'short', day: 'numeric' };
  if (y !== thisYear) opts.year = 'numeric';
  return new Date(y, m - 1, d).toLocaleDateString([], opts);
}

function fileSize(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** "jane.doe@allied.com" -> "Jane Doe"; the signed-in user is "You". */
function personName(email, me) {
  if (!email) return '';
  if (lower(email) === me) return 'You';
  const local = String(email).split('@')[0];
  return local
    .split(/[._-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ') || String(email);
}

function People({ emails, me, empty = 'Nobody yet' }) {
  if (!emails || emails.length === 0) return <span className={styles.muted}>{empty}</span>;
  return (
    <span className={styles.people}>
      {emails.map((e, i) => (
        <span key={e} title={e}>
          {personName(e, me)}
          {i < emails.length - 1 ? ', ' : ''}
        </span>
      ))}
    </span>
  );
}

function dueIn(days) {
  if (days == null) return 'Due soon';
  if (days <= 0) return 'Due today';
  if (days === 1) return 'Due tomorrow';
  return `Due in ${days} days`;
}

/** Plain words and a colour for a milestone's state. */
function stateInfo(m) {
  const d = m.days;
  switch (m.state) {
    case 'done':
      return ['Done', 'success'];
    case 'waiting_review':
      return ['Waiting for review', 'info'];
    case 'overdue':
      if (d == null) return ['Late', 'danger'];
      return [d >= 0 ? dueIn(d) : `${plural(-d, 'day')} late`, 'danger'];
    case 'due_soon':
      return [dueIn(d), 'warning'];
    case 'on_track':
      return [d == null ? 'On track' : dueIn(d), 'neutral'];
    default:
      return ['No date', 'neutral'];
  }
}

/** Mirrors the backend's reviewers_for: milestone reviewers, else project owners, else leadership. */
function reviewersOf(m, project, leadership) {
  if (m.reviewer_emails?.length) return m.reviewer_emails;
  if (project.owner_emails?.length) return project.owner_emails;
  return leadership || [];
}

function waitingForMe(m, project, me, leadership) {
  const sub = m.latest_submission;
  return (
    sub?.status === 'SUBMITTED' &&
    includesEmail(reviewersOf(m, project, leadership), me) &&
    lower(sub.submitted_by) !== me
  );
}

function isBehind(p) {
  return Boolean(p.overdue) || p.behind > 0 || (p.milestones || []).some((m) => m.state === 'overdue');
}

/** Most behind first, then soonest due date, undated last. */
function compareProjects(a, b) {
  const lateA = isBehind(a) ? 1 : 0;
  const lateB = isBehind(b) ? 1 : 0;
  if (lateA !== lateB) return lateB - lateA;
  if ((b.behind || 0) !== (a.behind || 0)) return (b.behind || 0) - (a.behind || 0);
  if (a.done !== b.done) return a.done ? 1 : -1;
  if (!a.due_date && !b.due_date) return 0;
  if (!a.due_date) return 1;
  if (!b.due_date) return -1;
  return a.due_date.localeCompare(b.due_date);
}

function syncText(lastSync) {
  if (!lastSync) return 'Not read from monday yet.';
  const when = dateTime(lastSync.at);
  if (lastSync.error) return `Reading monday failed (${when}): ${lastSync.error}`;
  if (lastSync.result?.skipped) return `monday was not read (${when}): ${lastSync.result.skipped}`;
  return `Synced from monday ${when}`;
}

/* ------------------------------------------------------------------ page */

export default function ProjectsPage() {
  return (
    <Suspense fallback={<LoadingPage />}>
      <ProjectsView />
    </Suspense>
  );
}

function LoadingPage() {
  return (
    <Page>
      <Card>
        <Skeleton lines={6} />
      </Card>
    </Page>
  );
}

function ProjectsView() {
  const searchParams = useSearchParams();
  const targetMilestone = searchParams.get('milestone') || '';
  const targetProject = searchParams.get('project') || '';
  const targetKey = `${targetMilestone}|${targetProject}`;
  const hasTarget = Boolean(targetMilestone || targetProject);

  const { isAuthenticated, employeeEmail, hasRole } = useAuth();
  const me = lower(employeeEmail);
  const canOperate = Boolean(hasRole?.('OPERATOR'));

  const [reloadKey, setReloadKey] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  // Leadership reviews milestones whose project has no owner. Only the alerts
  // API reports who that is, so it is read from there (empty if unavailable).
  const [leadership, setLeadership] = useState([]);
  const [tab, setTab] = useState(null);
  const [opened, setOpened] = useState({});
  const [sync, runSync] = useAction(syncProjects);

  // A new deep link (e.g. clicking an alert while already here) resets the view.
  const [seenKey, setSeenKey] = useState(targetKey);
  if (seenKey !== targetKey) {
    setSeenKey(targetKey);
    setTab(null);
    setOpened({});
  }

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    async function load() {
      const [proj, al] = await Promise.allSettled([fetchProjects(), fetchAlerts({ openOnly: true })]);
      if (!alive) return;
      if (proj.status === 'fulfilled') {
        setData(proj.value);
        setError('');
      } else {
        setError(proj.reason?.message || 'Could not load projects');
      }
      if (al.status === 'fulfilled') setLeadership(al.value?.channels?.leadership || []);
    }
    load();
    return () => {
      alive = false;
    };
  }, [reloadKey, isAuthenticated]);

  // Scroll to and focus the linked milestone or project once it is on screen.
  const scrolledRef = useRef('');
  useEffect(() => {
    if (!data || !hasTarget || scrolledRef.current === targetKey) return;
    const el = document.getElementById(targetMilestone ? `milestone-${targetMilestone}` : `project-${targetProject}`);
    if (!el) return;
    scrolledRef.current = targetKey;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.focus({ preventScroll: true });
  }, [data, hasTarget, targetKey, targetMilestone, targetProject]);

  async function refresh() {
    const res = await runSync();
    if (res) reload();
  }

  const projects = data?.projects || [];
  const counts = data?.counts || {};
  const today = data?.today;

  const behindProjects = projects.filter(isBehind).sort(compareProjects);
  const allProjects = [...projects].sort(compareProjects);
  const reviewViews = allProjects
    .map((p) => ({ project: p, milestones: (p.milestones || []).filter((m) => waitingForMe(m, p, me, leadership)) }))
    .filter((v) => v.milestones.length > 0);
  const mineViews = allProjects
    .map((p) => ({ project: p, milestones: (p.milestones || []).filter((m) => includesEmail(m.owner_emails, me)) }))
    .filter((v) => v.milestones.length > 0);
  const reviewCount = reviewViews.reduce((n, v) => n + v.milestones.length, 0);
  const mineCount = mineViews.reduce((n, v) => n + v.milestones.length, 0);

  const activeTab = tab ?? (hasTarget || behindProjects.length === 0 ? 'all' : 'behind');
  const tabs = [
    { key: 'behind', label: 'Behind', count: behindProjects.length },
    { key: 'all', label: 'All projects', count: projects.length },
    { key: 'review', label: 'Waiting for my review', count: reviewCount },
    { key: 'mine', label: 'My milestones', count: mineCount },
  ];

  let views;
  let emptyTitle;
  let emptyDesc;
  if (activeTab === 'behind') {
    views = behindProjects.map((p) => ({ project: p, milestones: p.milestones || [] }));
    emptyTitle = 'Nothing is behind schedule.';
    emptyDesc = 'Projects with a late milestone show up here.';
  } else if (activeTab === 'review') {
    views = reviewViews;
    emptyTitle = me ? 'Nothing is waiting for your review.' : 'Sign in to see what is waiting for your review.';
    emptyDesc = 'Design documents you are asked to review show up here.';
  } else if (activeTab === 'mine') {
    views = mineViews;
    emptyTitle = me ? 'You have no milestones.' : 'Sign in to see your milestones.';
    emptyDesc = 'Milestones you own on the monday projects board show up here.';
  } else {
    views = allProjects.map((p) => ({ project: p, milestones: p.milestones || [] }));
    emptyTitle = 'No projects.';
  }

  const targetFound =
    !targetMilestone || projects.some((p) => (p.milestones || []).some((m) => String(m.item_id) === targetMilestone));
  const projectFound = !targetProject || projects.some((p) => String(p.item_id) === targetProject);

  function isOpen(m) {
    return opened[m.item_id] ?? String(m.item_id) === targetMilestone;
  }
  function toggle(m) {
    const next = !isOpen(m);
    setOpened((o) => ({ ...o, [m.item_id]: next }));
  }

  return (
    <Page>
      <StatGrid>
        <Stat label="Behind schedule" value={data ? counts.overdue ?? 0 : null} tone={counts.overdue > 0 ? 'danger' : undefined} />
        <Stat label="Due in the next 2 days" value={data ? counts.due_soon ?? 0 : null} tone={counts.due_soon > 0 ? 'warning' : undefined} />
        <Stat label="Waiting for review" value={data ? counts.waiting_review ?? 0 : null} />
        <Stat label="Missing design docs" value={data ? counts.missing_docs ?? 0 : null} tone={counts.missing_docs > 0 ? 'warning' : undefined} />
      </StatGrid>

      <div className={styles.syncBar}>
        <p className={styles.syncLine}>{data ? syncText(data.last_sync) : error ? '' : 'Loading…'}</p>
        {canOperate && (
          <Button loading={sync.busy} onClick={refresh}>Refresh from monday</Button>
        )}
      </div>

      {sync.error && <Notice tone="danger" title="Could not read monday">{sync.error}</Notice>}
      {sync.data?.skipped && <Notice tone="warning" title="monday was not read">{sync.data.skipped}</Notice>}
      {sync.data && !sync.data.skipped && (
        <Notice tone="success">
          Read {plural(sync.data.projects ?? 0, 'project')} and {plural(sync.data.milestones ?? 0, 'milestone')} from monday.
          {sync.data.removed > 0 && ` Removed ${sync.data.removed} no longer on the board.`}
        </Notice>
      )}

      {error && (
        <Notice tone="danger" title="Could not load projects" action={<Button size="sm" onClick={reload}>Try again</Button>}>
          {error}
        </Notice>
      )}

      {data && !targetFound && (
        <Notice tone="warning">That milestone is no longer on the monday projects board.</Notice>
      )}
      {data && !projectFound && (
        <Notice tone="warning">That project is no longer on the monday projects board.</Notice>
      )}

      {!data ? (
        !error && (
          <Card>
            <Skeleton lines={6} />
          </Card>
        )
      ) : projects.length === 0 ? (
        <Card>
          <EmptyState
            title="No projects yet."
            description="Projects and their milestones come from the monday projects board (the board set in MONDAY_PROJECTS_BOARD_ID). The board is read every 10 minutes, so new projects appear here shortly after they are added on monday."
            action={canOperate ? <Button size="sm" loading={sync.busy} onClick={refresh}>Refresh from monday</Button> : null}
          />
        </Card>
      ) : (
        <>
          <Tabs tabs={tabs} value={activeTab} onChange={setTab} />
          {views.length === 0 ? (
            <Card>
              <EmptyState title={emptyTitle} description={emptyDesc} />
            </Card>
          ) : (
            <div className={styles.projects}>
              {views.map(({ project, milestones }) => (
                <ProjectCard
                  key={project.item_id}
                  project={project}
                  milestones={milestones}
                  today={today}
                  me={me}
                  canOperate={canOperate}
                  leadership={leadership}
                  highlighted={String(project.item_id) === targetProject}
                  targetMilestone={targetMilestone}
                  isOpen={isOpen}
                  onToggle={toggle}
                  onChanged={reload}
                />
              ))}
            </div>
          )}
        </>
      )}
    </Page>
  );
}

/* --------------------------------------------------------------- project */

function ProjectCard({ project, milestones, today, me, canOperate, leadership, highlighted, targetMilestone, isOpen, onToggle, onChanged }) {
  const badges = (
    <div className={styles.badges}>
      {project.behind > 0 && <Badge tone="danger">{plural(project.behind, 'milestone')} late</Badge>}
      {project.overdue && <Badge tone="danger">Past its due date</Badge>}
      {project.done && <Badge tone="success">Done</Badge>}
    </div>
  );

  return (
    <div
      id={`project-${project.item_id}`}
      tabIndex={-1}
      className={cx(styles.anchor, highlighted && styles.targetCard)}
    >
      <Card title={project.name} description={project.group_title || undefined} actions={badges} flush>
        <p className={styles.projectMeta}>
          <span>Owner: <People emails={project.owner_emails} me={me} empty="Not set" /></span>
          <span>Due {shortDate(project.due_date, today)}</span>
          {project.status && <span>monday status: {project.status}</span>}
        </p>
        {milestones.length === 0 ? (
          <p className={styles.noMilestones}>No milestones on this project yet.</p>
        ) : (
          <ul className={styles.milestones}>
            {milestones.map((m) => (
              <MilestoneRow
                key={m.item_id}
                milestone={m}
                project={project}
                today={today}
                me={me}
                canOperate={canOperate}
                leadership={leadership}
                highlighted={String(m.item_id) === targetMilestone}
                open={isOpen(m)}
                onToggle={() => onToggle(m)}
                onChanged={onChanged}
              />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------- milestone */

function MilestoneRow({ milestone: m, project, today, me, canOperate, leadership, highlighted, open, onToggle, onChanged }) {
  const [label, tone] = stateInfo(m);
  const latest = m.latest_submission;
  const panelId = `milestone-panel-${m.item_id}`;
  const buttonId = `milestone-button-${m.item_id}`;

  return (
    <li id={`milestone-${m.item_id}`} tabIndex={-1} className={cx(styles.milestone, styles.anchor, highlighted && styles.targetRow)}>
      <button
        type="button"
        id={buttonId}
        className={styles.row}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <span className={styles.rowMain}>
          <span className={styles.rowName}>
            <span className={cx(styles.chevron, open && styles.chevronOpen)} aria-hidden="true" />
            {m.name}
          </span>
          <span className={styles.rowMeta}>
            <People emails={m.owner_emails} me={me} empty="No owner" />
            <span aria-hidden="true"> · </span>
            <span>Due {shortDate(m.due_date, today)}</span>
            {latest && (
              <>
                <span aria-hidden="true"> · </span>
                <span>Version {latest.version}: {(SUBMISSION[latest.status]?.[0] || latest.status).toLowerCase()}</span>
              </>
            )}
          </span>
        </span>
        <span className={styles.badges}>
          <Badge tone={tone}>{label}</Badge>
          {m.missing_docs && <Badge tone="warning">Design docs needed</Badge>}
        </span>
      </button>
      {open && (
        <div id={panelId} role="region" aria-labelledby={buttonId} className={styles.panel}>
          <MilestoneDetails milestone={m} project={project} today={today} me={me} canOperate={canOperate} leadership={leadership} onChanged={onChanged} />
        </div>
      )}
    </li>
  );
}

function MilestoneDetails({ milestone: m, project, today, me, canOperate, leadership, onChanged }) {
  const latest = m.latest_submission;
  const submissions = m.submissions || [];
  const waiting = latest?.status === 'SUBMITTED';
  const mySubmission = waiting && lower(latest.submitted_by) === me;
  // The backend only accepts reviews from operators and above.
  const canReview = waiting && canOperate && Boolean(me) && !mySubmission;
  const canSubmit = canOperate && !m.done && !waiting;
  const reviewers = m.reviewer_emails?.length ? m.reviewer_emails : null;

  return (
    <div className={styles.details}>
      <DetailList
        items={[
          ['Owner', <People key="o" emails={m.owner_emails} me={me} empty="Not set" />],
          ['Reviewers', reviewers
            ? <People key="r" emails={reviewers} me={me} />
            : project.owner_emails?.length
              ? <span key="r">Project owner: <People emails={project.owner_emails} me={me} /></span>
              : <span key="r">Leadership: <People emails={leadership} me={me} empty="not set" /></span>],
          ['Due', m.due_date ? shortDate(m.due_date, today) : 'No date'],
          ['monday status', m.status || '–'],
        ]}
      />

      {canReview && <ReviewForm submission={latest} onChanged={onChanged} />}
      {mySubmission && (
        <Notice tone="info">Your version {latest.version} is waiting for someone else to review it.</Notice>
      )}
      {waiting && !mySubmission && !canReview && <Notice tone="info">Version {latest.version} is waiting for review.</Notice>}

      {canSubmit && <SubmitForm milestone={m} onChanged={onChanged} />}

      <div>
        <h3 className={styles.subhead}>Design documents sent</h3>
        {submissions.length === 0 ? (
          <p className={styles.muted}>
            {m.needs_submission ? 'Nothing sent yet. This milestone needs design documents.' : 'Nothing sent yet.'}
          </p>
        ) : (
          <ol className={styles.history}>
            {submissions.map((s) => (
              <SubmissionItem key={s.id} submission={s} me={me} />
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function SubmissionItem({ submission: s, me }) {
  const [label, tone] = SUBMISSION[s.status] || [s.status, 'neutral'];
  const [busyIndex, setBusyIndex] = useState(null);
  const [error, setError] = useState('');

  async function download(index, name) {
    setBusyIndex(index);
    setError('');
    try {
      await downloadSubmissionFile(s.id, index, name);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyIndex(null);
    }
  }

  return (
    <li className={styles.submission}>
      <div className={styles.subHead}>
        <span className={styles.subTitle}>Version {s.version}</span>
        <Badge tone={tone}>{label}</Badge>
      </div>
      <p className={styles.meta}>
        Sent by <People emails={s.submitted_by ? [s.submitted_by] : []} me={me} empty="someone" /> on {dateTime(s.submitted_at)}
      </p>
      {s.note && <p className={styles.note}>{s.note}</p>}
      {s.files?.length > 0 && (
        <ul className={styles.files}>
          {s.files.map((f, i) => (
            <li key={`${f.name}-${i}`} className={styles.file}>
              <span className={styles.fileName}>
                {f.name}
                {fileSize(f.size) && <span className={styles.muted}> ({fileSize(f.size)})</span>}
              </span>
              <Button
                size="sm"
                variant="ghost"
                loading={busyIndex === i}
                onClick={() => download(i, f.name)}
                aria-label={`Download ${f.name}`}
              >
                Download
              </Button>
            </li>
          ))}
        </ul>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
      {s.reviewed_by && (
        <div className={cx(styles.review, s.status === 'CHANGES_REQUESTED' && styles.reviewChanges)}>
          <p className={styles.meta}>
            {s.status === 'APPROVED' ? 'Approved' : 'Reviewed'} by <People emails={[s.reviewed_by]} me={me} /> on {dateTime(s.reviewed_at)}
          </p>
          {s.review_note && <p className={styles.note}>{s.review_note}</p>}
        </div>
      )}
    </li>
  );
}

function ReviewForm({ submission, onChanged }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');
  const noteId = `review-note-${submission.id}`;
  const errorId = `review-error-${submission.id}`;

  async function decide(approve) {
    if (!approve && !note.trim()) {
      setError('Say what needs to change before sending it back.');
      return;
    }
    setBusy(approve ? 'approve' : 'changes');
    setError('');
    try {
      await reviewSubmission(submission.id, approve, note.trim());
      setNote('');
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.box}>
      <h3 className={styles.subhead}>Review version {submission.version}</h3>
      <Field label="Note" hint="Optional when approving. Needed when asking for changes: say what to change.">
        <Textarea
          id={noteId}
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-describedby={error ? errorId : undefined}
          aria-invalid={error ? true : undefined}
        />
      </Field>
      {error && (
        <p id={errorId} className={styles.error} role="alert">{error}</p>
      )}
      <Toolbar>
        <Button variant="primary" loading={busy === 'approve'} disabled={Boolean(busy)} onClick={() => decide(true)}>
          Approve
        </Button>
        <Button variant="danger" loading={busy === 'changes'} disabled={Boolean(busy)} onClick={() => decide(false)}>
          Request changes
        </Button>
      </Toolbar>
    </div>
  );
}

function SubmitForm({ milestone, onChanged }) {
  const [files, setFiles] = useState([]);
  const [note, setNote] = useState('');
  const [inputKey, setInputKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);
  const changes = milestone.latest_submission?.status === 'CHANGES_REQUESTED';

  async function submit(e) {
    e.preventDefault();
    if (files.length === 0) {
      setError('Choose at least one file.');
      return;
    }
    setBusy(true);
    setError('');
    setSent(false);
    try {
      await submitDesignDocs(milestone.item_id, files, note.trim());
      setFiles([]);
      setNote('');
      setInputKey((k) => k + 1);
      setSent(true);
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={styles.box} onSubmit={submit}>
      <h3 className={styles.subhead}>{changes ? 'Send a new version' : 'Send design documents for review'}</h3>
      {sent && <Notice tone="success">Sent for review.</Notice>}
      <Field
        label="Files"
        hint={files.length > 0 ? files.map((f) => f.name).join(', ') : 'You can choose more than one file.'}
      >
        <input
          key={inputKey}
          type="file"
          multiple
          className={styles.fileInput}
          onChange={(e) => {
            setFiles(Array.from(e.target.files || []));
            setError('');
          }}
        />
      </Field>
      <Field label="Note (optional)">
        <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything the reviewer should know" />
      </Field>
      {error && <Notice tone="danger">{error}</Notice>}
      <Toolbar>
        <Button type="submit" variant="primary" loading={busy}>Send for review</Button>
      </Toolbar>
    </form>
  );
}
