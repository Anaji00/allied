'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { api, ApiError, ok } from '@/lib/api/client';
import { dateTime, money, words } from '@/lib/format';
import type { Role } from '@/lib/nav';

// The Inbox merges four queues that used to live on four pages: my alerts,
// problems on orders (exceptions), AI results waiting for approval, and
// monday.com board changes that failed. Design reviews arrive as alerts.

export type Severity = 'critical' | 'warning' | 'info';
export type Group = 'approvals' | 'problems' | 'schedule';
export type Source = 'alerts' | 'problems' | 'ai' | 'monday';

export type InboxAction = {
  id: string;
  label: string;
  /** Shown in the toast: "Approved: …". */
  verb: string;
  primary?: boolean;
  hotkey?: 'a' | 'r';
  minRole: Role;
  /** Sent after the undo window; `keepalive` when the tab is closing. */
  commit?: (keepalive: boolean) => Promise<unknown>;
  href?: string;
  /** Needs a value typed first (the new unit price on a price check). */
  needsPrice?: boolean;
};

export type InboxItem = {
  key: string;
  source: Source;
  group: Group;
  severity: Severity;
  kind: string;
  title: string;
  meta: string;
  at: string | null;
  body?: string;
  facts: [string, string][];
  actions: InboxAction[];
  /** Problems can be explained in plain words by the AI. */
  explainId?: string;
  /** AI results: what the model produced, for the reviewer to check. */
  output?: unknown;
  /** The AI tool behind an approval, so its result renders in its own shape. */
  feature?: string;
};

type Alert = {
  id: number;
  kind: string;
  severity: string;
  title: string;
  body?: string | null;
  link?: string | null;
  recipients?: string[] | null;
  created_at?: string | null;
};
type Problem = {
  exception_id: string;
  exception_type: string;
  severity: string;
  title: string;
  description: string;
  reference_id: string;
  vendor_or_customer: string;
  variance_amount?: number | null;
  created_at?: string | null;
  available_actions?: { action: string; label: string }[];
};
type Suggestion = {
  id: number;
  feature: string;
  title: string;
  reference?: string | null;
  provider?: string | null;
  model?: string | null;
  confidence?: number | null;
  output?: unknown;
  created_at?: string | null;
  created_by?: string | null;
};
type MondayEvent = {
  id: number;
  status: string;
  action?: string | null;
  label?: string | null;
  item_id?: string | number | null;
  item_name?: string | null;
  attempts?: number | null;
  last_error?: string | null;
  received_at?: string | null;
  next_attempt_at?: string | null;
};

/** A write sent with the session token; keepalive lets it finish as the tab closes. */
async function post(path: string, body?: unknown, keepalive = false) {
  const res = await authFetch(`/api/v1${path}`, {
    method: 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    keepalive,
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j?.detail).catch(() => null);
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `Request failed (${res.status}).`);
  }
  return res.json().catch(() => null);
}

const SCHEDULE_KINDS = /^(milestone_|project_|shipment_)/;
const REVIEW_KINDS = new Set(['review_waiting', 'submission_ready']);

function fromAlert(a: Alert, everyone: boolean): InboxItem {
  const severity: Severity = a.severity === 'critical' ? 'critical' : a.severity === 'warning' ? 'warning' : 'info';
  const actions: InboxAction[] = [
    {
      id: 'handled',
      label: 'Mark handled',
      verb: 'Handled',
      primary: true,
      minRole: 'VIEWER',
      commit: (keepalive) => post(`/alerts/${a.id}/acknowledge`, undefined, keepalive),
    },
  ];
  if (a.link) actions.push({ id: 'open', label: 'Open', verb: 'Opened', minRole: 'VIEWER', href: a.link });
  const facts: [string, string][] = [['Sent', dateTime(a.created_at)]];
  if (everyone && a.recipients?.length) facts.push(['To', a.recipients.join(', ')]);
  return {
    key: `alerts:${a.id}`,
    source: 'alerts',
    group: REVIEW_KINDS.has(a.kind) ? 'approvals' : SCHEDULE_KINDS.test(a.kind) ? 'schedule' : 'problems',
    severity,
    kind: REVIEW_KINDS.has(a.kind) ? 'Review' : words(a.kind),
    title: a.title,
    meta: (a.body || '').split('\n')[0],
    at: a.created_at ?? null,
    body: a.body ?? undefined,
    facts,
    actions,
  };
}

/** The monday event a stuck-event problem is about, as `MONDAY-EVT-<id>`. */
const eventRef = (id: number) => `MONDAY-EVT-${id}`;

function fromProblem(p: Problem, event?: MondayEvent): InboxItem {
  const severity: Severity = p.severity === 'CRITICAL' ? 'critical' : p.severity === 'LOW' ? 'info' : 'warning';
  const listed = p.available_actions ?? [];
  const actions: InboxAction[] = listed.map((a, i) => ({
    id: a.action,
    label: a.label,
    verb: a.action === 'REPLAY_MONDAY_EVENT' ? 'Replayed' : 'Done',
    primary: i === 0,
    minRole: 'OPERATOR',
    needsPrice: a.action === 'OVERRIDE_PRICE_CHECK',
    commit: (keepalive) =>
      post(`/exceptions/${encodeURIComponent(p.exception_id)}/resolve`, { action_type: a.action, resolution_notes: `Resolved from the Inbox (${a.action})` }, keepalive),
  }));
  if (!listed.some((a) => a.action === 'MANUALLY_RESOLVED')) {
    actions.push({
      id: 'MANUALLY_RESOLVED',
      label: 'Fixed outside the system',
      verb: 'Resolved',
      primary: actions.length === 0,
      minRole: 'OPERATOR',
      commit: (keepalive) =>
        post(`/exceptions/${encodeURIComponent(p.exception_id)}/resolve`, { action_type: 'MANUALLY_RESOLVED', resolution_notes: 'Fixed outside the system (from the Inbox)' }, keepalive),
    });
  }
  const facts: [string, string][] = [
    ['Reference', p.reference_id],
    ['Who', p.vendor_or_customer],
  ];
  if (p.variance_amount) facts.push(['Difference', money(p.variance_amount)]);
  if (event) {
    facts.push(['Change', event.label || words(event.action)], ['Tries', String(event.attempts ?? 0)]);
    actions.push({ id: 'log', label: 'Event log', verb: 'Opened', minRole: 'VIEWER', href: '/health?tab=monday' });
  }
  facts.push(['Raised', dateTime(p.created_at)]);
  return {
    key: `problems:${p.exception_id}`,
    source: 'problems',
    group: 'problems',
    severity,
    kind: words(p.exception_type),
    title: p.title,
    meta: `${p.vendor_or_customer} · ${p.reference_id}`,
    at: p.created_at ?? null,
    body: p.description,
    facts,
    actions,
    explainId: p.exception_id,
  };
}

function fromSuggestion(s: Suggestion): InboxItem {
  const confidence = s.confidence == null ? null : Math.round(s.confidence * (s.confidence <= 1 ? 100 : 1));
  const review = (approve: boolean) => (keepalive: boolean) =>
    post(`/ai/suggestions/${s.id}/review`, { approve, note: approve ? 'Approved from the Inbox' : 'Rejected from the Inbox' }, keepalive);
  const facts: [string, string][] = [['Tool', words(s.feature)]];
  if (confidence != null) facts.push(['Confidence', `${confidence}%`]);
  if (s.reference) facts.push(['Reference', s.reference]);
  if (s.provider) facts.push(['Engine', [s.provider, s.model].filter(Boolean).join(' · ')]);
  if (s.created_by) facts.push(['Asked by', s.created_by]);
  return {
    key: `ai:${s.id}`,
    source: 'ai',
    group: 'approvals',
    severity: 'info',
    kind: `AI · ${words(s.feature)}`,
    title: s.title,
    meta: [confidence != null ? `${confidence}% sure` : '', s.reference || ''].filter(Boolean).join(' · '),
    at: s.created_at ?? null,
    facts,
    output: s.output,
    feature: s.feature,
    actions: [
      { id: 'approve', label: 'Approve', verb: 'Approved', primary: true, hotkey: 'a', minRole: 'OPERATOR', commit: review(true) },
      { id: 'reject', label: 'Reject', verb: 'Rejected', hotkey: 'r', minRole: 'OPERATOR', commit: review(false) },
    ],
  };
}

function fromEvent(e: MondayEvent): InboxItem {
  const item = e.item_name || (e.item_id ? `Item ${e.item_id}` : 'A board item');
  const facts: [string, string][] = [
    ['Change', e.label || words(e.action)],
    ['Item', item],
    ['Tries', String(e.attempts ?? 0)],
    ['Received', dateTime(e.received_at)],
  ];
  return {
    key: `monday:${e.id}`,
    source: 'monday',
    group: 'problems',
    severity: e.status === 'DEAD' ? 'critical' : 'warning',
    kind: 'monday.com',
    title: `“${e.label || words(e.action) || 'Status'}” board change failed`,
    meta: `${item} · ${e.status === 'DEAD' ? 'gave up retrying' : 'will retry'}`,
    at: e.received_at ?? null,
    body: e.last_error ?? undefined,
    facts,
    actions: [
      { id: 'replay', label: 'Replay', verb: 'Replayed', primary: true, minRole: 'OPERATOR', commit: (k) => post(`/webhooks/monday/events/${e.id}/replay`, undefined, k) },
      { id: 'log', label: 'Event log', verb: 'Opened', minRole: 'VIEWER', href: '/health?tab=monday' },
    ],
  };
}

const RANK: Record<Severity, number> = { critical: 0, warning: 1, info: 2 };

const POLL = { refetchInterval: 30_000 } as const;

/** Everything waiting on this person, newest first with critical items on top. */
export function useInbox({ everyone = false } = {}) {
  const { isAuthenticated, hasRole } = useAuth();
  const canSeeAll = hasRole('MANAGER');
  const showAll = everyone && canSeeAll;
  const enabled = isAuthenticated;

  const alerts = useQuery({
    queryKey: ['inbox', 'alerts', showAll],
    queryFn: async () =>
      ((await ok(api.GET('/api/v1/alerts', { params: { query: { open_only: true, everyone: showAll, limit: 200 } } }))) as {
        items: Alert[];
      }).items,
    enabled,
    ...POLL,
  });
  const problems = useQuery({
    queryKey: ['inbox', 'problems'],
    queryFn: async () => (await ok(api.GET('/api/v1/exceptions', { params: { query: { status_filter: 'OPEN' } } }))) as Problem[],
    enabled,
    ...POLL,
  });
  const ai = useQuery({
    queryKey: ['inbox', 'ai'],
    queryFn: async () =>
      ((await ok(api.GET('/api/v1/ai/suggestions', { params: { query: { status: 'SUGGESTED', limit: 100 } } }))) as {
        items: Suggestion[];
      }).items,
    enabled,
    ...POLL,
  });
  const monday = useQuery({
    queryKey: ['inbox', 'monday'],
    queryFn: async () =>
      ((await ok(api.GET('/api/v1/webhooks/monday/events', { params: { query: { limit: 200 } } }))) as {
        events: MondayEvent[];
      }).events.filter((e) => e.status === 'FAILED' || e.status === 'DEAD'),
    enabled,
    ...POLL,
  });

  // A stuck monday event also raises a problem; show it once, as the problem,
  // which carries the replay and closes when it is handled.
  const events = new Map((monday.data ?? []).map((e) => [eventRef(e.id), e]));
  const raised = new Set((problems.data ?? []).filter((p) => p.exception_type === 'MONDAY_EVENT_FAILED').map((p) => p.reference_id));
  const items: InboxItem[] = [
    ...(alerts.data ?? []).map((a) => fromAlert(a, showAll)),
    ...(problems.data ?? []).map((p) => fromProblem(p, p.exception_type === 'MONDAY_EVENT_FAILED' ? events.get(p.reference_id) : undefined)),
    ...(ai.data ?? []).map(fromSuggestion),
    ...(monday.data ?? []).filter((e) => !raised.has(eventRef(e.id))).map(fromEvent),
  ].sort((x, y) => RANK[x.severity] - RANK[y.severity] || (y.at ?? '').localeCompare(x.at ?? ''));

  const sources = { alerts, problems, ai, monday };
  const failed = (Object.entries(sources) as [Source, typeof alerts][])
    .filter(([, q]) => q.isError)
    .map(([name, q]) => ({ name, error: q.error }));

  return {
    items,
    isPending: [alerts, problems, ai, monday].some((q) => q.isPending),
    failed,
    canSeeAll,
    refetch: () => Promise.all([alerts.refetch(), problems.refetch(), ai.refetch(), monday.refetch()]),
  };
}

/** After a decision lands, refresh just the queue it came from. */
export function useRefreshSource() {
  const client = useQueryClient();
  return (source: Source) => client.invalidateQueries({ queryKey: ['inbox', source] });
}

export async function explainProblem(exceptionId: string) {
  const res = (await ok(
    api.POST('/api/v1/ai/exceptions/{exception_id}/explain', { params: { path: { exception_id: exceptionId } } }),
  )) as { result?: { explanation?: string; suggested_fix?: string } };
  return res.result ?? {};
}

export { post as inboxPost };
