'use client';

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type Digest = {
  rows?: {
    container?: string;
    reference?: string;
    old_eta?: string | null;
    new_eta?: string | null;
    job?: { ref?: string } | null;
    flags?: string[];
    action?: string;
  }[];
  ambiguous?: { reference?: string; eta_text?: string; reason?: string }[];
  sender_trusted?: boolean;
  can_apply?: boolean;
  applied?: boolean;
  check?: string | null;
};

async function send(text: string, sender: string, apply: boolean): Promise<Digest> {
  const res = await authFetch('/api/v1/pipelines/forwarder-digest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, apply, sender: sender.trim() || undefined }),
  });
  const out = (await res.json().catch(() => null)) as (Digest & { detail?: string }) | null;
  if (!res.ok || !out) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t work (${res.status}).`);
  return out;
}

/**
 * Paste the forwarder’s weekly update. You first see which shipments would get which new arrival date, and why anything is left out.
 * Nothing changes until you press Apply, and only an update from a trusted sender can be applied.
 */
export function ForwarderDigest() {
  const { hasRole } = useAuth();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [sender, setSender] = useState('');
  const [out, setOut] = useState<Digest | null>(null);
  const preview = useMutation({
    mutationFn: () => send(text, sender, false),
    onSuccess: setOut,
    onError: (e) => toast.error('It could not be read', { description: e.message }),
  });
  const apply = useMutation({
    mutationFn: () => send(text, sender, true),
    onSuccess: (o) => {
      setOut(o);
      toast.success('The new arrival dates were applied.');
    },
    onError: (e) => toast.error('Nothing was applied', { description: e.message }),
  });
  if (!hasRole('OPERATOR')) return null;
  if (!open)
    return (
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Read the forwarder’s weekly update
      </Button>
    );
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4 text-[13px]" aria-label="Forwarder update">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">The forwarder’s weekly update</h2>
        <Button size="xs" variant="ghost" className="ml-auto" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>
      <Field label="Paste the update">
        <Textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <Field label="Who sent it (their email address)">
        <Input value={sender} onChange={(e) => setSender(e.target.value)} placeholder="forwarder@example.com" className="max-w-xs" />
      </Field>
      <Button size="sm" className="justify-self-start" disabled={!text.trim() || preview.isPending} onClick={() => preview.mutate()}>
        {preview.isPending && <Loader2 className="animate-spin" />} Show what would change
      </Button>
      {out && (
        <div className="grid gap-2 rounded-md bg-muted/40 p-3">
          <p className="font-medium">{out.applied ? 'Applied.' : 'Here is what would happen. Nothing has changed yet.'}</p>
          <ul className="grid gap-1">
            {(out.rows ?? []).map((r, i) => (
              <li key={i}>
                {r.reference || r.container}
                {r.job?.ref ? ` (job ${r.job.ref})` : ''}: {r.old_eta ?? 'no date'} to {r.new_eta ?? 'no date'}.{' '}
                <span className="text-muted-foreground">{r.action}</span>
                {(r.flags ?? []).map((f) => (
                  <span key={f} className="ml-1 rounded-full bg-warn-soft px-1.5 py-0.5 text-[11px] text-warn">
                    {f}
                  </span>
                ))}
              </li>
            ))}
          </ul>
          {(out.ambiguous ?? []).map((a, i) => (
            <p key={i} className="text-warn">
              {a.reference}: “{a.eta_text}” could not be read for sure. {a.reason}
            </p>
          ))}
          {out.check && <p className="text-warn">Check against the original: {out.check}</p>}
          {!out.applied &&
            (out.can_apply ? (
              <Button size="sm" className="justify-self-start" disabled={apply.isPending} onClick={() => apply.mutate()}>
                {apply.isPending && <Loader2 className="animate-spin" />} Apply these dates
              </Button>
            ) : (
              <p className="text-xs text-muted-foreground">
                {out.sender_trusted === false
                  ? 'This sender is not on the trusted list, so the dates can be read but not applied.'
                  : 'These dates cannot be applied.'}
              </p>
            ))}
        </div>
      )}
    </section>
  );
}
