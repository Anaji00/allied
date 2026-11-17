'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, Check, Loader2 } from 'lucide-react';
import { PanelHeading } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { FileChip } from '@/components/data/item-files';
import { UpdatesPanel } from '@/components/data/updates-panel';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { dateTime, day, since, words } from '@/lib/format';
import { cn } from '@/lib/utils';
import { STAGES, jobsCall, stageLabel, type Job } from './job-types';
import { AddJobShipment } from './add-shipment';
import { JobPacket } from './job-packet';
import { VendorQuoteButton } from './vendor-quote';
import { PurchaseOrders } from './po-card';
import { ResearchCard, SalesOrderCard } from './sales-research';

const PO_STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: 'Draft', tone: 'neutral' },
  APPROVED: { label: 'Approved', tone: 'info' },
  SENT: { label: 'Sent to the vendor', tone: 'ok' },
  ACKNOWLEDGED: { label: 'Vendor confirmed', tone: 'ok' },
};
const text = (v: unknown) => (typeof v === 'string' || typeof v === 'number' ? String(v) : '');
const pick = (r: Record<string, unknown>, keys: string[]) => keys.map((k) => text(r[k])).find(Boolean) ?? '';

function Card({ title, children, empty }: { title: string; children?: React.ReactNode; empty?: string }) {
  return (
    <section
      className={
        children == null
          ? 'flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg border bg-card px-4 py-2.5'
          : 'grid content-start gap-2 rounded-lg border bg-card p-4'
      }
    >
      <PanelHeading>{title}</PanelHeading>
      {children ?? <p className="text-sm text-muted-foreground">{empty}</p>}
    </section>
  );
}

/** Where the job is, left to right: finished steps are filled, the current one is marked. */
function StageBar({ job }: { job: Job }) {
  const done = new Set(job.done ?? []);
  const at = STAGES.findIndex(([k]) => k === job.stage);
  const history = new Map((job.stages ?? []).map((s) => [s.stage, s]));
  return (
    <ol className="grid grid-cols-2 gap-1.5 sm:grid-cols-4 lg:grid-cols-6" aria-label="Stages of the job">
      {STAGES.map(([key, label], i) => {
        const current = job.stage === key;
        const finished = done.has(key);
        // A step the job moved past without it being recorded: shown dashed, not as unfinished work.
        const info = history.get(key);
        const skipped = !finished && !current && at > i && !info?.done;
        return (
          <li
            key={key}
            aria-current={current ? 'step' : undefined}
            className={cn(
              'flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-xs',
              (finished || info?.done) && 'border-transparent bg-ok-soft text-ok',
              info?.blocked_reason && 'border-warn',
              current && 'border-foreground bg-card font-medium text-foreground shadow-sm',
              !finished && !current && 'text-muted-foreground',
              skipped && 'border-dashed',
            )}
          >
            {finished || info?.done ? (
              <Check className="size-3.5 shrink-0" aria-hidden="true" />
            ) : (
              <span className={cn('size-2 shrink-0 rounded-full', current ? 'bg-foreground' : 'bg-border')} aria-hidden="true" />
            )}
            <span className="grid min-w-0">
              <span>{label}</span>
              {info?.entered_at && <span className="text-xs font-normal text-muted-foreground">{day(info.entered_at)}</span>}
              {info?.blocked_reason && <span className="text-xs font-normal text-warn">{info.blocked_reason}</span>}
            </span>
            {skipped && <span className="sr-only"> (skipped)</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** One job from the customer's request to the shipment: what is done, what is waiting on whom, the design, the orders and the drafts. */
export function JobPage() {
  const { ref } = useParams<{ ref: string }>();
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const id = decodeURIComponent(String(ref ?? ''));
  const q = useQuery({ queryKey: ['jobs', id], queryFn: () => jobsCall<Job>(`/${encodeURIComponent(id)}`), retry: false });
  const approve = useMutation({
    mutationFn: () => jobsCall<Job>(`/${encodeURIComponent(id)}/design/approve`, 'POST'),
    onSuccess: () => {
      toast.success('Design approved. Purchase orders for this job can now go out.');
      void client.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: (e) => toast.error('The design wasn’t approved', { description: e.message }),
  });

  if (q.isPending) return <RowsSkeleton rows={6} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const job = q.data;
  if (!job) return <EmptyState title="Job not found">It may not exist, or jobs aren’t switched on yet.</EmptyState>;

  const approved = Boolean(job.design?.approved_at);
  const pos = job.purchase_orders ?? [];
  const drafts = job.drafts ?? [];
  const ships = job.shipments ?? [];

  return (
    <div className="grid gap-4">
      <div className="grid gap-1">
        <Link href="/jobs" className="inline-flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-3" /> All jobs
        </Link>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-xl font-semibold tracking-tight">{job.title}</h2>
          <span className="font-mono text-xs text-muted-foreground">{job.ref}</span>
          <ToneBadge tone="info">{stageLabel(job.stage)}</ToneBadge>
        </div>
        <p className="text-sm text-muted-foreground">
          {job.customer || 'Customer not named'}
          {job.sales_order?.customer_reference || job.customer_reference
            ? ` · their reference ${job.sales_order?.customer_reference || job.customer_reference}`
            : ''}
          {job.updated_at ? ` · updated ${since(job.updated_at)}` : ''}
        </p>
      </div>

      <StageBar job={job} />

      {(job.checks?.length ?? 0) > 0 && (
        <ul className="grid gap-1.5" aria-label="Checks">
          {job.checks!.map((c) => (
            <li
              key={c.key}
              className={cn(
                'rounded-md px-3 py-2 text-[13px]',
                c.level === 'warn' ? 'bg-warn-soft text-warn' : 'bg-muted text-muted-foreground',
              )}
            >
              {c.text}
            </li>
          ))}
        </ul>
      )}

      {(job.next_steps?.length ?? 0) > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-card px-3 py-2 text-[13px]" aria-label="What now">
          <span className="font-medium">What now</span>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {job.next_steps!.map((s) => (
              <li key={s.key} className="text-muted-foreground">
                {s.text}
              </li>
            ))}
          </ul>
          {job.next_steps!.some((s) => s.key === 'ask_vendor') && (
            <span className="ml-auto">
              <VendorQuoteButton job={job} />
            </span>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Waiting on" empty="Nothing is waiting right now.">
          {(job.waiting?.length ?? 0) > 0 ? (
            <ul className="grid gap-2 text-[13px]">
              {job.waiting!.map((w, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-2">
                  <span className="font-medium">{w.what}</span>
                  {w.who && <span className="text-muted-foreground">{w.who}</span>}
                  {w.since && <span className="text-xs text-muted-foreground">{since(w.since)}</span>}
                  {w.action_id && hasRole('MANAGER') && (
                    <Link href="/inbox?show=approvals" className="ml-auto text-xs underline">
                      Open in Approvals
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          ) : undefined}
        </Card>

        <Card title="Design">
          <div className="grid gap-2 text-[13px]">
            {approved ? (
              <p className="flex items-center gap-2">
                <ToneBadge tone="ok">Approved</ToneBadge>
                <span className="text-muted-foreground">
                  by {job.design?.approved_by} · {dateTime(job.design?.approved_at)}
                </span>
              </p>
            ) : (
              <p className="text-muted-foreground">
                Not approved yet. Purchase orders for this job stay locked until a manager approves the design.
              </p>
            )}
            {(job.design?.files?.length ?? 0) > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {job.design!.files!.map((f) => (
                  <FileChip key={f.id} file={{ id: Number(f.id), filename: f.name, size: f.size ?? 0 }} />
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No design files yet. Add them in the Files box below; they go to the vendor with the purchase order.
              </p>
            )}
            {!approved && hasRole('MANAGER') && (
              <Button
                size="sm"
                className="justify-self-start"
                disabled={approve.isPending}
                onClick={() => {
                  if (window.confirm('Approve this design? Purchase orders for the job can then go to the vendor.')) approve.mutate();
                }}
              >
                {approve.isPending && <Loader2 className="animate-spin" />}
                Approve the design
              </Button>
            )}
          </div>
        </Card>

        <PurchaseOrders job={job} />

        <SalesOrderCard job={job} />

        <Card title="Shipments" empty="Nothing has shipped yet.">
          {ships.length > 0 ? (
            <ul className="divide-y text-[13px]">
              {ships.map((s, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 py-1.5 first:pt-0 last:pb-0">
                  <Link href={s.id ? `/shipments?id=${encodeURIComponent(String(s.id))}` : '/shipments'} className="underline">
                    {pick(s, ['reference', 'container', 'booking', 'tracking', 'id']) || 'Shipment'}
                  </Link>
                  {pick(s, ['status']) && <ToneBadge>{words(pick(s, ['status']))}</ToneBadge>}
                  {pick(s, ['eta', 'eta_current', 'eta_original']) && (
                    <span className="text-xs text-muted-foreground">arrives {day(pick(s, ['eta', 'eta_current', 'eta_original']))}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Nothing has shipped yet.</p>
          )}
          {hasRole('OPERATOR') && <AddJobShipment job={job} />}
        </Card>

        <Card title="Emails written for this job" empty="No drafts yet.">
          {drafts.length > 0 ? (
            <ul className="divide-y text-[13px]">
              {drafts.map((d) => (
                <li key={d.id} className="flex flex-wrap items-center gap-2 py-1.5 first:pt-0 last:pb-0">
                  <span>{d.summary}</span>
                  <ToneBadge tone={d.status === 'PENDING' ? 'warn' : 'neutral'}>
                    {d.status === 'PENDING' ? 'Waiting for your OK' : d.status.toLowerCase()}
                  </ToneBadge>
                </li>
              ))}
              <li className="pt-1.5 text-xs">
                <Link href="/inbox?show=approvals" className="underline">
                  Open Approvals
                </Link>
              </li>
            </ul>
          ) : undefined}
        </Card>
      </div>

      <JobPacket job={job} />
      <ResearchCard job={job} />

      {job.thread ? (
        <div className="grid gap-2">
          <h3 className="text-sm font-semibold">Discussion, files and who has the ball</h3>
          <UpdatesPanel itemType={job.thread.entity === 'rfq' ? 'rfq' : (job.thread.entity as 'order')} itemRef={String(job.thread.id)} />
        </div>
      ) : null}
    </div>
  );
}
