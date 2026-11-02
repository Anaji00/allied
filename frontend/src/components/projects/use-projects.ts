'use client';

import { useQuery } from '@tanstack/react-query';
import type { Tone } from '@/components/data/tone-badge';
import { api, ok } from '@/lib/api/client';
import { plural } from '@/lib/format';

export type Submission = {
  id: number;
  version: number;
  status: 'SUBMITTED' | 'APPROVED' | 'CHANGES_REQUESTED' | string;
  note?: string | null;
  files: { name: string; size: number }[];
  submitted_by?: string | null;
  submitted_at?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
};

export type Milestone = {
  item_id: string;
  project_item_id: string;
  name: string;
  owner_emails?: string[];
  reviewer_emails?: string[];
  due_date?: string | null;
  status?: string | null;
  done?: boolean;
  needs_submission?: boolean;
  // on_hold: open but waiting on someone else (Paused or Sent for Review on monday); never chased.
  state: 'done' | 'waiting_review' | 'overdue' | 'due_soon' | 'on_track' | 'on_hold' | 'no_date';
  days: number | null;
  missing_docs: boolean;
  latest_submission: Submission | null;
  submissions: Submission[];
};

export type Project = {
  item_id: string;
  name: string;
  group_title?: string | null;
  owner_emails?: string[];
  due_date?: string | null;
  status?: string | null;
  done?: boolean;
  behind: number;
  overdue: boolean;
  milestones: Milestone[];
};

type LastSync = { at?: string; error?: string; result?: { skipped?: string } } | null;

export type ProjectsData = {
  today: string;
  counts: { overdue: number; due_soon: number; waiting_review: number; missing_docs: number };
  projects: Project[];
  last_sync?: LastSync;
};

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: async () => (await ok(api.GET('/api/v1/projects'))) as ProjectsData,
  });
}

/**
 * Leadership reviews milestones whose project has no owner. Only the alerts
 * API says who that is, so it is read from there (empty when unavailable).
 */
export function useLeadership() {
  return useQuery({
    queryKey: ['alerts', 'channels'],
    queryFn: async () =>
      (
        (await ok(api.GET('/api/v1/alerts', { params: { query: { open_only: true, limit: 1 } } }))) as {
          channels?: { leadership?: string[] };
        }
      ).channels?.leadership ?? [],
    staleTime: 10 * 60_000,
  });
}

export const SUBMISSION: Record<string, { label: string; tone: Tone }> = {
  SUBMITTED: { label: 'Waiting for review', tone: 'info' },
  APPROVED: { label: 'Approved', tone: 'ok' },
  CHANGES_REQUESTED: { label: 'Changes requested', tone: 'warn' },
};

const dueIn = (days: number | null) =>
  days == null ? 'Due soon' : days <= 0 ? 'Due today' : days === 1 ? 'Due tomorrow' : `Due in ${days} days`;

/** A milestone's state in plain words and a tone. */
export function milestoneState(m: Milestone): { label: string; tone: Tone } {
  switch (m.state) {
    case 'done':
      return { label: 'Done', tone: 'ok' };
    case 'waiting_review':
      return { label: 'Waiting for review', tone: 'info' };
    case 'on_hold':
      return { label: 'On hold', tone: 'neutral' };
    case 'overdue':
      return { label: m.days == null ? 'Late' : m.days >= 0 ? dueIn(m.days) : `${plural(-m.days, 'day')} late`, tone: 'bad' };
    case 'due_soon':
      return { label: dueIn(m.days), tone: 'warn' };
    case 'on_track':
      return { label: m.days == null ? 'On track' : dueIn(m.days), tone: 'neutral' };
    default:
      return { label: 'No date', tone: 'neutral' };
  }
}

const lower = (v?: string | null) => (v ?? '').trim().toLowerCase();
export const hasEmail = (list: string[] | undefined, email: string) => Boolean(email) && (list ?? []).some((e) => lower(e) === email);

/** Mirrors the backend's reviewers_for: milestone reviewers, else project owners, else leadership. */
export function reviewersOf(m: Milestone, p: Project, leadership: string[]) {
  if (m.reviewer_emails?.length) return m.reviewer_emails;
  if (p.owner_emails?.length) return p.owner_emails;
  return leadership;
}

export function waitingForMe(m: Milestone, p: Project, me: string, leadership: string[]) {
  const sub = m.latest_submission;
  return sub?.status === 'SUBMITTED' && hasEmail(reviewersOf(m, p, leadership), me) && lower(sub.submitted_by) !== me;
}

export const isBehind = (p: Project) => p.overdue || p.behind > 0;

/**
 * A board laid out by group (Allied's Engineering Team Board) has no project items: each
 * monday group stands in for a project and its items are the jobs. The backend gives such a
 * project the id "g{board}-{group}"; a real monday item id is a number.
 */
export const isGroup = (p: Project) => p.item_id.startsWith('g');

/** The first milestone still to do, which is what the project is waiting on. */
export const nextMilestone = (p: Project) => p.milestones.find((m) => m.state !== 'done');

export function syncText(last?: LastSync) {
  if (!last?.at) return 'Not read from monday yet';
  if (last.error) return `Reading monday failed: ${last.error}`;
  if (last.result?.skipped) return `monday wasn’t read: ${last.result.skipped}`;
  return null;
}

export function fileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
