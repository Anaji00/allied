'use client';

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { BellRing, Loader2, X } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { PersonSelect, useAppPeople } from '@/components/data/pick';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

const CHANNELS = [
  ['app', 'In the app'],
  ['email', 'Email'],
  ['sms', 'Text message'],
] as const;
type Channel = (typeof CHANNELS)[number][0];
type Mode = 'nudge' | 'milestone';
type NudgeResult = {
  message?: string;
  practice?: boolean;
  sent?: { email: string; channel: string; status: string }[];
  skipped?: { email: string; reason: string }[];
};

/**
 * Get something done: a message to one person or several, now, by app notification, email and text. A milestone blast tells a whole
 * group about a date. Nothing goes until the sender confirms who gets it and how. The server decides who may send.
 */
export function Nudge() {
  const { hasRole } = useAuth();
  const people = useAppPeople();
  const [mode, setMode] = useState<Mode>('nudge');
  const [to, setTo] = useState<string[]>([]);
  const [pick, setPick] = useState('');
  const [audience, setAudience] = useState<'pick' | 'engineers' | 'everyone'>('pick');
  const [message, setMessage] = useState('');
  const [milestone, setMilestone] = useState('');
  const [due, setDue] = useState('');
  const [channels, setChannels] = useState<Channel[]>(['app', 'email']);
  const [confirming, setConfirming] = useState(false);
  const [practice, setPractice] = useState(true);
  const [result, setResult] = useState<NudgeResult | null>(null);
  const info = useQuery({
    queryKey: ['nudges', 'info'],
    queryFn: async (): Promise<{ can_send?: boolean; senders_set?: boolean } | null> => {
      const res = await authFetch('/api/v1/nudges', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as { can_send?: boolean; senders_set?: boolean }) : null;
    },
    retry: false,
    staleTime: 120_000,
  });

  const reachable = (people.data ?? []).filter((p) => p.email);
  const recipients =
    mode === 'milestone' && audience !== 'pick'
      ? reachable.filter((p) => audience === 'everyone' || p.role === 'engineer').map((p) => p.email)
      : to;
  const nameOf = (email: string) => reachable.find((p) => p.email === email)?.name || email;

  const send = useMutation({
    mutationFn: async () => {
      const res = await authFetch('/api/v1/nudges', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: mode,
          to: recipients,
          message: message.trim(),
          channels,
          practice: practice || undefined,
          ...(mode === 'milestone' ? { milestone: { name: milestone.trim(), due: due || undefined } } : {}),
        }),
      });
      const out = (await res.json().catch(() => null)) as ({ detail?: string } & NudgeResult) | null;
      if (res.status === 404) throw new Error('This isn’t switched on yet. Tell your administrator.');
      if (!res.ok)
        throw Object.assign(new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t go through (${res.status}).`), {
          status: res.status,
        });
      return out;
    },
    onSuccess: (out) => {
      setResult(out as NudgeResult);
      toast.success(
        out?.practice
          ? 'Practice only: nothing was sent.'
          : (out?.sent ?? []).length === 0 && out?.sent
            ? 'Nobody was reached.'
            : (out?.message ?? `Sent to ${recipients.length} ${recipients.length === 1 ? 'person' : 'people'}.`),
      );
      setConfirming(false);
      setMessage('');
      setTo([]);
    },
    onError: (e) => {
      setConfirming(false);
      const status = (e as Error & { status?: number }).status;
      // A refusal (4xx) means nothing went. Anything else may have gone before the answer was lost, so it must be checked before resending.
      if (status && status >= 400 && status < 500) toast.error('Nothing was sent', { description: e.message });
      else
        toast.warning('Not sure it went through', {
          description: 'Check with the person before sending again, so a text is not sent twice.',
        });
    },
  });

  if (!hasRole('MANAGER') || info.data?.can_send === false) return null;
  const ready = recipients.length > 0 && message.trim().length > 0 && channels.length > 0 && (mode === 'nudge' || milestone.trim());
  const how = CHANNELS.filter(([c]) => channels.includes(c))
    .map(([, l]) => l.toLowerCase())
    .join(', ');

  return (
    <section className="grid min-w-0 max-w-full gap-3 rounded-xl border bg-card p-4 sm:p-5" aria-label="Get something done">
      <div className="flex flex-wrap items-center gap-2">
        <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
          <BellRing className="size-4" />
        </span>
        <div className="grid">
          <h2 className="text-base font-semibold">Get something done</h2>
          <p className="text-xs text-muted-foreground">
            Nudge someone, or tell a whole group about a milestone. By app, email and text, right now.
          </p>
        </div>
        <div className="ml-auto flex gap-1.5" role="group" aria-label="What kind">
          {(
            [
              ['nudge', 'Nudge someone'],
              ['milestone', 'Milestone blast'],
            ] as const
          ).map(([m, l]) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => setMode(m)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs',
                mode === m ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground',
              )}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      {mode === 'milestone' && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Milestone">
            <Input
              value={milestone}
              onChange={(e) => setMilestone(e.target.value)}
              placeholder="Drawings due to the vendor"
              className="w-full sm:w-72"
            />
          </Field>
          <Field label="Date">
            <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} className="w-full sm:w-40" />
          </Field>
          <Field label="Who">
            <NativeSelect value={audience} onChange={(e) => setAudience(e.target.value as typeof audience)} className="w-full sm:w-48">
              <option value="engineers">All the engineers</option>
              <option value="everyone">Everyone with an email</option>
              <option value="pick">People I choose</option>
            </NativeSelect>
          </Field>
        </div>
      )}

      {(mode === 'nudge' || audience === 'pick') && (
        <div className="flex flex-wrap items-center gap-2">
          <PersonSelect
            value={pick}
            onChange={(v) => {
              if (v && !to.includes(v)) setTo([...to, v]);
              setPick('');
            }}
            none={to.length ? 'Add another person' : 'Choose who to nudge'}
            className="w-auto min-w-56"
          />
          {to.map((e) => (
            <span key={e} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-xs">
              {nameOf(e)}
              <button type="button" aria-label={`Remove ${nameOf(e)}`} onClick={() => setTo(to.filter((x) => x !== e))}>
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      {mode === 'milestone' && audience !== 'pick' && (
        <p className="text-xs text-muted-foreground">
          {recipients.length} {recipients.length === 1 ? 'person' : 'people'} will get it. Anyone without an email in the app is left out.
        </p>
      )}

      <Field label={mode === 'nudge' ? 'What do you need from them?' : 'Message (optional detail)'}>
        <Textarea
          rows={3}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={
            mode === 'nudge'
              ? 'Please send me the updated drawing for PO 9248 today.'
              : 'Please have your part done and sent for review by then.'
          }
        />
      </Field>
      <div className="flex flex-wrap items-center gap-4" role="group" aria-label="How to reach them">
        {CHANNELS.map(([c, l]) => (
          <label key={c} className="inline-flex items-center gap-1.5 text-[13px]">
            <input
              type="checkbox"
              className="size-4"
              checked={channels.includes(c)}
              onChange={() => setChannels((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]))}
            />
            {l}
          </label>
        ))}
        <label className="inline-flex items-center gap-1.5 text-[13px]">
          <input type="checkbox" className="size-4" checked={practice} onChange={(e) => setPractice(e.target.checked)} />
          Practice only: show what would happen, send nothing
        </label>
        <Button className="ml-auto" disabled={!ready} onClick={() => setConfirming(true)}>
          {mode === 'nudge' ? 'Send the nudge…' : 'Send the blast…'}
        </Button>
      </div>

      {info.data?.senders_set === false && (
        <p className="text-xs text-muted-foreground">Only administrators can send these until the owner names who else may.</p>
      )}
      {result && (
        <div role="status" className="grid gap-1 rounded-md bg-muted/40 p-3 text-[13px]">
          <p className="font-medium">
            {result.practice ? 'Practice: nothing was sent.' : 'Sent.'} {result.message}
          </p>
          {(result.sent ?? []).length > 0 && (
            <p className="text-xs text-muted-foreground">
              {(result.sent ?? []).map((s) => `${nameOf(s.email)} by ${s.channel}`).join(', ')}
            </p>
          )}
          {(result.skipped ?? []).map((s) => (
            <p key={s.email} className="text-xs text-warn">
              {nameOf(s.email)} was left out: {s.reason}
            </p>
          ))}
        </div>
      )}
      {confirming && (
        <Dialog open onOpenChange={(o) => !o && !send.isPending && setConfirming(false)}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Send this now?</DialogTitle>
              <DialogDescription>
                {practice ? 'This is a practice run. Nothing will be sent. It shows who would get it.' : 'It goes out right away to'}{' '}
                {recipients.length} {recipients.length === 1 ? 'person' : 'people'} by {how}. Text messages cost money and cannot be taken
                back.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2 text-[13px]">
              {mode === 'milestone' && (
                <p className="font-medium">
                  Milestone: {milestone}
                  {due ? `, ${due}` : ''}
                </p>
              )}
              <p className="text-muted-foreground">
                To: {recipients.map(nameOf).slice(0, 8).join(', ')}
                {recipients.length > 8 ? ` and ${recipients.length - 8} more` : ''}
              </p>
              <p className="rounded-md bg-muted/50 p-2 whitespace-pre-wrap">{message}</p>
            </div>
            <DialogFooter>
              <Button variant="ghost" disabled={send.isPending} onClick={() => setConfirming(false)}>
                Not now
              </Button>
              <Button disabled={send.isPending} onClick={() => send.mutate()}>
                {send.isPending && <Loader2 className="animate-spin" />}
                Send now
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </section>
  );
}
