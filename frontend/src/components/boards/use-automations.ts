'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { api, ok } from '@/lib/api/client';
import { optimistic } from '@/lib/optimistic';
import { decideAssistantAction } from '@/lib/opsApi';
import type { Role } from './use-boards';

/** off: does nothing. suggest: drafts, a person sends. auto: does it itself. */
export type Mode = 'off' | 'suggest' | 'auto';
export type Did = { what: string; outcome: string; to?: string[] };

export type Recipe = {
  name: string;
  /** play: on a timer. reaction: when something changes. check: the behind-schedule alerts. */
  kind: 'play' | 'reaction' | 'check';
  when: string;
  then: string;
  mode: Mode;
  last: { at?: string; did: Did[]; error?: string | null } | null;
  can_preview: boolean;
};

export type Draft = {
  id: string;
  play: string | null;
  summary: string;
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'CANCELLED' | 'EXPIRED';
  to?: string | null;
  subject?: string | null;
  body?: string | null;
  created_at: string | null;
  decided_at: string | null;
  reviewer: string;
  /** Whether the signed-in person may send or cancel it. */
  yours: boolean;
};

/** Something an automation would draft now (key), or a message only a person can send (manual: no email on file). */
export type Due = {
  play: string;
  key: string | null;
  summary: string;
  action_id: string | null;
  manual?: boolean;
  body?: string;
  /** Set when one of people's own rules made it. */
  rule?: string | null;
};

/** One of people's own "when this, then that" rules (managers add them). */
export type Rule = {
  id: number;
  name: string;
  board_role: 'projects' | 'vendors' | 'rfqs';
  board_id: string | null;
  /** status_is: on the change. status_for: sat in status `column` for `value` days. date_within: a date `value` days away. */
  trigger: 'status_is' | 'status_for' | 'date_within';
  column?: string | null;
  value: string;
  action: 'notify' | 'draft_email';
  recipients?: string | null;
  email_to?: string | null;
  subject?: string | null;
  body?: string | null;
  mode: 'suggest' | 'auto';
  enabled: boolean;
  description: string;
  created_by?: string | null;
};
export type RuleIn = Partial<Omit<Rule, 'id' | 'board_id' | 'description' | 'created_by'>>;

/** How people treat each automation's drafts: sent, cancelled or left to lapse. */
export type Score = {
  name: string;
  mode: string | null;
  drafted: number;
  sent: number;
  cancelled: number;
  lapsed: number;
  failed: number;
  waiting: number;
  send_rate: number | null;
  advice: string;
};

export type BoardEvent = {
  at: string;
  event: string;
  summary: string;
  reactions: { name: string; mode: Mode; did?: Did[]; error?: string }[];
};

export type Automations = {
  board_id: string;
  role: Role;
  enabled: boolean;
  every_seconds: number;
  recipes: Recipe[];
  /** People's own rules for this board. */
  rules: Rule[];
  recent: BoardEvent[];
  drafts: Record<string, Draft[]>;
  due: Record<string, Due[]>;
  /** Vendor rows: when the vendor is followed up (vendor boards only). */
  follow_ups?: Record<string, FollowUp>;
  /** Drafts on this board still waiting for someone to send or cancel them. */
  waiting: number;
};

/**
 * A vendor order's follow-up: once it hasn't shipped `wait_days` after its PO, the vendor is
 * followed up once on its own; after that, staff follow up on command (`now_key`).
 */
export type FollowUp = {
  job_id: number;
  first: string;
  wait_days: number;
  wait_basis: string;
  /** The wait set by hand on this order, or null for the usual one. */
  manual_days: number | null;
  last: string | null;
  drafted: string | null;
  due: boolean;
  started: boolean;
  reason: string;
  /** Key for "Follow up now"; null before the first follow-up's day, or with no email on file. */
  now_key: string | null;
};

export const automationsKey = (boardId: string | null) => ['boards', boardId, 'automations'] as const;

export function useAutomations(boardId: string | null, tracked: boolean) {
  return useQuery({
    queryKey: automationsKey(boardId),
    queryFn: async () =>
      (await ok(api.GET('/api/v1/boards/{board_id}/automations', { params: { path: { board_id: boardId! } } }))) as unknown as Automations,
    enabled: Boolean(boardId) && tracked,
    refetchInterval: 60_000,
  });
}

/** What needs a person on one item: drafts to send, and things that could be drafted now. */
export function needsAction(data: Automations | undefined, itemId: string, canDraft: boolean) {
  // Drafts only another reviewer can send aren't this person's to act on, and only operators can
  // draft; a message to send by hand (no email on file) is anyone's.
  const waiting = (data?.drafts[itemId] ?? []).filter((d) => d.status === 'PENDING' && d.yours).length;
  const doable = (data?.due[itemId] ?? []).filter((d) => d.manual || canDraft);
  const manual = doable.filter((d) => d.manual).length;
  return { waiting, due: doable.length, manual, any: waiting + doable.length > 0 };
}

/** Send (confirm) or cancel a draft. The row shows the decision at once; a refusal puts it back. */
export function useDecide(boardId: string) {
  const client = useQueryClient();
  const o = optimistic(client, automationsKey(boardId), (old: Automations, v: { id: string; confirm: boolean }) => ({
    ...old,
    waiting: Math.max(0, old.waiting - 1),
    drafts: Object.fromEntries(
      Object.entries(old.drafts).map(([item, ds]) => [
        item,
        ds.map((d) => (d.id === v.id ? { ...d, status: v.confirm ? 'RUNNING' : 'CANCELLED', decided_at: new Date().toISOString() } : d)),
      ]),
    ),
  }));
  return useMutation({
    mutationFn: async (v: { id: string; confirm: boolean }) =>
      (await decideAssistantAction(v.id, v.confirm)) as { status: string; result?: { done?: string; error?: string } },
    onMutate: o.onMutate,
    onError: (e, _v, ctx) => {
      o.rollback(ctx);
      toast.error(e.message);
    },
    onSuccess: (d) => {
      if (d.status === 'DONE') toast.success(d.result?.done ?? 'Done.');
      else if (d.status === 'FAILED') toast.error(d.result?.error ?? 'It didn’t work.');
      else toast('Cancelled. Nothing was sent.');
    },
    onSettled: o.onSettled,
  });
}

/** Draft one item's message now. It waits for a person; nothing is sent. */
export function useDraftNow(boardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { itemId: string; key: string }) =>
      ok(
        api.POST('/api/v1/boards/{board_id}/items/{item_id}/drafts', {
          params: { path: { board_id: boardId, item_id: v.itemId } },
          body: { key: v.key },
        }),
      ),
    onSuccess: () => toast.success('Drafted. Read it, then send or cancel it.'),
    onError: (e) => toast.error(e.message),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: automationsKey(boardId) });
      void client.invalidateQueries({ queryKey: ['vendors'] }); // its follow-up shows there too
    },
  });
}

export type RunReport = { dry_run: boolean; plays: Record<string, { mode: Mode; did: Did[]; skipped?: string; error?: string }> };

/** Run one timed recipe: a preview (dry run) changes nothing; a real run does what its mode allows. */
export function useRunPlay(boardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { play: string; dryRun: boolean }) =>
      (await ok(api.POST('/api/v1/autopilot/run', { body: { play: v.play, dry_run: v.dryRun } }))) as unknown as RunReport,
    onError: (e) => toast.error(e.message),
    onSettled: (_d, _e, v) => {
      if (!v.dryRun) void client.invalidateQueries({ queryKey: ['boards', boardId] });
    },
  });
}

export function useRunChecks() {
  return useMutation({
    mutationFn: async () => ok(api.POST('/api/v1/alerts/check-now')),
    onSuccess: () => toast.success('Checks run. Anyone with something late has been told.'),
    onError: (e) => toast.error(e.message),
  });
}

/** Read the board from monday now instead of waiting for the next change or the hourly read. */
export function useReadNow(boardId: string, role: Role) {
  const client = useQueryClient();
  return useMutation({
    /** Why nothing was read, or null when it was. */
    mutationFn: async (): Promise<string | null> => {
      if (role === 'projects' || role === 'vendors') {
        const res = (await ok(api.POST(role === 'projects' ? '/api/v1/projects/sync' : '/api/v1/vendors/sync'))) as { skipped?: string };
        return res?.skipped ?? null;
      }
      const res = (await ok(api.POST('/api/v1/autopilot/run', { body: { play: 'rfq_board', dry_run: false } }))) as unknown as RunReport;
      const play = res.plays.rfq_board;
      return play?.skipped ?? play?.error ?? null;
    },
    onSuccess: (skipped) => {
      if (skipped) toast(`Not read: ${skipped}.`);
      else toast.success('Read from monday.');
    },
    onError: (e) => toast.error(e.message),
    onSettled: () => client.invalidateQueries({ queryKey: ['boards'] }),
  });
}

/** Change how long an order waits after its PO before the vendor is followed up; 0 = the usual wait. */
export function useFollowUpWait(boardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { jobId: number; days: number }) =>
      ok(api.PATCH('/api/v1/vendors/{job_id}', { params: { path: { job_id: v.jobId } }, body: { follow_up_days: v.days } })),
    onSuccess: (_d, v) => toast.success(v.days ? `Follow up ${v.days} days after the PO.` : 'Back to the usual wait.'),
    onError: (e) => toast.error(e.message),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: automationsKey(boardId) });
      void client.invalidateQueries({ queryKey: ['vendors'] });
    },
  });
}

/** Who may press what, mirroring the API's own checks. */
/** Add, change, switch off or remove one of people's own rules. Managers only, as the API checks. */
export function useRules(boardId: string) {
  const client = useQueryClient();
  const settle = () => client.invalidateQueries({ queryKey: automationsKey(boardId) });
  const o = optimistic(client, automationsKey(boardId), (old: Automations, v: { id: number; patch: RuleIn }) => ({
    ...old,
    rules: old.rules.map((r) => (r.id === v.id ? { ...r, ...v.patch } : r)),
  }));
  const add = useMutation({
    mutationFn: async (rule: RuleIn) => ok(api.POST('/api/v1/autopilot/recipes', { body: rule })),
    onSuccess: () => toast.success('Rule added. It runs in the app, never in monday.'),
    onError: (e) => toast.error(e.message),
    onSettled: settle,
  });
  const change = useMutation({
    mutationFn: async (v: { id: number; patch: RuleIn }) =>
      ok(api.PATCH('/api/v1/autopilot/recipes/{recipe_id}', { params: { path: { recipe_id: v.id } }, body: v.patch })),
    onMutate: o.onMutate,
    onError: (e, _v, ctx) => {
      o.rollback(ctx);
      toast.error(e.message);
    },
    onSettled: o.onSettled,
  });
  const remove = useMutation({
    mutationFn: async (id: number) => ok(api.DELETE('/api/v1/autopilot/recipes/{recipe_id}', { params: { path: { recipe_id: id } } })),
    onSuccess: () => toast('Rule removed.'),
    onError: (e) => toast.error(e.message),
    onSettled: settle,
  });
  const checkDates = useMutation({
    mutationFn: async (dryRun: boolean) =>
      (await ok(api.POST('/api/v1/autopilot/recipes/run-dates', { params: { query: { dry_run: dryRun } } }))) as unknown as {
        dry_run: boolean;
        did: Did[];
      },
    onError: (e) => toast.error(e.message),
    onSettled: (_d, _e, dryRun) => {
      if (!dryRun) void settle();
    },
  });
  return { add, change, remove, checkDates };
}

/** A ready-made rule from what Allied's boards show, with the fact behind it and what it would act on today. */
export type Starter = {
  slug: string;
  why: string;
  saves: string;
  /** "recipients": someone must be named before it can be added. */
  needs?: string | null;
  recipe: RuleIn & { name: string; description: string };
  board_id: string | null;
  added: boolean;
  today?: { item_id: string; item: string; detail?: string | null }[];
};

export function useStarters(enabled: boolean) {
  return useQuery({
    queryKey: ['autopilot', 'starters'],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/autopilot/recipes/starters', { params: { query: { preview: true } } }))) as unknown as Starter[],
    enabled,
    staleTime: 5 * 60_000,
  });
}

/** Add a ready-made rule, with any changes (who gets it, for one). */
export function useAddStarter(boardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (v: { slug: string; overrides?: RuleIn }) =>
      ok(api.POST('/api/v1/autopilot/recipes/starters/{slug}', { params: { path: { slug: v.slug } }, body: v.overrides ?? {} })),
    onSuccess: () => toast.success('Rule added. It runs in the app, never in monday.'),
    onError: (e) => toast.error(e.message),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ['autopilot', 'starters'] });
      void client.invalidateQueries({ queryKey: automationsKey(boardId) });
    },
  });
}

/** Send rates per automation, for managers. */
export function useScorecard(enabled: boolean) {
  return useQuery({
    queryKey: ['autopilot', 'scorecard'],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/autopilot/scorecard', { params: { query: { days: 90 } } }))) as unknown as { automations: Score[] },
    enabled,
    staleTime: 5 * 60_000,
  });
}

export const READ_NOW_ROLE: Record<Role, 'OPERATOR' | 'ADMIN' | null> = {
  projects: 'OPERATOR',
  vendors: 'OPERATOR',
  rfqs: 'ADMIN',
  reference: null,
  doc: null,
};
