'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';
import { EmailDraftDialog } from '@/components/data/email-draft-dialog';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { jobsCall, type JobRow } from '@/components/jobs/job-types';

const KINDS = [
  ['none', 'Nothing in particular (write it myself)', ''],
  ['job', 'A job', 'JOB-2026-0001'],
  ['order', 'A sales order', 'SO-12889'],
  ['rfq', 'A quote request', 'RFQ-2026-0001'],
] as const;

/**
 * Start a new email draft about a job, order or quote request: pick who it is for and what to attach, and the draft
 * waits in the Drafts list. Nothing is sent until a person approves it there.
 */
export function NewDraft({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<(typeof KINDS)[number][0]>('none');
  const [ref, setRef] = useState('');
  const [going, setGoing] = useState(false);
  const client = useQueryClient();
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [audience, setAudience] = useState('internal');
  // A draft not tied to a record: written here, waiting in Drafts for a person to approve. Nothing is sent.
  const write = useMutation({
    mutationFn: async () => {
      const res = await authFetch('/api/v1/secretary/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to: to.trim(), subject: subject.trim(), body: body.trim(), audience }),
      });
      const out = (await res.json().catch(() => null)) as { detail?: string; message?: string } | null;
      if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t work (${res.status}).`);
      return out;
    },
    onSuccess: (out) => {
      toast.success(out?.message ?? 'Draft saved for approval. Nothing was sent.');
      void client.invalidateQueries({ queryKey: ['inbox'] });
      onClose();
    },
    onError: (e) => toast.error('The draft wasn’t saved', { description: e.message }),
  });
  const jobs = useQuery({
    queryKey: ['jobs', 'for-draft'],
    queryFn: async () => {
      const list = await jobsCall<JobRow[] | { items?: JobRow[]; jobs?: JobRow[] }>('?limit=200').catch(() => null);
      return Array.isArray(list) ? list : (list?.items ?? list?.jobs ?? []);
    },
    enabled: kind === 'job',
    retry: false,
    staleTime: 60_000,
  });
  const example = KINDS.find((k) => k[0] === kind)![2];
  const clean = ref.trim();

  if (going && clean && kind !== 'none')
    return (
      <EmailDraftDialog
        kind={kind}
        refId={clean}
        subject={`${KINDS.find((k) => k[0] === kind)![1].replace(/^An? /, '')} ${clean}`}
        onClose={onClose}
      />
    );

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New draft</DialogTitle>
          <DialogDescription>
            What is the email about? You choose who it is for and what to attach next. It is a draft; nothing is sent.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <Field label="About">
            <NativeSelect
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as typeof kind);
                setRef('');
              }}
            >
              {KINDS.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          {kind === 'none' ? (
            <>
              <Field label="To (email)">
                <Input value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@company.com" />
              </Field>
              <Field label="Who is it for?">
                <NativeSelect value={audience} onChange={(e) => setAudience(e.target.value)}>
                  <option value="internal">Our team</option>
                  <option value="vendor">A vendor</option>
                  <option value="customer">A customer</option>
                  <option value="forwarder">A freight forwarder</option>
                </NativeSelect>
              </Field>
              <Field label="Subject">
                <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
              </Field>
              <Field label="Message">
                <Textarea rows={6} value={body} onChange={(e) => setBody(e.target.value)} />
              </Field>
            </>
          ) : (
            <Field label="Which one">
              <Input
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                placeholder={example}
                list={kind === 'job' ? 'draft-jobs' : undefined}
                autoFocus
              />
              {kind === 'job' && (
                <datalist id="draft-jobs">
                  {(jobs.data ?? []).map((j) => (
                    <option key={j.ref} value={j.ref}>
                      {j.title}
                    </option>
                  ))}
                </datalist>
              )}
            </Field>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {kind === 'none' ? (
            <Button disabled={!to.trim() || !subject.trim() || !body.trim() || write.isPending} onClick={() => write.mutate()}>
              Save as a draft
            </Button>
          ) : (
            <Button disabled={!clean} onClick={() => setGoing(true)}>
              Next
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
