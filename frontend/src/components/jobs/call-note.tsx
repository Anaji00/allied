'use client';

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Upload } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';

type Action = { label: string; method: string; path: string; body?: unknown };
type Note = {
  note?: string;
  summary?: { caller?: string; company?: string; reason?: string; callback_needed?: boolean; urgency?: string; action_items?: string[] };
  message?: string;
  confirm?: Action;
  dismiss?: Action;
};

async function fail(res: Response) {
  const d = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return new Error(typeof d === 'string' ? d : `That didn’t work (${res.status}).`);
}

/**
 * Paste what was said on a call, or upload the recording. The app writes a short note with the caller, why they called and what
 * to do next. It is only a draft: nothing goes on the job until you press the button.
 */
export function CallNote({ jobRef, onClose }: { jobRef: string; onClose: () => void }) {
  const client = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [note, setNote] = useState<Note | null>(null);

  const make = useMutation({
    mutationFn: async (audio: File | null): Promise<Note> => {
      let res: Response;
      if (audio) {
        const body = new FormData();
        body.append('job', jobRef);
        body.append('upload', audio, audio.name);
        res = await authFetch('/api/v1/calls/to-job/audio', { method: 'POST', body });
      } else {
        res = await authFetch('/api/v1/calls/to-job', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job: jobRef, text: text.trim() }),
        });
      }
      if (!res.ok) throw await fail(res);
      return (await res.json()) as Note;
    },
    onSuccess: setNote,
    onError: (e) => toast.error('The call wasn’t turned into a note', { description: e.message }),
  });
  const decide = useMutation({
    mutationFn: async (a: Action) => {
      const res = await authFetch(a.path, {
        method: a.method,
        headers: a.body ? { 'Content-Type': 'application/json' } : undefined,
        body: a.body ? JSON.stringify(a.body) : undefined,
      });
      if (!res.ok) throw await fail(res);
      return a;
    },
    onSuccess: (a) => {
      toast.success(a === note?.confirm ? 'Note added to the job' : 'Discarded');
      void client.invalidateQueries({ queryKey: ['jobs'] });
      void client.invalidateQueries({ queryKey: ['updates'] });
      onClose();
    },
    onError: (e) => toast.error('That didn’t go through', { description: e.message }),
  });
  const s = note?.summary;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a call note</DialogTitle>
          <DialogDescription>Paste what was said, or upload the recording. You check the note before it goes on the job.</DialogDescription>
        </DialogHeader>
        {!note ? (
          <div className="grid gap-3">
            <Field label="What was said">
              <Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the call here" autoFocus />
            </Field>
            <input
              ref={file}
              type="file"
              accept="audio/*,.mp3,.wav,.m4a,.webm"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) make.mutate(f);
                e.target.value = '';
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="justify-self-start"
              disabled={make.isPending}
              onClick={() => file.current?.click()}
            >
              <Upload /> Upload a recording instead
            </Button>
          </div>
        ) : (
          <div className="grid gap-3 text-[13px]">
            {s && (
              <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1">
                {s.caller && (
                  <>
                    <dt className="text-muted-foreground">Caller</dt>
                    <dd>
                      {s.caller}
                      {s.company ? `, ${s.company}` : ''}
                    </dd>
                  </>
                )}
                {s.reason && (
                  <>
                    <dt className="text-muted-foreground">Why</dt>
                    <dd>{s.reason}</dd>
                  </>
                )}
                {s.callback_needed && (
                  <>
                    <dt className="text-muted-foreground">Call back</dt>
                    <dd className="text-warn">Yes{s.urgency ? `, ${s.urgency}` : ''}</dd>
                  </>
                )}
              </dl>
            )}
            {(s?.action_items ?? []).length > 0 && (
              <ul className="grid gap-0.5 pl-4">
                {s!.action_items!.map((a, i) => (
                  <li key={i} className="list-disc">
                    {a}
                  </li>
                ))}
              </ul>
            )}
            {note.note && <p className="whitespace-pre-line rounded-md bg-muted/40 p-3">{note.note}</p>}
          </div>
        )}
        <DialogFooter>
          {note ? (
            <>
              {note.dismiss && (
                <Button variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate(note.dismiss!)}>
                  Discard
                </Button>
              )}
              {note.confirm && (
                <Button disabled={decide.isPending} onClick={() => decide.mutate(note.confirm!)}>
                  {decide.isPending && <Loader2 className="animate-spin" />}
                  Put it on the job
                </Button>
              )}
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button disabled={!text.trim() || make.isPending} onClick={() => make.mutate(null)}>
                {make.isPending && <Loader2 className="animate-spin" />}
                Make the note
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
