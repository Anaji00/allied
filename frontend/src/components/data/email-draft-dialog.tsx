'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';

type Attachment = { type: string; id: number | string; name: string; label?: string; audiences?: string[] };
type EmailAction = {
  action: string;
  default_audience?: string;
  recipients?: { audience: string; email: string }[];
  attachments?: Attachment[];
};
type Actions = { title?: string; actions?: EmailAction[] };

const AUDIENCE: Record<string, string> = {
  internal: 'Our team',
  vendor: 'The vendor',
  customer: 'The customer',
  forwarder: 'The forwarder',
};

/**
 * Email this item: pick who it is for, tick the files to attach, and a draft is made in Approvals. Nothing is sent until
 * a person approves it there. The server checks that a vendor draft never names the customer and a customer draft never names a vendor.
 */
export function EmailDraftDialog({ kind, refId, subject, onClose }: { kind: string; refId: string; subject: string; onClose: () => void }) {
  const router = useRouter();
  const base = `/api/v1/actions/${encodeURIComponent(kind)}/${encodeURIComponent(refId)}`;
  const info = useQuery({
    queryKey: ['actions', kind, refId],
    queryFn: async (): Promise<EmailAction | null> => {
      const res = await authFetch(base, { cache: 'no-store' }).catch(() => null);
      if (!res?.ok) return null;
      return (((await res.json()) as Actions).actions ?? []).find((a) => a.action === 'email_draft') ?? null;
    },
    retry: false,
  });
  const email = info.data;
  const [audience, setAudience] = useState('');
  const [to, setTo] = useState<string | null>(null);
  const [subj, setSubj] = useState(subject);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const aud = audience || email?.default_audience || 'internal';
  const audiences = [...new Set(['internal', ...(email?.recipients ?? []).map((r) => r.audience)])];
  const recipient = to ?? (email?.recipients ?? []).find((r) => r.audience === aud)?.email ?? '';
  const files = (email?.attachments ?? []).filter((a) => !a.audiences || a.audiences.includes(aud));
  const key = (a: Attachment) => `${a.type}:${a.id}`;

  async function make() {
    setBusy(true);
    try {
      const res = await authFetch(`${base}/email-draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          audience: aud,
          ...(recipient ? { to: recipient } : {}),
          subject: subj,
          attach: files.filter((a) => !off.has(key(a))).map((a) => ({ type: a.type, id: a.id })),
          include_summary: true,
        }),
      });
      const body = (await res.json().catch(() => null)) as { detail?: string; message?: string } | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      toast.success('Draft ready. Nothing has been sent.', {
        description: body?.message ?? 'It is waiting for approval.',
        action: { label: 'Open Approvals', onClick: () => router.push('/inbox?show=approvals') },
      });
      onClose();
    } catch (e) {
      toast.error('The draft wasn’t made', { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  // An older server has no draft route: fall back to the person's own mail app with the item's title.
  const fallback = info.isSuccess && !email;
  useEffect(() => {
    if (!fallback) return;
    window.location.href = `mailto:?subject=${encodeURIComponent(subject)}`;
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fallback]);
  if (fallback) return null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Email this</DialogTitle>
          <DialogDescription>A draft is made for someone to approve. It is not sent from here.</DialogDescription>
        </DialogHeader>
        {info.isPending ? (
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        ) : (
          <div className="grid gap-3">
            <Field label="Who is it for?">
              <NativeSelect
                value={aud}
                onChange={(e) => {
                  setAudience(e.target.value);
                  setTo(null);
                }}
              >
                {audiences.map((a) => (
                  <option key={a} value={a}>
                    {AUDIENCE[a] ?? a}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="To">
              <Input type="email" value={recipient} onChange={(e) => setTo(e.target.value)} placeholder="name@company.com" />
            </Field>
            <Field label="Subject">
              <Input value={subj} onChange={(e) => setSubj(e.target.value)} />
            </Field>
            {files.length > 0 && (
              <fieldset className="grid gap-1.5">
                <legend className="mb-1 text-sm font-medium">Attach</legend>
                {files.map((a) => (
                  <label key={key(a)} className="flex items-center gap-2 text-[13px]">
                    <input
                      type="checkbox"
                      className="size-4 accent-foreground"
                      checked={!off.has(key(a))}
                      onChange={(e) =>
                        setOff((cur) => {
                          const next = new Set(cur);
                          if (e.target.checked) next.delete(key(a));
                          else next.add(key(a));
                          return next;
                        })
                      }
                    />
                    <span className="min-w-0 truncate">{a.label || a.name}</span>
                  </label>
                ))}
              </fieldset>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={busy || info.isPending || !subj.trim()} onClick={() => void make()}>
            {busy && <Loader2 className="animate-spin" />}
            Make the draft
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
