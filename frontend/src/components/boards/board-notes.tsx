'use client';

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, MessageSquare, Play, RefreshCw, RotateCcw, Trash2, X } from 'lucide-react';
import { NativeSelect, PanelHeading } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { DictateButton, MicButton, addDictation } from '@/components/data/voice';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { clock, useRecorder } from '@/hooks/use-recorder';
import { dateTime, person, since } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useReadNow } from './use-automations';
import type { Role } from './use-boards';
import { LANGS, unreadFor, useBoardNotes, useMarkRead, useNotePeople, usePostNote, voiceUrl, type Lang, type Note } from './use-notes';

const lower = (v?: string | null) => (v ?? '').trim().toLowerCase();

/** On a row or card: notes on this job sent to me that I haven't read. */
export function NoteChip({ notes, itemId }: { notes: Note[] | undefined; itemId: string }) {
  const { employeeEmail } = useAuth();
  const n = unreadFor(notes, itemId, lower(employeeEmail));
  if (!n) return null;
  return (
    <ToneBadge tone="warn">
      <MessageSquare className="size-3" aria-hidden />
      {n} unread note{n === 1 ? '' : 's'}
    </ToneBadge>
  );
}

/**
 * Writing a note for the engineers on one job (managers). The notes themselves, with who has read them,
 * the Mandarin and Vietnamese versions and any voice recording, now show in the job's one thread
 * together with the comments (see UpdatesPanel).
 */
export function ItemNotes({ boardId, role, itemId, owners = [] }: { boardId: string; role: string; itemId: string; owners?: string[] }) {
  const { hasRole } = useAuth();
  if (!hasRole('MANAGER')) return null;
  return (
    <section className="grid gap-2" aria-label="Write a note for the engineers">
      <PanelHeading>
        <span className="inline-flex items-center gap-1.5">
          <MessageSquare className="size-3" aria-hidden /> Write a note for the engineers
        </span>
      </PanelHeading>
      <NoteComposer boardId={boardId} role={role} itemId={itemId} owners={owners} />
    </section>
  );
}

/** A voice note: loaded when asked for (it's a protected file), then a normal player, plus its transcript. */
export function VoiceNote({ voice }: { voice: NonNullable<Note['voice']> }) {
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle');
  useEffect(() => () => void (src && URL.revokeObjectURL(src)), [src]);

  return (
    <div className="grid gap-1.5 rounded-md bg-muted/40 p-2">
      {src ? (
        <audio controls autoPlay src={src} className="h-9 w-full" />
      ) : (
        <Button
          size="xs"
          variant="outline"
          className="justify-self-start"
          disabled={state === 'loading'}
          onClick={() => {
            setState('loading');
            voiceUrl(voice.url).then(
              (url) => setSrc(url),
              () => setState('error'),
            );
          }}
        >
          {state === 'loading' ? <Loader2 className="animate-spin" /> : <Play />}
          Play the voice note{voice.seconds ? ` (${Math.round(voice.seconds)} s)` : ''}
        </Button>
      )}
      {state === 'error' && <span className="text-xs text-bad">The recording couldn’t be loaded.</span>}
      {voice.transcript ? (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Said: </span>
          {voice.transcript}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">It couldn’t be written out; play it to hear it.</p>
      )}
    </div>
  );
}

/** A manager's note to the job's engineers (or people picked), with optional translations. */
function NoteComposer({ boardId, role, itemId, owners = [] }: { boardId: string; role: string; itemId: string; owners?: string[] }) {
  const post = usePostNote(boardId);
  const [open, setOpen] = useState(false);
  const people = useNotePeople(role, open);
  const client = useQueryClient();
  const readNow = useReadNow(boardId, role as Role);
  const all = people.data?.people ?? [];
  const boardRead = people.data?.boardRead ?? true;
  // The job's owners are named on monday; the app finds their emails among the people it knows.
  const norm = (t: string) =>
    t
      .toLowerCase()
      .replace(/[^a-z0-9 ]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  const withEmail = all.filter((p) => p.email);
  const found = owners.map((name) => ({
    name,
    person:
      withEmail.find((p) => norm(p.name) === norm(name)) ??
      withEmail.find((p) => norm(p.name) && (norm(p.name).includes(norm(name)) || norm(name).includes(norm(p.name)))),
  }));
  const ownerPeople = found.flatMap((f) => (f.person ? [f.person] : []));
  const noEmail = found.filter((f) => !f.person).map((f) => f.name);
  const [text, setText] = useState('');
  const [langs, setLangs] = useState<Set<Lang>>(() => new Set());
  const [urgent, setUrgent] = useState(false);
  const [to, setTo] = useState<string[]>([]);
  const voice = useRecorder({ maxSeconds: 600 });
  const recorded = voice.state === 'done' ? voice.recording : null;
  const reset = () => {
    setText('');
    setLangs(new Set());
    setUrgent(false);
    setTo([]);
    voice.clear();
    setOpen(false);
  };
  const label = (email: string) => all.find((p) => p.email.toLowerCase() === email.toLowerCase())?.name || email;

  if (!open)
    return (
      <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setOpen(true)}>
        <MessageSquare /> Write a note
      </Button>
    );

  return (
    <form
      className="grid gap-3 rounded-lg border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        post.mutate(
          { item_id: itemId, text: text.trim(), languages: [...langs], urgent, to: to.length ? to : undefined, voice: recorded },
          { onSuccess: reset },
        );
      }}
    >
      <Textarea
        required={!recorded}
        rows={4}
        maxLength={4000}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={
          recorded ? 'Anything to add in writing (optional with a voice note)' : 'What the engineers need to know about this job'
        }
        aria-label="Note"
      />
      <div className="flex flex-wrap items-center gap-2">
        <DictateButton onText={(t) => setText((cur) => addDictation(cur, t))} />
        {recorded ? (
          <div className="grid w-full gap-1.5 rounded-md bg-muted/40 p-2">
            <span className="text-xs font-medium">Voice note, {clock(recorded.seconds)}: listen before you send</span>
            <audio controls src={recorded.url} className="h-9 w-full" />
            <div className="flex gap-2">
              <Button type="button" size="xs" variant="ghost" onClick={() => void voice.start()}>
                <RotateCcw /> Record again
              </Button>
              <Button type="button" size="xs" variant="ghost" onClick={voice.clear}>
                <Trash2 /> Remove it
              </Button>
            </div>
          </div>
        ) : (
          <MicButton recorder={voice} size="xs" label="Voice note" />
        )}
        {voice.state === 'error' && <span className="text-xs text-bad">{voice.error}</span>}
      </div>
      <p className="-mt-1 text-xs text-muted-foreground">
        Dictate writes your words into the note; a voice note is attached for everyone to play, and written out for them too.
      </p>
      <div className="grid gap-1.5">
        <span className="text-xs font-medium">Who gets it</span>
        <div className="flex flex-wrap items-center gap-1.5">
          {to.length === 0 ? (
            <ToneBadge>
              The job’s engineers (its owners on the board)
              {owners.length > 0 ? `: ${owners.join(', ')}` : boardRead ? '' : ': not known yet'}
            </ToneBadge>
          ) : (
            to.map((email) => (
              <span key={email} className="inline-flex h-6 items-center gap-1 rounded-full border bg-card pr-1 pl-2 text-xs">
                {label(email)}
                <button type="button" aria-label={`Remove ${email}`} onClick={() => setTo((t) => t.filter((x) => x !== email))}>
                  <X className="size-3" />
                </button>
              </span>
            ))
          )}
          <NativeSelect
            aria-label="Add a person"
            className="h-7 w-auto max-w-64 text-xs"
            value=""
            onChange={(e) => e.target.value && setTo((t) => (t.includes(e.target.value) ? t : [...t, e.target.value]))}
          >
            <option value="">{people.isPending ? 'Loading people…' : 'Add a person…'}</option>
            {[
              { title: 'On this job', list: ownerPeople },
              { title: 'Engineers', list: all.filter((p) => p.role === 'engineer' && !ownerPeople.includes(p)) },
              { title: 'Everyone else', list: all.filter((p) => p.role !== 'engineer' && !ownerPeople.includes(p)) },
            ]
              .map((g) => ({ ...g, list: g.list.filter((p) => !to.includes(p.email)) }))
              .filter((g) => g.list.length > 0)
              .map((g) => (
                <optgroup key={g.title} label={g.title}>
                  {g.list.map((p) => (
                    <option key={p.email || p.name} value={p.email} disabled={!p.email}>
                      {p.label}
                    </option>
                  ))}
                </optgroup>
              ))}
          </NativeSelect>
        </div>
        {!boardRead && (
          <p className="flex flex-wrap items-center gap-2 text-xs text-warn">
            The app hasn’t read this board from monday yet, so the job’s owners can’t be named.
            <Button
              type="button"
              size="xs"
              variant="outline"
              disabled={readNow.isPending}
              onClick={() =>
                readNow.mutate(undefined, { onSettled: () => void client.invalidateQueries({ queryKey: ['autopilot', 'recipe-options'] }) })
              }
            >
              {readNow.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Read it from monday now
            </Button>
          </p>
        )}
        {noEmail.length > 0 && (
          <p className="text-xs text-warn">
            No email on file for {noEmail.join(', ')}, so a note can’t reach {noEmail.length === 1 ? 'them' : 'them'} yet. Add them under
            People &amp; alerts.
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
        <span className="font-medium">Also in</span>
        {(Object.keys(LANGS) as Lang[]).map((l) => (
          <label key={l} className="inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={langs.has(l)}
              onChange={() => setLangs((s) => (s.has(l) ? new Set([...s].filter((x) => x !== l)) : new Set(s).add(l)))}
              className="size-3.5 accent-foreground"
            />
            {LANGS[l].label}
          </label>
        ))}
      </div>
      <label className={cn('inline-flex items-start gap-2 text-xs', urgent && 'text-bad')}>
        <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} className="mt-0.5 size-3.5 accent-current" />
        <span>
          <span className="font-medium">Urgent</span>: also send it as a text message and a phone push now, at any hour.
        </span>
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={reset}>
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          variant={urgent ? 'destructive' : 'default'}
          disabled={post.isPending || voice.state === 'recording' || (!text.trim() && !recorded)}
        >
          {post.isPending && <Loader2 className="animate-spin" />}
          {urgent ? 'Send urgent note' : 'Send note'}
        </Button>
      </div>
    </form>
  );
}
