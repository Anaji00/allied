'use client';

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { EmailDraftDialog } from '@/components/data/email-draft-dialog';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { authFetch } from '@/lib/api';
import { openCompare } from '@/components/tools/compare-dialog';
import type { Job } from './job-types';

type Result = {
  verdict?: string;
  changed_percent?: number;
  summary?: string;
  warnings?: string[];
  overlay_png_base64?: string;
  thread?: { posted?: boolean; problem?: string } | null;
};

/**
 * Compare two drawings already attached to the job, newest against the one before it. The differences come first in plain words
 * (units, revision, size), then the picture: red is only on the first, blue only on the second. One click posts the result
 * to the job's thread or starts an email to the vendor with the picture attached.
 */
export function CompareJobFiles({ job, onClose }: { job: Job; onClose: () => void }) {
  const files = (job.design?.files ?? []).filter((f) => typeof f.id === 'number' || /^\d+$/.test(String(f.id)));
  // Newest two by default: the previous revision first, the new one second.
  const [a, setA] = useState(String(files.length > 1 ? files[files.length - 2].id : (files[0]?.id ?? '')));
  const [b, setB] = useState(String(files.length > 1 ? files[files.length - 1].id : ''));
  const [emailing, setEmailing] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  const run = useMutation({
    mutationFn: async (post: boolean): Promise<Result> => {
      const res = await authFetch('/api/v1/drawings/compare-files', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_a: Number(a), file_b: Number(b), ...(post ? { post_to_thread: true } : {}) }),
      });
      const body = (await res.json().catch(() => null)) as (Result & { detail?: string }) | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      return body ?? {};
    },
    onSuccess: (r, post) => {
      setResult(r);
      if (post) {
        if (r.thread?.posted) toast.success('Posted to the job’s discussion');
        else toast.warning('It wasn’t posted', { description: r.thread?.problem });
      }
    },
    onError: (e) => toast.error('The drawings weren’t compared', { description: e.message }),
  });

  const name = (id: string) => files.find((f) => String(f.id) === id)?.name ?? '';
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Compare drawings on this job</DialogTitle>
          <DialogDescription>Pick two files already on the job. Nothing is uploaded again.</DialogDescription>
        </DialogHeader>
        {files.length < 2 ? (
          <div className="grid gap-2 text-sm text-muted-foreground">
            <p>This job has fewer than two drawings attached. Attach the old and the new one, or compare files from your computer.</p>
            <Button variant="outline" size="sm" className="justify-self-start" onClick={() => (onClose(), openCompare())}>
              Compare files from my computer
            </Button>
          </div>
        ) : (
          <div className="grid gap-3">
            <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
              <Field label="Earlier (first)">
                <NativeSelect value={a} onChange={(e) => setA(e.target.value)}>
                  {files.map((f) => (
                    <option key={String(f.id)} value={String(f.id)}>
                      {f.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Newer (second)">
                <NativeSelect value={b} onChange={(e) => setB(e.target.value)}>
                  {files.map((f) => (
                    <option key={String(f.id)} value={String(f.id)}>
                      {f.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Button disabled={!a || !b || a === b || run.isPending} onClick={() => run.mutate(false)}>
                {run.isPending && <Loader2 className="animate-spin" />}
                Compare
              </Button>
            </div>
            {a === b && <p className="text-xs text-muted-foreground">Choose two different files.</p>}

            {result && (
              <div className="grid gap-3">
                {(result.warnings ?? []).length > 0 && (
                  <ul className="grid gap-1 rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
                    {result.warnings!.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                )}
                {result.summary && <p className="whitespace-pre-line text-[13px]">{result.summary}</p>}
                {result.overlay_png_base64 && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={`data:image/png;base64,${result.overlay_png_base64}`}
                    alt={`What differs between ${name(a)} and ${name(b)}: red is only on the first, blue only on the second`}
                    className="max-h-[60svh] w-full rounded-md border bg-white object-contain"
                  />
                )}
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" disabled={run.isPending} onClick={() => run.mutate(true)}>
                    Post result to the job
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setEmailing(true)}>
                    Email to the vendor
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
        {emailing && (
          <EmailDraftDialog
            kind="job"
            refId={job.ref}
            subject={`Drawing change on ${job.ref}: ${job.title}`}
            onClose={() => setEmailing(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
