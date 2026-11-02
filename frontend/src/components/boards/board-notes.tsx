'use client';

import { useEffect, useState } from 'react';
import { Loader2, MessageSquare, Play, RotateCcw, Trash2, X } from 'lucide-react';
import { NativeSelect, PanelHeading } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { DictateButton, MicButton, addDictation } from '@/components/data/voice';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { clock, useRecorder } from '@/hooks/use-recorder';
import { dateTime, person, since } from '@/lib/format';
import { cn } from '@/lib/utils';
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
 * Notes from management to the engineers on one job: English first (the version that counts),
 * Mandarin or Vietnamese when asked for, who has read each, and a composer for managers.
 */
export function ItemNotes({ boardId, role, itemId }: { boardId: string; role: string; itemId: string }) {
  const { hasRole, employeeEmail } = useAuth();
  const me = lower(employeeEmail);
  const notes = useBoardNotes(boardId, true);
  const mine = (notes.data ?? []).filter((n) => n.item_id === itemId);
  const offered = (Object.keys(LANGS) as Lang[]).filter((l) => mine.some((n) => n.translations[l]?.text));
  const [lang, setLang] = useState<'en' | Lang>('en');
  const unread = unreadFor(notes.data, itemId, me);

  return (
    <section className="grid gap-2" aria-label="Notes for the engineers">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PanelHeading>
          <span className="inline-flex items-center gap-1.5">
            <MessageSquare className="size-3" aria-hidden /> Notes for the engineers
            {unread > 0 && <ToneBadge tone="warn">{unread} unread</ToneBadge>}
          </span>
        </PanelHeading>
        {offered.length > 0 && (
          <div role="group" aria-label="Language" className="flex gap-1">
            {(['en', ...offered] as const).map((l) => (
              <button
                key={l}
                type="button"
                aria-pressed={lang === l}
                onClick={() => setLang(l)}
                className={cn(
                  'h-6 rounded-full border px-2 text-xs',
                  lang === l ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {l === 'en' ? 'English' : LANGS[l].short}
              </button>
            ))}
          </div>
        )}
      </div>
      {notes.isPending ? (
        <p className="text-xs text-muted-foreground">Loading notes…</p>
      ) : notes.error ? (
        <p className="text-xs text-bad">Notes couldn’t be loaded: {notes.error.message}</p>
      ) : mine.length === 0 ? (
        <p className="text-xs text-muted-foreground">No notes on this job yet.</p>
      ) : (
        <ul className="grid gap-2">
          {mine.map((n) => (
            <NoteCard key={n.id} note={n} lang={lang} me={me} boardId={boardId} />
          ))}
        </ul>
      )}
      {hasRole('MANAGER') && <NoteComposer boardId={boardId} role={role} itemId={itemId} />}
    </section>
  );
}

function NoteCard({ note: n, lang, me, boardId }: { note: Note; lang: 'en' | Lang; me: string; boardId: string }) {
  const markRead = useMarkRead(boardId, me);
  const t = lang === 'en' ? undefined : n.translations[lang];
  const read = n.recipients.filter((r) => n.read_by[r]);
  const mineUnread = n.unread.includes(me);

  return (
    <li className={cn('grid gap-2 rounded-lg border p-3 text-[13px]', n.urgent && 'border-bad/50', mineUnread && 'bg-warn-soft/40')}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{person(n.author, me)}</span>
        <span title={dateTime(n.created_at)}>{since(n.created_at)}</span>
        {n.urgent && <ToneBadge tone="bad">Urgent</ToneBadge>}
      </div>
      {t?.text ? (
        <>
          <p lang={lang} className="leading-relaxed whitespace-pre-wrap">
            <Marked text={t.text} terms={t.unsure ?? []} />
          </p>
          {(t.unsure?.length ?? 0) > 0 && (
            <p className="text-xs text-muted-foreground">
              Highlighted terms the translator wasn’t sure of; check them against the English.
            </p>
          )}
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">English (the version that counts)</summary>
            <p className="mt-1 whitespace-pre-wrap">{n.text}</p>
          </details>
        </>
      ) : (
        <>
          <p className="leading-relaxed whitespace-pre-wrap">{n.text}</p>
          {lang !== 'en' && (
            <p className="text-xs text-muted-foreground">{t?.error ?? `No ${LANGS[lang].label} translation was asked for on this note.`}</p>
          )}
        </>
      )}
      {n.voice && <VoiceNote voice={n.voice} />}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span
          title={n.recipients
            .map((r) => `${person(r, me)}: ${n.read_by[r] ? `read ${dateTime(n.read_by[r])}` : 'not read yet'}`)
            .join('\n')}
        >
          Read by {read.length} of {n.recipients.length}
          {n.unread.length > 0 && ` · waiting on ${n.unread.map((r) => person(r, me)).join(', ')}`}
        </span>
        {mineUnread && (
          <Button size="xs" variant="outline" disabled={markRead.isPending} onClick={() => markRead.mutate(n.id)}>
            Mark as read
          </Button>
        )}
      </div>
    </li>
  );
}

/** A translation with the terms the translator wasn't sure of highlighted. */
function Marked({ text, terms }: { text: string; terms: string[] }) {
  const wanted = terms.filter(Boolean);
  if (!wanted.length) return <>{text}</>;
  const escaped = wanted.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const parts = text.split(new RegExp(`(${escaped.join('|')})`, 'g'));
  return (
    <>
      {parts.map((part, i) =>
        wanted.includes(part) ? (
          <mark key={i} className="rounded-sm bg-warn-soft px-0.5 text-warn" title="The translator wasn’t sure of this term">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

/** A voice note: loaded when asked for (it's a protected file), then a normal player, plus its transcript. */
function VoiceNote({ voice }: { voice: NonNullable<Note['voice']> }) {
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
function NoteComposer({ boardId, role, itemId }: { boardId: string; role: string; itemId: string }) {
  const post = usePostNote(boardId);
  const [open, setOpen] = useState(false);
  const people = useNotePeople(role, open);
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
  const label = (email: string) => people.data?.find((p) => p.value === email)?.label ?? email;

  if (!open)
    return (
      <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setOpen(true)}>
        <MessageSquare /> Write a note to the engineers
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
            <ToneBadge>The job’s engineers (its owners on the board)</ToneBadge>
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
            className="h-7 w-auto text-xs"
            value=""
            onChange={(e) => e.target.value && setTo((t) => (t.includes(e.target.value) ? t : [...t, e.target.value]))}
          >
            <option value="">{people.isPending ? 'Loading people…' : 'Add a person…'}</option>
            {(people.data ?? [])
              .filter((p) => !to.includes(p.value))
              .map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
          </NativeSelect>
        </div>
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
