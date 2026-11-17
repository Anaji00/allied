'use client';

import { useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Upload } from 'lucide-react';
import { PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { words } from '@/lib/format';
import { DocFacts } from './doc-facts';
import type { Job } from './job-types';

type Doc = {
  id: number | string;
  kind: string;
  label: string;
  pages?: string | null;
  fields?: Record<string, unknown>;
  source?: string | null;
  confirmed?: boolean;
  confirmed_by?: string | null;
  warnings?: string[];
};
type Waiting = Record<string, unknown> & { packet: number | string };

const shown = (v: unknown) => (Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? '' : String(v ?? ''));

async function fail(res: Response) {
  const detail = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
}

/**
 * The job's paper packet: the documents found in it, in order, with only the facts printed on each page.
 * A blank on the paper stays blank here. The packets are Allied's own papers and are never changed.
 */
export function JobPacket({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const operator = hasRole('OPERATOR');
  const docs = ((job as Job & { documents?: { items?: Doc[] } }).documents?.items ?? []) as Doc[];
  const waiting = useQuery({
    queryKey: ['packets', 'waiting'],
    queryFn: async (): Promise<Waiting[] | null> => {
      const res = await authFetch('/api/v1/packets', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Waiting[]) : null;
    },
    enabled: operator,
    retry: false,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['jobs'] });
    void client.invalidateQueries({ queryKey: ['packets'] });
  };
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData();
      body.append('file', file);
      const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(job.ref)}/packets`, { method: 'POST', body });
      if (!res.ok) throw await fail(res);
    },
    onSuccess: () => {
      toast.success('Papers read');
      refresh();
    },
    onError: (e) => toast.error('The packet wasn’t read', { description: e.message }),
  });
  const stage = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(job.ref)}/packets/stage-monday`, { method: 'POST' });
      if (!res.ok) throw await fail(res);
      return (await res.json()) as { staged?: number; differs?: unknown[]; no_column_for?: string[] };
    },
    onSuccess: (r) => {
      toast.success(`${r.staged ?? 0} fact${r.staged === 1 ? '' : 's'} saved for monday`, {
        description: 'Only blank cells, and nothing is sent until someone sends the monday changes list.',
      });
      void client.invalidateQueries({ queryKey: ['monday-push'] });
    },
    onError: (e) => toast.error('Nothing was saved', { description: e.message }),
  });
  const confirm = useMutation({
    mutationFn: async ({ id, fields }: { id: number | string; fields: Record<string, string>; label?: string }) => {
      const res = await authFetch(`/api/v1/packets/documents/${encodeURIComponent(String(id))}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.keys(fields).length ? { fields } : {}),
      });
      if (!res.ok) throw await fail(res);
    },
    onSuccess: (_r, v) => {
      toast.success(v.label ? `Confirmed: ${v.label}` : 'Confirmed');
      refresh();
    },
    onError: (e) => toast.error('That wasn’t saved', { description: e.message }),
  });
  const attach = useMutation({
    mutationFn: async (packet: number | string) => {
      const res = await authFetch(`/api/v1/packets/${encodeURIComponent(String(packet))}/attach`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job: job.ref }),
      });
      if (!res.ok) throw await fail(res);
    },
    onSuccess: () => {
      toast.success('Packet attached to this job');
      refresh();
    },
    onError: (e) => toast.error('It wasn’t attached', { description: e.message }),
  });

  const available = waiting.data !== null && waiting.data !== undefined;
  if (docs.length === 0 && !available) return null;
  const queue = waiting.data ?? [];
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4" aria-label="Job papers">
      <div className="flex flex-wrap items-center gap-2">
        <PanelHeading>Job papers</PanelHeading>
        {operator && available && (
          <>
            <input
              ref={input}
              type="file"
              accept="application/pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = '';
              }}
            />
            <Button size="sm" variant="outline" className="ml-auto" disabled={upload.isPending} onClick={() => input.current?.click()}>
              {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
              Add the papers (PDF)
            </Button>
          </>
        )}
      </div>
      {docs.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">No packet read for this job yet.</p>
      ) : (
        <ol className="grid gap-2">
          {docs.map((d) => {
            const facts = Object.entries(d.fields ?? {}).filter(([, v]) => shown(v));
            return (
              <li key={d.id} className="grid gap-0.5 border-l-2 pl-3 text-[13px]">
                <span className="font-medium">
                  {d.label || words(d.kind)}
                  {d.pages ? <span className="font-normal text-muted-foreground"> · page {d.pages}</span> : null}
                </span>
                {facts.length > 0 && (
                  <DocFacts
                    doc={d}
                    facts={facts}
                    canEdit={operator}
                    pending={confirm.isPending}
                    onConfirm={(fields) =>
                      confirm.mutate({
                        id: d.id,
                        fields,
                        label: [d.label, d.fields?.po_number ? `PO ${d.fields.po_number}` : ''].filter(Boolean).join(' '),
                      })
                    }
                  />
                )}
              </li>
            );
          })}
        </ol>
      )}
      {operator && docs.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <Button size="sm" variant="outline" disabled={stage.isPending} onClick={() => stage.mutate()}>
            {stage.isPending && <Loader2 className="animate-spin" />}
            Save these to the job’s monday row (not sent yet)
          </Button>
          <span className="text-xs text-muted-foreground">Only fills empty boxes. Nothing goes to monday until you approve it.</span>
        </div>
      )}
      {operator && queue.length > 0 && (
        <div className="grid gap-1.5 border-t pt-3">
          <span className="text-xs font-medium text-muted-foreground">Papers waiting for a job</span>
          {queue.map((p) => (
            <div key={String(p.packet)} className="flex flex-wrap items-center gap-2 text-[13px]">
              <span>{String(p.name ?? p.filename ?? `Packet ${p.packet}`)}</span>
              <Button size="xs" variant="outline" disabled={attach.isPending} onClick={() => attach.mutate(p.packet)}>
                Attach to this job
              </Button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
