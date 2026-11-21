'use client';

import { CompareJobFiles } from './compare-job-files';
import { ActionBar } from '@/components/data/action-bar';
import { CustomsPapers } from './customs-papers';
import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, Loader2, Plus, Trash2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime, money, words } from '@/lib/format';
import { downloadBlob, jobsCall, type Job } from './job-types';

type Line = {
  quantity: number | string;
  description: string;
  unit_cost: number | string;
  revision?: string | null;
  // For goods coming from overseas. All optional; a blank stays blank on the paper.
  hts_code?: string | null;
  origin?: string | null;
  uom?: string | null;
  net_kg?: number | string | null;
  cert_required?: boolean | null;
};

/** The server names the quantity "qty" and adds an "amount"; the screen works with quantity, description and unit cost. */
const norm = (l: Partial<Line> & { qty?: number | string }): Line => ({
  quantity: l.quantity ?? l.qty ?? 0,
  description: l.description ?? '',
  unit_cost: l.unit_cost ?? 0,
  revision: l.revision ?? '',
  hts_code: l.hts_code ?? '',
  origin: l.origin ?? '',
  uom: l.uom ?? '',
  net_kg: l.net_kg ?? '',
  cert_required: l.cert_required ?? false,
});
export type PO = {
  id: number | string;
  job_id?: number | string;
  po_number?: string | null;
  vendor: string;
  vendor_email?: string | null;
  lines?: Line[];
  /** Line numbers with no drawing revision. A soft warning only; it blocks nothing. */
  missing_revision?: number[];
  /** A draft whose total is over the approval limit: only an administrator can approve it. */
  needs_admin?: boolean;
  approved_by_creator?: boolean;
  /** Per line: the drawing revision sent, the one the vendor confirmed, and whether they differ. */
  revisions?: {
    line: number;
    revision_sent?: string | null;
    revision_acked?: string | null;
    acked_by?: string | null;
    mismatch?: boolean;
  }[];
  cancel_reason?: string | null;
  terms?: string | null;
  incoterm?: string | null;
  incoterm_place?: string | null;
  ship_via?: string | null;
  good_thru?: string | null;
  status: 'DRAFT' | 'APPROVED' | 'SENT' | 'ACKNOWLEDGED' | string;
  approved_by?: string | null;
  approved_at?: string | null;
  sent_at?: string | null;
  action_id?: string | null;
};

const STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: 'Draft', tone: 'neutral' },
  APPROVED: { label: 'Approved, not sent', tone: 'info' },
  SENDING: { label: 'Sending…', tone: 'info' },
  CANCELLED: { label: 'Cancelled', tone: 'neutral' },
  SENT: { label: 'Sent to the vendor', tone: 'ok' },
  ACKNOWLEDGED: { label: 'Vendor confirmed', tone: 'ok' },
};
const total = (lines?: Line[]) => (lines ?? []).reduce((n, l) => n + Number(l.quantity || 0) * Number(l.unit_cost || 0), 0);

async function poCall<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await authFetch(`/api/v1/purchase-orders${path}`, {
    method,
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
  }
  return (await res.json().catch(() => ({}))) as T;
}

/** Purchase orders to the vendors on this job: draft, check, approve, see the PDF, and queue the email (which still waits for a person's OK). */
export function PurchaseOrders({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const ref = job.ref;
  const [adding, setAdding] = useState('');
  const [comparing, setComparing] = useState(false);
  const [editing, setEditing] = useState<PO | null>(null);
  const [cancelling, setCancelling] = useState<PO | null>(null);
  const [acking, setAcking] = useState<{ po: PO; line: number; sent: string } | null>(null);
  const designApproved = Boolean(job.design?.approved_at);
  const list = useQuery({
    queryKey: ['jobs', ref, 'pos'],
    queryFn: () => jobsCall<PO[]>(`/${encodeURIComponent(ref)}/purchase-orders`),
    retry: false,
  });
  // The job's own summary until the full routes answer.
  const pos: PO[] = (list.data ?? (job.purchase_orders as PO[] | undefined) ?? []).map((p) => ({ ...p, lines: p.lines?.map(norm) }));
  const full = list.data != null;
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['jobs'] });
  };

  const act = useMutation({
    mutationFn: async ({ po, what }: { po: PO; what: 'approve' | 'email-draft' | 'acknowledge' }) => ({
      what,
      r: await poCall<{ action_id?: string }>(`/${po.id}/${what}`, 'POST'),
    }),
    onSuccess: ({ what }) => {
      if (what === 'approve') toast.success('Approved. It has its number now.');
      else if (what === 'email-draft')
        toast.success('Email drafted for you to check', { description: 'It goes out only after you approve it in Approvals.' });
      else toast.success('Marked as confirmed by the vendor');
      refresh();
    },
    onError: (e) => toast.error('That didn’t go through', { description: e.message }),
  });
  const create = useMutation({
    mutationFn: () => jobsCall<PO>(`/${encodeURIComponent(ref)}/purchase-orders`, 'POST', { vendor: adding.trim() }),
    onSuccess: () => {
      toast.success('Purchase order drafted');
      setAdding('');
      refresh();
    },
    onError: (e) => toast.error('The purchase order wasn’t drafted', { description: e.message }),
  });
  const pdf = useMutation({
    mutationFn: (po: PO) => downloadBlob(`/api/v1/purchase-orders/${po.id}/pdf`, `PO-${po.po_number || po.id}.pdf`),
    onError: (e) => toast.error('Couldn’t download the PDF', { description: e.message }),
  });

  return (
    <section className="grid content-start gap-3 rounded-lg border bg-card p-4">
      <h3 className="text-xs font-medium text-muted-foreground">Purchase orders to vendors</h3>
      {pos.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No purchase order has been drafted yet. One is drafted for each vendor when the quote request goes out for prices.
        </p>
      )}
      {pos.map((p) => {
        const st = STATUS[p.status] ?? { label: words(p.status), tone: 'neutral' as Tone };
        return (
          <article key={p.id} className="grid gap-2 rounded-md border p-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{p.po_number || 'Number on approval'}</span>
              <span className="font-medium">{p.vendor}</span>
              <ToneBadge tone={st.tone}>{st.label}</ToneBadge>
              {full && p.lines && p.lines.length > 0 && <span className="ml-auto tabular-nums">{money(total(p.lines))}</span>}
            </div>
            {full && p.lines && p.lines.length > 0 && (
              <table className="w-full text-xs">
                <thead className="text-left text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-2 font-medium">Qty</th>
                    <th className="py-1 pr-2 font-medium">What</th>
                    <th className="py-1 text-right font-medium">Unit cost</th>
                  </tr>
                </thead>
                <tbody>
                  {p.lines.map((l, i) => (
                    <tr key={i} className="border-t">
                      <td className="py-1 pr-2 tabular-nums">{l.quantity}</td>
                      <td className="py-1 pr-2">
                        {l.description}
                        {l.revision ? <span className="ml-1.5 text-muted-foreground">rev {l.revision}</span> : null}
                        {(() => {
                          const rv = p.revisions?.find((x) => x.line === i + 1);
                          if (!rv?.revision_sent) return null;
                          return (
                            <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs">
                              {rv.mismatch ? (
                                <span className="text-warn">
                                  Vendor confirmed rev {rv.revision_acked}, but rev {rv.revision_sent} was sent
                                </span>
                              ) : rv.revision_acked ? (
                                <span className="text-ok">Vendor confirmed rev {rv.revision_acked}</span>
                              ) : (
                                <span className="text-muted-foreground">Rev {rv.revision_sent} sent; no reply yet</span>
                              )}
                              {hasRole('OPERATOR') && !rv.revision_acked && (
                                <button
                                  type="button"
                                  className="underline"
                                  onClick={() => setAcking({ po: p, line: i + 1, sent: rv.revision_sent ?? '' })}
                                >
                                  Record their reply
                                </button>
                              )}
                            </span>
                          );
                        })()}
                      </td>
                      <td className="py-1 text-right tabular-nums">{money(l.unit_cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {full && hasRole('OPERATOR') && ['APPROVED', 'SENT', 'ACKNOWLEDGED'].includes(p.status) && <CustomsPapers poId={p.id} />}
            {p.status === 'CANCELLED' && p.cancel_reason && <p className="text-xs text-muted-foreground">Cancelled: {p.cancel_reason}</p>}
            {(p.missing_revision?.length ?? 0) > 0 && (
              <p className="text-xs text-warn">
                No drawing revision on line{p.missing_revision!.length === 1 ? '' : 's'} {p.missing_revision!.join(', ')}. It can still go
                out; the vendor just won’t see which revision to make.
              </p>
            )}
            {(p.ship_via || p.good_thru || p.terms || p.vendor_email) && (
              <p className="text-xs text-muted-foreground">
                {[
                  p.vendor_email && `To ${p.vendor_email}`,
                  p.ship_via && `Ship via ${p.ship_via}`,
                  p.terms && `Terms ${p.terms}`,
                  p.good_thru && `Good through ${p.good_thru}`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            )}
            {p.approved_at && (
              <p className="text-xs text-muted-foreground">
                Approved by {p.approved_by}
                {p.approved_by_creator ? ' (who also made it)' : ''} · {dateTime(p.approved_at)}
                {p.sent_at ? ` · sent ${dateTime(p.sent_at)}` : ''}
              </p>
            )}
            {p.status === 'DRAFT' && !designApproved && (
              <p className="text-xs text-warn">It can’t be approved until a manager approves the design.</p>
            )}
            {p.status === 'APPROVED' && p.action_id && (
              <p className="text-xs">
                The email is waiting for your OK.{' '}
                <Link href="/inbox?show=approvals" className="underline">
                  Open Approvals
                </Link>
              </p>
            )}
            {full && (
              <ActionBar
                server={{ kind: 'po', ref: String(p.id) }}
                subject={`PO ${p.po_number ?? p.id} to ${p.vendor}`}
                text={[
                  `PO ${p.po_number ?? p.id} to ${p.vendor}`,
                  `Status: ${(STATUS[p.status] ?? { label: words(p.status) }).label}`,
                ].join(String.fromCharCode(10))}
                onDownload={() => pdf.mutate(p)}
                downloadLabel="PDF"
                more={[{ label: 'Compare drawings', onClick: () => setComparing(true) }]}
              />
            )}
            {full && (
              <div className="flex flex-wrap gap-1.5">
                {p.status !== 'SENDING' && p.status !== 'CANCELLED' && hasRole('OPERATOR') && (
                  <Button size="xs" variant="outline" onClick={() => setEditing(p)}>
                    {p.status === 'SENT' || p.status === 'ACKNOWLEDGED' ? 'Customs details' : 'Edit'}
                  </Button>
                )}
                {['DRAFT', 'APPROVED', 'SENT', 'ACKNOWLEDGED'].includes(p.status) && hasRole('MANAGER') && (
                  <Button size="xs" variant="ghost" onClick={() => setCancelling(p)}>
                    Cancel order
                  </Button>
                )}
                {p.status === 'SENDING' && hasRole('MANAGER') && (
                  <span className="inline-flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    Stuck sending. Did the email go out?
                    <Button
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        if (window.confirm('Mark this purchase order as sent? Only do this if you saw the email arrive.'))
                          void poCall(`/${p.id}/resolve-send`, 'POST', { sent: true })
                            .then(refresh)
                            .catch((e: Error) => toast.error('That didn’t save', { description: e.message }));
                      }}
                    >
                      Yes, it went
                    </Button>
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() =>
                        void poCall(`/${p.id}/resolve-send`, 'POST', { sent: false })
                          .then(refresh)
                          .catch((e: Error) => toast.error('That didn’t save', { description: e.message }))
                      }
                    >
                      No, send again
                    </Button>
                  </span>
                )}
                {p.status === 'DRAFT' && hasRole('MANAGER') && p.needs_admin && !hasRole('ADMIN') && (
                  <>
                    <Button size="xs" disabled title="Above the approval limit">
                      Approve
                    </Button>
                    <span className="text-xs text-warn">Above the limit: an administrator approves this. Ask one to do it.</span>
                  </>
                )}
                {p.status === 'DRAFT' && hasRole('MANAGER') && !(p.needs_admin && !hasRole('ADMIN')) && (
                  <Button
                    size="xs"
                    disabled={act.isPending}
                    onClick={() => {
                      if (window.confirm(`Approve this purchase order to ${p.vendor}? It gets its number now.`))
                        act.mutate({ po: p, what: 'approve' });
                    }}
                  >
                    Approve
                  </Button>
                )}
                {p.status === 'APPROVED' && !p.action_id && hasRole('OPERATOR') && (
                  <Button size="xs" disabled={act.isPending} onClick={() => act.mutate({ po: p, what: 'email-draft' })}>
                    Write the email to the vendor
                  </Button>
                )}
                {p.status === 'SENT' && hasRole('OPERATOR') && (
                  <Button size="xs" variant="outline" disabled={act.isPending} onClick={() => act.mutate({ po: p, what: 'acknowledge' })}>
                    Vendor confirmed
                  </Button>
                )}
                <Button size="xs" variant="ghost" disabled={pdf.isPending} onClick={() => pdf.mutate(p)}>
                  {pdf.isPending ? <Loader2 className="animate-spin" /> : <Download />} PDF
                </Button>
              </div>
            )}
          </article>
        );
      })}
      {full && hasRole('OPERATOR') && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (adding.trim()) create.mutate();
          }}
        >
          <label className="grid gap-1 text-xs">
            <span className="font-medium">Add a purchase order</span>
            <Input className="w-56" placeholder="Vendor name" value={adding} onChange={(e) => setAdding(e.target.value)} />
          </label>
          <Button type="submit" size="sm" variant="outline" disabled={!adding.trim() || create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Draft it
          </Button>
        </form>
      )}
      {editing && <EditPO po={editing} onClose={() => setEditing(null)} onSaved={refresh} />}
      {acking && <AckRevision {...acking} onClose={() => setAcking(null)} onDone={refresh} />}
      {cancelling && <CancelPO po={cancelling} onClose={() => setCancelling(null)} onDone={refresh} />}
    </section>
  );
}

function EditPO({ po, onClose, onSaved }: { po: PO; onClose: () => void; onSaved: () => void }) {
  const [lines, setLines] = useState<Line[]>(
    po.lines?.length ? po.lines.map((l) => ({ ...l })) : [{ quantity: 1, description: '', unit_cost: 0 }],
  );
  const [terms, setTerms] = useState(po.terms ?? '');
  const [shipVia, setShipVia] = useState(po.ship_via ?? '');
  const [goodThru, setGoodThru] = useState(po.good_thru ?? '');
  const [email, setEmail] = useState(po.vendor_email ?? '');
  const [incoterm, setIncoterm] = useState(po.incoterm ?? '');
  const [incotermPlace, setIncotermPlace] = useState(po.incoterm_place ?? '');
  const setLine = (i: number, patch: Partial<Line>) => setLines((cur) => cur.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const sent = po.status === 'SENT' || po.status === 'ACKNOWLEDGED';
  const customsBody = () => ({
    lines: lines.map((l, i) => ({
      line: i + 1,
      hts_code: l.hts_code?.trim() ?? '',
      origin: l.origin?.trim() ?? '',
      uom: l.uom?.trim() ?? '',
      net_kg: String(l.net_kg ?? '').trim() ? Number(l.net_kg) || 0 : null,
      cert_required: Boolean(l.cert_required),
    })),
    incoterm: incoterm.trim(),
    incoterm_place: incotermPlace.trim(),
  });
  const save = useMutation({
    mutationFn: async () => {
      // After approval the lines are frozen: the tariff and certificate details go through their own call, and nothing is resent.
      if (po.status !== 'DRAFT') {
        if (!sent) await poCall(`/${po.id}`, 'PATCH', { terms, ship_via: shipVia, good_thru: goodThru, vendor_email: email });
        return poCall(`/${po.id}/customs`, 'PATCH', customsBody());
      }
      return poCall(`/${po.id}`, 'PATCH', {
        // The lines can only change while the order is a draft; after approval only the terms and the vendor details can.
        ...(po.status === 'DRAFT'
          ? {
              lines: lines
                .filter((l) => l.description.trim())
                .map((l) => ({
                  quantity: Number(l.quantity) || 0,
                  description: l.description.trim(),
                  unit_cost: Number(l.unit_cost) || 0,
                  ...(l.revision?.trim() ? { revision: l.revision.trim() } : {}),
                  ...(l.hts_code?.trim() ? { hts_code: l.hts_code.trim() } : {}),
                  ...(l.origin?.trim() ? { origin: l.origin.trim() } : {}),
                  ...(l.uom?.trim() ? { uom: l.uom.trim() } : {}),
                  ...(String(l.net_kg ?? '').trim() ? { net_kg: Number(l.net_kg) || 0 } : {}),
                  ...(l.cert_required ? { cert_required: true } : {}),
                })),
            }
          : {}),
        terms,
        ship_via: shipVia,
        good_thru: goodThru,
        vendor_email: email,
        ...(incoterm.trim() ? { incoterm: incoterm.trim() } : {}),
        ...(incotermPlace.trim() ? { incoterm_place: incotermPlace.trim() } : {}),
      });
    },
    onSuccess: () => {
      toast.success('Purchase order saved');
      onSaved();
      onClose();
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Purchase order to {po.vendor}</DialogTitle>
          <DialogDescription>Check the lines before approving. Once it is sent it can’t be changed.</DialogDescription>
        </DialogHeader>
        <form
          id="po-edit"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <fieldset disabled={po.status !== 'DRAFT'} className="m-0 grid min-w-0 gap-2 border-0 p-0 disabled:opacity-70">
            <div
              className="grid grid-cols-[4.5rem_minmax(0,1fr)_5rem_6.5rem_auto] gap-2 text-xs font-medium text-muted-foreground"
              aria-hidden
            >
              <span>How many</span>
              <span>What to make</span>
              <span>Drawing rev</span>
              <span>Cost each</span>
              <span />
            </div>
            {lines.map((l, i) => (
              <div key={i} className="grid grid-cols-[4.5rem_minmax(0,1fr)_5rem_6.5rem_auto] items-center gap-2">
                <Input
                  aria-label="Quantity"
                  inputMode="decimal"
                  value={String(l.quantity)}
                  onChange={(e) => setLine(i, { quantity: e.target.value.replace(/[^\d.]/g, '') })}
                />
                <Input
                  aria-label="What"
                  placeholder="What to make"
                  value={l.description}
                  onChange={(e) => setLine(i, { description: e.target.value })}
                />
                <Input
                  aria-label="Drawing revision"
                  placeholder="Rev"
                  maxLength={20}
                  value={l.revision ?? ''}
                  onChange={(e) => setLine(i, { revision: e.target.value })}
                />
                <Input
                  aria-label="Unit cost"
                  inputMode="decimal"
                  value={String(l.unit_cost)}
                  onChange={(e) => setLine(i, { unit_cost: e.target.value.replace(/[^\d.]/g, '') })}
                />
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label="Remove this line"
                  onClick={() => setLines((cur) => cur.filter((_, j) => j !== i))}
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="justify-self-start"
              onClick={() => setLines((cur) => [...cur, { quantity: 1, description: '', unit_cost: 0 }])}
            >
              <Plus /> Add a line
            </Button>
            {po.status !== 'DRAFT' && <p className="text-xs text-muted-foreground">The lines are locked once the order is approved.</p>}
          </fieldset>
          <div className={sent ? 'hidden' : 'grid gap-3 sm:grid-cols-2'}>
            <Field label="Terms">
              <Input value={terms} onChange={(e) => setTerms(e.target.value)} placeholder="e.g. Net 30" />
            </Field>
            <Field label="Ship via">
              <Input value={shipVia} onChange={(e) => setShipVia(e.target.value)} placeholder="e.g. Sea freight" />
            </Field>
            <Field label="Good through">
              <Input type="date" value={goodThru} onChange={(e) => setGoodThru(e.target.value)} />
            </Field>
            <Field label="Vendor’s email">
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
          </div>
          <details className="rounded-md border px-3 py-2" open={sent}>
            <summary className="cursor-pointer text-sm text-muted-foreground">Overseas shipping details (optional)</summary>
            <div className="mt-3 grid gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Delivery terms">
                  <Input value={incoterm} onChange={(e) => setIncoterm(e.target.value)} placeholder="e.g. FOB" maxLength={10} />
                </Field>
                <Field label="Delivery place">
                  <Input value={incotermPlace} onChange={(e) => setIncotermPlace(e.target.value)} placeholder="e.g. Shanghai" />
                </Field>
              </div>
              {['DRAFT', 'APPROVED', 'SENT', 'ACKNOWLEDGED'].includes(po.status) &&
                lines.map((l, i) => (
                  <fieldset key={i} className="grid gap-2 rounded-md bg-muted/40 p-2 text-xs sm:grid-cols-5">
                    <legend className="px-1 font-medium text-muted-foreground">
                      Line {i + 1}: {l.description || 'no description yet'}
                    </legend>
                    <Input
                      aria-label="Tariff code"
                      placeholder="Tariff code"
                      value={l.hts_code ?? ''}
                      onChange={(e) => setLine(i, { hts_code: e.target.value })}
                    />
                    <Input
                      aria-label="Made in"
                      placeholder="Made in"
                      value={l.origin ?? ''}
                      onChange={(e) => setLine(i, { origin: e.target.value })}
                    />
                    <Input aria-label="Unit" placeholder="Unit" value={l.uom ?? ''} onChange={(e) => setLine(i, { uom: e.target.value })} />
                    <Input
                      aria-label="Net kg"
                      placeholder="Net kg"
                      inputMode="decimal"
                      value={String(l.net_kg ?? '')}
                      onChange={(e) => setLine(i, { net_kg: e.target.value.replace(/[^\d.]/g, '') })}
                    />
                    <label className="inline-flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        className="size-4 accent-foreground"
                        checked={Boolean(l.cert_required)}
                        onChange={(e) => setLine(i, { cert_required: e.target.checked })}
                      />
                      Needs a certificate
                    </label>
                  </fieldset>
                ))}
            </div>
          </details>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="po-edit" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Cancelling a purchase order asks why, in a proper dialog. A warning comes back if the vendor already had it. */
function CancelPO({ po, onClose, onDone }: { po: PO; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const cancel = useMutation({
    mutationFn: () => poCall<{ warning?: string }>(`/${po.id}/cancel`, 'POST', { reason: reason.trim() }),
    onSuccess: (r) => {
      if (r?.warning) toast.warning(r.warning);
      else toast.success('Purchase order cancelled');
      onDone();
      onClose();
    },
    onError: (e) => toast.error('It wasn’t cancelled', { description: e.message }),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Cancel the order to {po.vendor}?</DialogTitle>
          <DialogDescription>
            {po.status === 'SENT' || po.status === 'ACKNOWLEDGED'
              ? 'The vendor already has this order. Tell them too.'
              : 'It has not gone to the vendor yet.'}
          </DialogDescription>
        </DialogHeader>
        <label className="grid gap-1.5 text-sm">
          <span className="font-medium">Why is it being cancelled?</span>
          <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        </label>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Keep it
          </Button>
          <Button variant="destructive" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate()}>
            {cancel.isPending && <Loader2 className="animate-spin" />}
            Cancel the order
          </Button>
        </DialogFooter>
        {!reason.trim() && <p className="text-xs text-muted-foreground">Write a reason to cancel.</p>}
      </DialogContent>
    </Dialog>
  );
}

/** Note which drawing revision the vendor confirmed for one line. */
function AckRevision({ po, line, sent, onClose, onDone }: { po: PO; line: number; sent: string; onClose: () => void; onDone: () => void }) {
  const [rev, setRev] = useState(sent);
  const save = useMutation({
    mutationFn: () => poCall(`/${po.id}/revision-ack`, 'POST', { line, revision: rev.trim() }),
    onSuccess: () => {
      toast.success('Noted');
      onDone();
      onClose();
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Which revision did the vendor confirm?</DialogTitle>
          <DialogDescription>
            Line {line} of the order to {po.vendor}. Rev {sent || 'not set'} was sent.
          </DialogDescription>
        </DialogHeader>
        <Input value={rev} onChange={(e) => setRev(e.target.value)} maxLength={20} autoFocus aria-label="Revision" />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!rev.trim() || save.isPending} onClick={() => save.mutate()}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
