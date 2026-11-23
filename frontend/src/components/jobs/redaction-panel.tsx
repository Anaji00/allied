'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Fold } from '@/components/data/fold';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { downloadBlob, type Job } from './job-types';

type Artifact = {
  id: number;
  source_file_id: number;
  redacted_file_id?: number | null;
  method: 'AUTO' | 'ATTESTED';
  status: 'PENDING' | 'CLEAN' | 'BLOCKED' | 'VERIFIED' | 'VOID';
  findings_before?: { page?: number; where?: string; term?: string }[];
  needs?: string;
  verified_by?: string | null;
  attested_by?: string | null;
};
type Redaction = { artifacts: Artifact[]; enforced?: boolean; ocr_available?: boolean };

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

/** What each state means to a person. "Unreadable" never reads as clean: only Verified is safe to send. */
const STATE: Record<string, { word: string; tone: string; hint: string }> = {
  PENDING: { word: 'Not checked yet', tone: 'bg-muted text-muted-foreground', hint: 'Check it before it goes to a vendor.' },
  BLOCKED: { word: 'Found customer text', tone: 'bg-bad-soft text-bad', hint: 'It cannot go to a vendor yet.' },
  CLEAN: {
    word: 'Looks clean, needs a person to confirm',
    tone: 'bg-warn-soft text-warn',
    hint: 'An engineer or manager must confirm it before a vendor gets it.',
  },
  VERIFIED: { word: 'Verified: safe for the vendor', tone: 'bg-ok-soft text-ok', hint: '' },
  VOID: { word: 'The file changed, so this no longer counts', tone: 'bg-warn-soft text-warn', hint: 'Check it again.' },
};

/**
 * The drawings that may go to a vendor. A vendor only ever gets a verified copy with the customer's name removed. A drawing the app cannot
 * read, or a DWG, is never called clean until a person confirms it.
 */
export function RedactionPanel({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const jobId = typeof job.id === 'number' ? job.id : Number(job.id);
  const files = (job.design?.files ?? []).filter((f) => Number.isFinite(Number(f.id)));
  const [attesting, setAttesting] = useState<{ id: number; name: string } | null>(null);
  const [statement, setStatement] = useState('');
  const q = useQuery({
    queryKey: ['redaction', jobId],
    queryFn: () => call<Redaction>(`/redaction/job/${jobId}`),
    enabled: Number.isFinite(jobId),
    retry: false,
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ['redaction', jobId] });
  const act = useMutation({
    mutationFn: async (a: { kind: 'check' | 'verify' | 'attest'; file?: number; id?: number }) => {
      if (a.kind === 'check') return call('/redaction/check', 'POST', { job_id: jobId, file_id: a.file });
      if (a.kind === 'verify') return call(`/redaction/${a.id}/verify`, 'POST', {});
      return call('/redaction/attest', 'POST', { job_id: jobId, file_id: a.file, statement: statement.trim() });
    },
    onSuccess: () => {
      toast.success('Saved');
      setAttesting(null);
      setStatement('');
      refresh();
    },
    onError: (e) => toast.error('That did not go through', { description: e.message }),
  });
  if (!q.data || files.length === 0) return null;
  const artifacts = q.data.artifacts ?? [];
  const forFile = (id: number) => artifacts.filter((a) => a.source_file_id === id).sort((a, b) => b.id - a.id)[0];
  const verified = files.filter((f) => forFile(Number(f.id))?.status === 'VERIFIED').length;

  return (
    <Fold id="job-redaction" title="Drawings for the vendor" summary={`${verified} of ${files.length} verified`} defaultOpen={false}>
      <div className="grid gap-3 text-[13px]">
        <p className="text-muted-foreground">
          A vendor only ever gets a verified copy with the customer’s name taken out. Nothing is sent from here.
        </p>
        {q.data.ocr_available === false && (
          <p role="note" className="rounded-md bg-warn-soft px-3 py-2 text-warn">
            Pages that are only a picture cannot be cleaned or read here. Those drawings need an engineer to confirm them by eye.
          </p>
        )}
        {files.map((f) => {
          const a = forFile(Number(f.id));
          const st = STATE[a?.status ?? 'PENDING'] ?? {
            word: 'Unknown: treat it as not safe',
            tone: 'bg-warn-soft text-warn',
            hint: 'Check it again before it goes to a vendor.',
          };
          const dwg = /\.dwg$/i.test(f.name);
          return (
            <div key={String(f.id)} className="grid gap-1.5 rounded-md border p-3">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                {f.name}
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-normal ${st.tone}`}>{st.word}</span>
              </p>
              {a?.needs && <p className="text-muted-foreground">{a.needs}</p>}
              {!a?.needs && st.hint && <p className="text-xs text-muted-foreground">{st.hint}</p>}
              {a?.status === 'BLOCKED' && (a.findings_before ?? []).length > 0 && (
                <ul className="grid gap-0.5 text-xs text-bad">
                  {a.findings_before!.slice(0, 6).map((x, i) => (
                    <li key={i}>
                      Found “{x.term}”{x.page ? ` on page ${x.page}` : ''}
                      {x.where ? ` (${x.where})` : ''}
                    </li>
                  ))}
                </ul>
              )}
              {a?.verified_by && <p className="text-xs text-muted-foreground">Confirmed by {a.verified_by}.</p>}
              {a?.attested_by && (
                <p className="text-xs text-muted-foreground">An engineer, {a.attested_by}, stated this file has nothing of the customer.</p>
              )}
              {hasRole('OPERATOR') && (
                <div className="flex flex-wrap gap-2">
                  {!dwg && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={act.isPending}
                      onClick={() => act.mutate({ kind: 'check', file: Number(f.id) })}
                    >
                      {act.isPending && <Loader2 className="animate-spin" />}
                      {a ? 'Check it again and make a clean copy' : 'Check it and make a clean copy'}
                    </Button>
                  )}
                  {a?.status === 'CLEAN' && a.redacted_file_id && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        void downloadBlob(`/api/v1/files/${a.redacted_file_id}/download`, 'clean-copy').catch((e: Error) =>
                          toast.error('The clean copy did not open', { description: e.message }),
                        )
                      }
                    >
                      Open the clean copy to look at it
                    </Button>
                  )}
                  {a?.status === 'CLEAN' && hasRole('MANAGER') && (
                    <Button size="sm" disabled={act.isPending} onClick={() => act.mutate({ kind: 'verify', id: a.id })}>
                      I looked: it shows nothing of the customer
                    </Button>
                  )}
                  {(dwg || a?.status === 'PENDING' || a?.status === 'BLOCKED') && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setStatement('');
                        setAttesting({ id: Number(f.id), name: f.name });
                      }}
                    >
                      An engineer states it is free of the customer
                    </Button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {attesting && (
        <Dialog open onOpenChange={(o) => !o && setAttesting(null)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Say this drawing is free of the customer</DialogTitle>
              <DialogDescription>
                {attesting.name}: only a named engineer can do this. It is recorded with your name, and it stops counting if the file
                changes.
              </DialogDescription>
            </DialogHeader>
            <Textarea
              rows={3}
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
              placeholder="For example: I opened it in CAD and removed the title block and notes."
            />
            <DialogFooter>
              <Button variant="ghost" onClick={() => setAttesting(null)}>
                Cancel
              </Button>
              <Button
                disabled={statement.trim().length < 8 || act.isPending}
                onClick={() => act.mutate({ kind: 'attest', file: attesting.id })}
              >
                Record my statement
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Fold>
  );
}
