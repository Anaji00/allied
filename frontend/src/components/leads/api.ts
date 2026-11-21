import { authFetch } from '@/lib/api';

export const STAGES = ['Found', 'Qualified', 'Contacted', 'Replied', 'Quote sent', 'Won', 'Lost'] as const;
export type Stage = (typeof STAGES)[number];

export type Lead = {
  id: number;
  account_id?: number | null;
  contact_id?: number | null;
  name?: string | null;
  title?: string | null;
  company?: string | null;
  industry?: string | null;
  location?: string | null;
  email?: string | null;
  phone?: string | null;
  source?: string | null;
  source_detail?: string | null;
  status: Stage | string;
  owner?: string | null;
  reason?: string | null;
  score?: number | null;
  score_parts?: { label: string; points: number; max?: number }[];
  next_touch_at?: string | null;
  last_touch_at?: string | null;
  notes?: string | null;
  estimated_annual_spend?: number | null;
  do_not_contact?: boolean;
  bad_address?: boolean;
  sequence?: { name?: string | null; step?: number | null; paused?: boolean } | null;
  monday_item_id?: string | null;
  created_at?: string | null;
};
export type LeadList = { items: Lead[]; total: number; due_today: number };
export type Summary = {
  by_stage: Record<string, number>;
  due_today: number;
  replied_week: number;
  won: number;
  lost: number;
  win_rate?: number | null;
  pipeline_value?: number | null;
  by_source?: Record<string, number>;
};
export type Suggestion = {
  id: string;
  kind: string;
  account?: string | null;
  sentence: string;
  suggested_template?: string | null;
  spend?: number | null;
  email?: string | null;
  already_a_lead?: boolean;
};
export type Template = { title: string; subject: string; body: string };
export type LeadSettings = {
  industries: string[];
  regions: string[];
  titles: string[];
  min_spend: number;
  exclude: string[];
  follow_up_days: number;
  sequence_days: number[];
  daily_draft_limit: number;
  business_hours: number[];
  sequence_mode: 'off' | 'suggest' | 'auto' | string;
  signature: string;
  phone: string;
  postal_address: string;
  optout_line: string;
  default_owner: string;
  search_enabled: boolean;
  weights: Record<string, number>;
  templates: Record<string, Template>;
};
export type Dnc = { id: number; kind: string; value: string; reason?: string | null; added_by?: string | null; created_at?: string };
export type Draft = { lead_id: number; template: string; to: string; subject: string; body: string; action_id?: string | number | null; next_touch_at?: string | null };

/** One call to the leads routes. A failure carries the server's own plain sentence. */
export async function leads<T = unknown>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await authFetch(`/api/v1/leads${path}`, {
    method,
    cache: 'no-store',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const j = await res.json().catch(() => null);
    const d = j?.detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) && d[0]?.msg ? String(d[0].msg) : `That didn’t work (${res.status}).`);
  }
  return (await res.json().catch(() => ({}))) as T;
}

export const scoreTone = (n?: number | null) => ((n ?? 0) >= 70 ? 'ok' : (n ?? 0) >= 40 ? 'info' : 'neutral') as 'ok' | 'info' | 'neutral';
