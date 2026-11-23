'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Fold } from '@/components/data/fold';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime, localIso, moneyOrig } from '@/lib/format';
import { PaymentCopyButton } from './po-extras';
import type { Job } from './job-types';

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T | null> {
  const res = await authFetch(`/api/v1${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  if (res.status === 404 && method === 'GET') return null;
  const out = (await res.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (!res.ok)
    throw new Error(
      typeof (out as { detail?: string } | null)?.detail === 'string'
        ? (out as { detail: string }).detail
        : `That didn’t work (${res.status}).`,
    );
  return out;
}

/* ------------------------------------------------------------------ gates */

type GateState = {
  id?: number | string | null;
  status: 'NONE' | 'PENDING' | 'APPROVED' | 'REJECTED' | 'OVERRIDDEN' | 'REVOKED';
  status_word?: string;
  passed?: boolean;
  decided_by_name?: string | null;
  decided_at?: string | null;
  can_request?: boolean;
  can_approve?: boolean;
  can_override?: boolean;
  can_revoke?: boolean;
};
type Gates = {
  needs_approver_set?: boolean;
  approvers_set?: boolean;
  status: Record<string, GateState>;
  history?: { at?: string; gate?: number; status?: string; by?: string; note?: string; reason?: string }[];
};
const GATE = [
  ['1', 'Gate 1: the vendor’s shop drawing'],
  ['2', 'Gate 2: measurement photos'],
] as const;

/** The two checks before production: an engineer approves the vendor’s drawing, then the measurement photos. The server says who may do what. */
export function GatesCard({ job }: { job: Job }) {
  const client = useQueryClient();
  const jobId = typeof job.id === 'number' ? job.id : Number(job.id);
  const [note, setNote] = useState('');
  const [override, setOverride] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<{ id: number | string; gate: string } | null>(null);
  const [reason, setReason] = useState('');
  const q = useQuery({
    queryKey: ['gates', jobId],
    queryFn: () => call<Gates>(`/gates/job/${jobId}`),
    enabled: Number.isFinite(jobId),
    retry: false,
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ['gates', jobId] });
  const act = useMutation({
    mutationFn: async (a: {
      kind: 'request' | 'approve' | 'reject' | 'override' | 'revoke';
      gate: string;
      id?: number | string | null;
    }) => {
      if (a.kind === 'request') return call('/gates/request', 'POST', { job_id: jobId, gate: Number(a.gate) });
      if (a.kind === 'override') return call('/gates/override', 'POST', { job_id: jobId, gate: Number(a.gate), reason: reason.trim() });
      if (a.kind === 'revoke') return call(`/gates/${a.id}/revoke`, 'POST', { reason: reason.trim() });
      return call(`/gates/${a.id}/decide`, 'POST', { approve: a.kind === 'approve', note: note.trim() });
    },
    onSuccess: () => {
      toast.success('Saved');
      setOverride(null);
      setRevoking(null);
      setReason('');
      setNote('');
      refresh();
    },
    onError: (e) => toast.error('That did not go through', { description: e.message }),
  });
  if (!q.data) return null;
  const g = q.data;
  const word = (s?: GateState) => s?.status_word ?? 'Not asked yet';
  return (
    <Fold id="job-gates" title="Production gates" summary={GATE.map(([n]) => `${n}: ${word(g.status[n])}`).join(' · ')} defaultOpen>
      <div className="grid gap-3 text-[13px]">
        {(g.needs_approver_set ?? g.approvers_set === false) && (
          <p role="alert" className="rounded-md border border-warn bg-warn-soft px-3 py-2 text-warn">
            No engineers are named to approve yet, so nobody can approve a gate. An administrator names the approving engineers in the
            server settings (ENGINEER_APPROVERS).
          </p>
        )}
        {GATE.map(([n, title]) => {
          const s = g.status[n] ?? { status: 'NONE' as const };
          return (
            <div key={n} className="grid gap-1.5 rounded-md border p-3">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                {title}
                <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">{word(s)}</span>
              </p>
              {s.decided_by_name && (
                <p className="text-xs text-muted-foreground">
                  By {s.decided_by_name}
                  {s.decided_at ? `, ${dateTime(s.decided_at)}` : ''}.
                </p>
              )}
              {s.can_approve && s.id != null && (
                <div className="flex flex-wrap items-center gap-2">
                  <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="A note (optional)" className="h-8 w-64" />
                  <Button size="sm" disabled={act.isPending} onClick={() => act.mutate({ kind: 'approve', gate: n, id: s.id })}>
                    Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={act.isPending}
                    onClick={() => act.mutate({ kind: 'reject', gate: n, id: s.id })}
                  >
                    Send back
                  </Button>
                </div>
              )}
              {s.status === 'PENDING' && !s.can_approve && (
                <p className="text-xs text-muted-foreground">Waiting for a named engineer to approve it.</p>
              )}
              {s.can_request && (
                <Button
                  size="sm"
                  variant="outline"
                  className="justify-self-start"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ kind: 'request', gate: n })}
                >
                  Ask the engineers to check this
                </Button>
              )}
              {s.can_revoke && s.id != null && (
                <button
                  type="button"
                  className="justify-self-start text-xs text-muted-foreground underline underline-offset-2"
                  onClick={() => {
                    setReason('');
                    setRevoking({ id: s.id!, gate: n });
                  }}
                >
                  Take this approval back (manager)
                </button>
              )}
              {s.can_override && (
                <button
                  type="button"
                  className="justify-self-start text-xs text-muted-foreground underline underline-offset-2"
                  onClick={() => {
                    setReason('');
                    setOverride(n);
                  }}
                >
                  Pass this gate without an engineer (manager)
                </button>
              )}
            </div>
          );
        })}
        {(g.history ?? []).length > 0 && (
          <ul className="grid gap-0.5 text-xs text-muted-foreground">
            {g.history!.slice(0, 5).map((h, i) => (
              <li key={i}>
                {h.at ? dateTime(h.at) : ''}: gate {h.gate} {h.status?.toLowerCase()}
                {h.by ? `, by ${h.by}` : ''}
                {h.note || h.reason ? `. ${h.note ?? h.reason}` : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
      {revoking && (
        <Dialog open onOpenChange={(o) => !o && setRevoking(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Take back the approval for gate {revoking.gate}?</DialogTitle>
              <DialogDescription>
                Work that depends on it is held again. This is recorded with your name and the reason (at least 8 characters).
              </DialogDescription>
            </DialogHeader>
            <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="The reason" />
            <DialogFooter>
              <Button variant="ghost" onClick={() => setRevoking(null)}>
                Cancel
              </Button>
              <Button
                disabled={reason.trim().length < 8 || act.isPending}
                onClick={() => act.mutate({ kind: 'revoke', gate: revoking.gate, id: revoking.id })}
              >
                Take it back
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {override && (
        <Dialog open onOpenChange={(o) => !o && setOverride(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Pass gate {override} without an engineer?</DialogTitle>
              <DialogDescription>This is recorded with your name and the reason. Say why, in at least 8 characters.</DialogDescription>
            </DialogHeader>
            <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="The reason" />
            <DialogFooter>
              <Button variant="ghost" onClick={() => setOverride(null)}>
                Cancel
              </Button>
              <Button disabled={reason.trim().length < 8 || act.isPending} onClick={() => act.mutate({ kind: 'override', gate: override })}>
                Pass the gate
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Fold>
  );
}

/* --------------------------------------------------------------- payments */

type Tranche = {
  id: number;
  seq: number;
  label: string;
  percent?: number | string;
  amount: string;
  trigger: string;
  status: 'PENDING' | 'DUE' | 'PAID';
  paid_at?: string | null;
};
type Schedule = {
  po_id: number;
  vendor?: string;
  currency?: string;
  total?: string;
  status?: string;
  paid?: string;
  remaining?: string;
  terms_code?: string;
  tranches: Tranche[];
};
const TRIGGER: Record<string, string> = {
  PO_ISSUED: 'when the PO is issued',
  PROFORMA: 'on the proforma invoice',
  GATE2_APPROVED: 'when Gate 2 is approved',
  DELIVERY_CONFIRMED: 'when delivery is confirmed',
  CUSTOM: 'on a date you set',
};

function RecordPayment({ tranche, currency, onClose }: { tranche: Tranche; currency?: string; onClose: () => void }) {
  const client = useQueryClient();
  const [form, setForm] = useState({
    paid_on: localIso(new Date()),
    amount: '',
    method: 'WIRE',
    reference: '',
    draft_remittance: true,
  });
  const save = useMutation({
    mutationFn: () =>
      call<{ duplicate?: boolean; effects?: Record<string, unknown> }>(`/payments/tranche/${tranche.id}/record`, 'POST', {
        paid_on: form.paid_on,
        amount: form.amount.trim() || undefined,
        method: form.method,
        reference: form.reference.trim(),
        draft_remittance: form.draft_remittance,
        idem_key: `${tranche.id}-${form.paid_on}-${form.amount.trim() || tranche.amount}-${form.reference.trim()}`,
      }),
    onSuccess: (out) => {
      toast.success(out?.duplicate ? 'That payment was already recorded.' : 'Payment recorded. Everything listed was updated.');
      void client.invalidateQueries({ queryKey: ['payments'] });
      onClose();
    },
    onError: (e) => toast.error('The payment was not recorded', { description: e.message }),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Record a payment: {tranche.label}</DialogTitle>
          <DialogDescription>You enter it once. The app then updates everything below, so nothing is typed twice.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <Field label="Date paid">
            <Input type="date" value={form.paid_on} onChange={(e) => setForm({ ...form, paid_on: e.target.value })} />
          </Field>
          <Field label={`Amount (blank = ${moneyOrig(tranche.amount, currency)})`}>
            <Input value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} inputMode="decimal" />
          </Field>
          <Field label="How">
            <NativeSelect value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
              {[
                ['WIRE', 'Wire'],
                ['ACH', 'ACH'],
                ['CHECK', 'Check'],
                ['CARD', 'Card'],
                ['OTHER', 'Other'],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Reference (wire number, check number)">
            <Input value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />
          </Field>
          <label className="flex items-center gap-2 text-[13px]">
            <input
              type="checkbox"
              className="size-4"
              checked={form.draft_remittance}
              onChange={(e) => setForm({ ...form, draft_remittance: e.target.checked })}
            />
            Write a remittance email to the vendor as a draft
          </label>
          <ul className="list-disc pl-5 text-xs text-muted-foreground">
            <li>The job packet and sales sheet</li>
            <li>monday, as a change waiting for a manager to send</li>
            <li>The vendor’s ledger</li>
            {form.draft_remittance && <li>A remittance draft for you to approve (not sent)</li>}
          </ul>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!form.paid_on || !form.reference.trim() || save.isPending} onClick={() => save.mutate()}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Record the payment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const TERMS = ['30/50/20', '30/70', '50/50', '100'] as const;

function PoPayments({ poId, label }: { poId: number; label: string }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const setTerms = useMutation({
    mutationFn: async (terms: string) => call(`/payments/po/${poId}/schedule`, 'PUT', { terms }),
    onSuccess: () => {
      toast.success('Payment steps saved');
      void client.invalidateQueries({ queryKey: ['payments', 'po', poId] });
    },
    onError: (e) => toast.error('The steps were not saved', { description: e.message }),
  });
  const [paying, setPaying] = useState<Tranche | null>(null);
  const q = useQuery({ queryKey: ['payments', 'po', poId], queryFn: () => call<Schedule>(`/payments/po/${poId}`), retry: false });
  if (!q.data) return null;
  const s = q.data;
  return (
    <div className="grid gap-2 rounded-md border p-3">
      <p className="text-[13px] font-medium">
        {label}
        <span className="font-normal text-muted-foreground">
          {' '}
          · {s.terms_code ?? 'terms not set'} · paid {moneyOrig(s.paid ?? 0, s.currency)} of {moneyOrig(s.total ?? 0, s.currency)},{' '}
          {moneyOrig(s.remaining ?? 0, s.currency)} left
        </span>
      </p>
      {hasRole('MANAGER') && (
        <label className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Payment steps
          <NativeSelect
            aria-label="Payment steps"
            className="h-7 w-auto text-xs"
            value={TERMS.includes((s.terms_code ?? '') as (typeof TERMS)[number]) ? (s.terms_code ?? '') : 'custom'}
            onChange={(e) => {
              const v =
                e.target.value === 'custom'
                  ? window.prompt('Type the steps as percentages that add to 100, for example 40/60')
                  : e.target.value;
              if (v && window.confirm('This replaces the payment steps for this purchase order. Continue?')) setTerms.mutate(v.trim());
            }}
          >
            {TERMS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
            <option value="custom">Other…</option>
          </NativeSelect>
        </label>
      )}
      <ul className="grid divide-y text-[13px]">
        {s.tranches.map((t) => (
          <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
            <span className="min-w-28 font-medium">{t.label}</span>
            <span className="tabular-nums">{moneyOrig(t.amount, s.currency)}</span>
            <span className="text-xs text-muted-foreground">{TRIGGER[t.trigger] ?? t.trigger}</span>
            <span
              className={`ml-auto rounded-full px-2 py-0.5 text-[11px] ${t.status === 'PAID' ? 'bg-ok-soft text-ok' : t.status === 'DUE' ? 'bg-warn-soft text-warn' : 'bg-muted text-muted-foreground'}`}
            >
              {t.status === 'PAID' ? (t.paid_at ? `Paid ${dateTime(t.paid_at)}` : 'Paid') : t.status === 'DUE' ? 'Due now' : 'Not due yet'}
            </span>
            {t.status === 'DUE' && hasRole('MANAGER') && (
              <Button size="xs" onClick={() => setPaying(t)}>
                Record a payment
              </Button>
            )}
            {t.status === 'DUE' && hasRole('MANAGER') && <PaymentCopyButton trancheId={t.id} />}
          </li>
        ))}
      </ul>
      {paying && <RecordPayment tranche={paying} currency={s.currency} onClose={() => setPaying(null)} />}
    </div>
  );
}

async function downloadLedger() {
  const res = await authFetch('/api/v1/payments/ledger.xlsx');
  if (!res.ok) return toast.error('The ledger could not be made');
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = 'vendor-payments-ledger.xlsx';
  a.click();
  URL.revokeObjectURL(url);
}

/** What is owed to each vendor on this job, in steps (30/50/20 and so on), and one button to record that a step was paid. */
export function PaymentsCard({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const pos = (job.purchase_orders ?? []).filter((p) => Number.isFinite(Number(p.id)));
  // What is owed to vendors is for managers only.
  if (pos.length === 0 || !hasRole('MANAGER')) return null;
  return (
    <Fold
      id="job-payments"
      title="Payments to vendors"
      summary={`${pos.length} purchase order${pos.length === 1 ? '' : 's'}`}
      defaultOpen={false}
    >
      <div className="grid gap-3">
        <Button size="sm" variant="outline" className="justify-self-start" onClick={() => void downloadLedger()}>
          Download the payments ledger (Excel)
        </Button>
        {pos.map((p) => (
          <PoPayments key={String(p.id)} poId={Number(p.id)} label={`${p.vendor}, PO ${p.po_number ?? p.id}`} />
        ))}
      </div>
    </Fold>
  );
}
