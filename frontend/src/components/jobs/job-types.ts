import { authFetch } from '@/lib/api';

export const STAGES: [string, string][] = [
  ['RFQ', 'Quote request'],
  ['RESEARCH', 'Research'],
  ['ENGINEERING', 'Engineering'],
  ['DESIGN_APPROVED', 'Design approved'],
  ['VENDOR_QUOTE', 'Vendor price in'],
  ['CUSTOMER_QUOTED', 'Customer quoted'],
  ['CUSTOMER_ORDERED', 'Customer ordered'],
  ['PO_ISSUED', 'Purchase order issued'],
  ['SO_CREATED', 'Sales order made'],
  ['SHIPPED', 'Shipped'],
  ['CLOSED', 'Closed'],
];
export const stageLabel = (s: string) => STAGES.find(([k]) => k === s)?.[1] ?? s;

export type Waiting = { what: string; who?: string | null; since?: string | null; action_id?: string | null };
export type JobRow = {
  id: number | string;
  ref: string;
  title: string;
  customer?: string | null;
  stage: string;
  done?: string[];
  waiting?: Waiting[];
  ball?: { with?: string | null; owner?: string | null; since?: string | null } | null;
  updated_at?: string | null;
};
export type Job = JobRow & {
  /** Up to three plain next steps from the server (ask_vendor, approve_design, approve_po, make_sales_order, ship). */
  /** Warnings only; they block nothing: an expired quote, an order total that differs from the quote, a thin margin. */
  checks?: { key: string; level: 'warn' | 'info'; text: string }[];
  /** One entry per stage, in order, with the date it was reached and, on the first unfinished one, why it is stuck. */
  stages?: { stage: string; done: boolean; entered_at?: string | null; by?: string | null; blocked_reason?: string | null }[];
  next_steps?: { key: string; text: string }[];
  customer_reference?: string | null;
  rfq?: { id: number; number: string; status?: string } | null;
  research?: { id: number | string; title: string; url?: string; note?: string; at?: string; source?: string }[];
  thread?: { entity: string; id: number | string } | null;
  design?: {
    approved_by?: string | null;
    approved_at?: string | null;
    files?: { id: number | string; name: string; rev?: string; size?: number; at?: string; url?: string }[];
  } | null;
  purchase_orders?: { id: number | string; po_number: string; vendor: string; status: string; pdf_url?: string }[];
  sales_order?: { number?: string | null; customer_reference?: string | null } | null;
  shipments?: Record<string, unknown>[];
  drafts?: { id: string; kind: string; status: string; summary: string }[];
};

/** A plain request to the jobs routes; a missing route (404) or a server fault comes back as null so the screen can say "not available yet". */
export async function jobsCall<T>(path: string, method = 'GET', body?: unknown): Promise<T | null> {
  const res = await authFetch(`/api/v1/jobs${path}`, {
    method,
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (method === 'GET' && res.status === 404) return null;
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
  }
  return (await res.json()) as T;
}

/** A protected file (a PDF) fetched with the sign-in and saved under the server's name. */
export async function downloadBlob(path: string, fallbackName: string) {
  const res = await authFetch(path, { cache: 'no-store' });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `Couldn’t download it (${res.status}).`);
  }
  const named = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get('content-disposition') || '')?.[1];
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = named ? decodeURIComponent(named) : fallbackName;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
