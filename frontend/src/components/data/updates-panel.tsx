'use client';

import { Fragment, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, MessageSquare, Paperclip, X } from 'lucide-react';
import { VoiceNote } from '@/components/boards/board-notes';
import { BallStrip } from '@/components/data/ball';
import { FileChip, FilesStrip, uploadFiles, type ItemFile } from '@/components/data/item-files';
import { PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { since } from '@/lib/format';

export type ItemType = 'order' | 'rfq' | 'vendor_job' | 'shipment' | 'milestone' | 'board_item';
type Person = { email: string; name: string };
type Unresolved = { word: string; matches: Person[] };
type Update = {
  id: number;
  files?: ItemFile[];
  parent_id?: number | null;
  author_email: string;
  author_name: string;
  body: string;
  removed?: boolean;
  mentions: Person[];
  created_at: string;
  edited_at?: string | null;
  mine?: boolean;
  replies?: Update[];
  kind?: 'comment';
  author_language?: string;
  translation?: { text: string; unsure?: string[]; to?: string } | null;
  needs_translation?: boolean;
};
/** A note from management, mixed into the same thread by time. */
type NoteItem = {
  kind: 'note';
  id: string;
  note_id: number;
  body: string;
  urgent?: boolean;
  unread?: boolean;
  voice?: { url: string; mime?: string; seconds?: number; transcript?: string } | null;
  mentions?: Person[];
  author_name?: string;
  author_email?: string;
  created_at: string;
  translation?: { text: string; unsure?: string[]; to?: string } | null;
  needs_translation?: boolean;
};
type Item = Update | NoteItem;

const BASE = '/api/v1/updates';

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await authFetch(`${BASE}${path}`, {
    method,
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    const err = new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? (null as T) : ((await res.json()) as T);
}

/** The text of a comment, as plain text: @names are bold, nothing else is ever treated as markup. */
function Body({ text }: { text: string }) {
  return (
    <p className="text-sm break-words whitespace-pre-wrap">
      {text.split(/(@\w+)/g).map((part, i) =>
        part.startsWith('@') ? (
          <strong key={i} className="font-semibold">
            {part}
          </strong>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </p>
  );
}

const escapeRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Translated words the engine wasn't sure of are underlined, so a reader knows to double-check them. */
function Marked({ text, unsure }: { text: string; unsure: string[] }) {
  const terms = unsure.filter(Boolean);
  if (!terms.length) return <Body text={text} />;
  const re = new RegExp(`(${terms.map(escapeRe).join('|')})`, 'gi');
  return (
    <p className="text-sm break-words whitespace-pre-wrap">
      {text.split(re).map((part, i) =>
        terms.some((t) => t.toLowerCase() === part.toLowerCase()) ? (
          <mark
            key={i}
            className="rounded-sm bg-warn-soft px-0.5 text-foreground underline decoration-dotted"
            title="The translation wasn’t sure of this word"
          >
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </p>
  );
}

/** One comment's words: in the reader's language first, with the original a tap away. */
function Message({ u, onTranslate }: { u: Pick<Update, 'body' | 'translation' | 'needs_translation'>; onTranslate: () => void }) {
  const [original, setOriginal] = useState(false);
  const t = u.translation;
  return (
    <div className="grid gap-1">
      {t && !original ? <Marked text={t.text} unsure={t.unsure ?? []} /> : <Body text={u.body} />}
      {t && (
        <button
          type="button"
          className="justify-self-start text-xs text-muted-foreground hover:text-foreground hover:underline"
          onClick={() => setOriginal((o) => !o)}
        >
          {original ? 'Show the translation' : 'Translated · show the original'}
        </button>
      )}
      {!t && u.needs_translation && (
        <button
          type="button"
          className="justify-self-start text-xs text-muted-foreground hover:text-foreground hover:underline"
          onClick={onTranslate}
        >
          Translate this
        </button>
      )}
    </div>
  );
}

/** A note from management, in the same thread: urgent and unread are visible, the voice note plays, one tap marks it read. */
function NoteRow({ n, onTranslate }: { n: NoteItem; onTranslate: () => void }) {
  const client = useQueryClient();
  const read = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`/api/v1/engineering-notes/${n.note_id}/read`, { method: 'POST' });
      if (!res.ok) throw new Error('That didn’t save.');
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['updates'] });
      void client.invalidateQueries({ queryKey: ['boards'] });
      void client.invalidateQueries({ queryKey: ['inbox'] });
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });
  return (
    <li className={`grid gap-1.5 rounded-lg border p-3 ${n.urgent ? 'border-bad/50' : ''} ${n.unread ? 'bg-warn-soft/40' : 'bg-muted/30'}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {n.unread && <span className="size-2 rounded-full bg-warn" aria-label="Unread" />}
        <span className="font-medium text-foreground">Note for engineers</span>
        {n.urgent && <span className="rounded-full bg-bad-soft px-2 py-0.5 font-medium text-bad">Urgent</span>}
        {n.author_name && <span>from {n.author_name}</span>}
        <span>{since(n.created_at)}</span>
      </div>
      <Message u={n} onTranslate={onTranslate} />
      {n.voice && <VoiceNote voice={n.voice as never} />}
      {n.unread && (
        <Button size="xs" variant="outline" className="justify-self-start" disabled={read.isPending} onClick={() => read.mutate()}>
          {read.isPending && <Loader2 className="animate-spin" />}
          Mark as read
        </Button>
      )}
    </li>
  );
}

/** A box that offers the people you can @mention as you type "@". */
function Composer({
  placeholder,
  submitLabel,
  initial = '',
  busy,
  onSubmit,
  onCancel,
  attach = false,
}: {
  placeholder: string;
  submitLabel: string;
  initial?: string;
  busy: boolean;
  onSubmit: (body: string, mentions: string[], files: File[]) => void;
  onCancel?: () => void;
  /** Offer a paperclip: files are attached to the comment once it is posted. */
  attach?: boolean;
}) {
  const [text, setText] = useState(initial);
  const [mentions, setMentions] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const word = /(?:^|\s)@(\w*)$/.exec(text)?.[1];
  const people = useQuery({
    queryKey: ['updates', 'people', word ?? ''],
    queryFn: () => call<Person[]>(`/people?q=${encodeURIComponent(word ?? '')}`),
    enabled: word !== undefined,
    retry: false,
  });

  const pick = (p: Person) => {
    const first = p.name.split(/\s+/)[0] || p.email;
    setText((t) => t.replace(/@\w*$/, `@${first} `));
    setMentions((m) => (m.includes(p.email) ? m : [...m, p.email]));
  };

  return (
    <form
      className="grid gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) {
          onSubmit(text.trim(), mentions, files);
          if (!initial) {
            setText('');
            setMentions([]);
            setFiles([]);
          }
        }
      }}
    >
      <Textarea
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        maxLength={4000}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
      />
      {word !== undefined && (people.data?.length ?? 0) > 0 && (
        <ul role="listbox" aria-label="People to mention" className="flex flex-wrap gap-1.5">
          {people.data!.slice(0, 8).map((p) => (
            <li key={p.email}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => pick(p)}
                className="rounded-full border bg-card px-2.5 py-1 text-xs hover:bg-accent"
              >
                {p.name}
              </button>
            </li>
          ))}
        </ul>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((f, i) => (
            <span
              key={`${f.name}-${i}`}
              className="inline-flex max-w-full items-center gap-1 rounded-full border bg-muted/50 py-0.5 pr-1 pl-2.5 text-xs"
            >
              <Paperclip className="size-3 shrink-0" aria-hidden="true" />
              <span className="min-w-0 truncate">{f.name}</span>
              <button
                type="button"
                aria-label={`Don’t attach ${f.name}`}
                className="grid size-5 place-items-center rounded-full hover:bg-muted"
                onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={busy || !text.trim()}>
          {busy && <Loader2 className="animate-spin" />}
          {submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
        {attach && (
          <>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label="Attach files"
              title="Attach files"
              onClick={() => picker.current?.click()}
            >
              <Paperclip />
            </Button>
            <input
              ref={picker}
              type="file"
              multiple
              className="sr-only"
              aria-label="Attach files"
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []);
                if (picked.length) setFiles((cur) => [...cur, ...picked]);
                if (picker.current) picker.current.value = '';
              }}
            />
          </>
        )}
        <span className="ml-auto text-xs text-muted-foreground">Type @ to tag someone</span>
      </div>
    </form>
  );
}

/** Attach files to a comment that has just been posted. A problem with the files never undoes the comment. */
async function attachAfter(itemType: ItemType, itemRef: string, commentId: number, files: File[]) {
  if (!files.length) return;
  try {
    await uploadFiles(itemType, itemRef, files, commentId);
  } catch (e) {
    toast.warning('The comment was posted, but the files weren’t attached', { description: e instanceof Error ? e.message : undefined });
  }
}

/**
 * Comments on one item, with @mentions. The same panel sits on orders, quote requests, vendor jobs,
 * shipments, milestones and board rows. Until the server has it, it says so instead of failing.
 */
function CommentsBox({ itemType, itemRef }: { itemType: ItemType; itemRef: string }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const key = ['updates', itemType, itemRef];
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [unresolved, setUnresolved] = useState<{
    id: number;
    body: string;
    items: Unresolved[];
  } | null>(null);
  const canWrite = hasRole('OPERATOR');

  const thread = useQuery({
    queryKey: key,
    queryFn: async (): Promise<Item[] | null> => {
      try {
        return await call<Item[]>(`?item_type=${itemType}&item_ref=${encodeURIComponent(itemRef)}`);
      } catch (e) {
        // No such route yet (the update isn't switched on), or no such item: say "not available", don't break the panel.
        const status = (e as { status?: number }).status;
        if (status === 404 || (status && status >= 500)) return null;
        throw e;
      }
    },
    retry: false,
  });
  const refresh = () => void client.invalidateQueries({ queryKey: key });

  const post = useMutation({
    mutationFn: async ({ body, mentions, files }: { body: string; mentions: string[]; files: File[] }) => {
      const r = await call<Update & { unresolved?: Unresolved[] }>('', 'POST', { item_type: itemType, item_ref: itemRef, body, mentions });
      await attachAfter(itemType, itemRef, r.id, files);
      return r;
    },
    onSuccess: (r, v) => {
      refresh();
      setReplyTo(null);
      if (r.unresolved?.length) setUnresolved({ id: r.id, body: v.body, items: r.unresolved });
    },
    onError: (e) => toast.error('The comment wasn’t posted', { description: e.message }),
  });
  const reply = useMutation({
    mutationFn: async ({ body, mentions, parent, files }: { body: string; mentions: string[]; parent: number; files: File[] }) => {
      const r = await call<Update & { unresolved?: Unresolved[] }>('', 'POST', {
        item_type: itemType,
        item_ref: itemRef,
        body,
        mentions,
        parent_id: parent,
      });
      await attachAfter(itemType, itemRef, r.id, files);
      return r;
    },
    onSuccess: (r, v) => {
      refresh();
      setReplyTo(null);
      if (r.unresolved?.length) setUnresolved({ id: r.id, body: v.body, items: r.unresolved });
    },
    onError: (e) => toast.error('The reply wasn’t posted', { description: e.message }),
  });
  const edit = useMutation({
    mutationFn: ({ id, body, mentions }: { id: number; body: string; mentions?: string[] }) => call(`/${id}`, 'PATCH', { body, mentions }),
    onSuccess: () => {
      refresh();
      setEditing(null);
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => call(`/${id}`, 'DELETE'),
    onSuccess: refresh,
    onError: (e) => toast.error('That couldn’t be removed', { description: e.message }),
  });

  if (thread.isPending) return <p className="text-sm text-muted-foreground">Loading comments…</p>;
  if (thread.isError) return <p className="text-sm text-bad">{thread.error.message}</p>;
  if (thread.data === null)
    return (
      <div className="grid gap-1 rounded-md border p-3">
        <PanelHeading>Comments</PanelHeading>
        <p className="text-sm text-muted-foreground">Not available yet.</p>
      </div>
    );

  const items = thread.data;
  const count = items.reduce(
    (n, u) => (u.kind === 'note' ? n + 1 : n + (u.removed ? 0 : 1) + (u.replies?.filter((r) => !r.removed).length ?? 0)),
    0,
  );

  const one = (u: Update, nested: boolean) => (
    <li key={u.id} className={nested ? 'ml-5 border-l pl-3' : ''}>
      <div className="grid gap-1">
        <div className="flex flex-wrap items-baseline gap-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{u.removed ? '' : u.author_name || u.author_email}</span>
          <span>{since(u.created_at)}</span>
          {u.edited_at && !u.removed && <span>(edited)</span>}
        </div>
        {u.removed ? (
          <p className="text-sm text-muted-foreground italic">(removed)</p>
        ) : editing === u.id ? (
          <Composer
            placeholder="Edit your comment"
            submitLabel="Save"
            initial={u.body}
            busy={edit.isPending}
            onSubmit={(body, mentions) =>
              edit.mutate({
                id: u.id,
                body,
                mentions: mentions.length ? mentions : undefined,
              })
            }
            onCancel={() => setEditing(null)}
          />
        ) : (
          <Message u={u} onTranslate={refresh} />
        )}
        {!u.removed && (u.files?.length ?? 0) > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {u.files!.map((f) => (
              <FileChip key={f.id} file={f} />
            ))}
          </div>
        )}
        {!u.removed && editing !== u.id && (
          <div className="flex gap-3 text-xs text-muted-foreground">
            {canWrite && !nested && (
              <button
                type="button"
                className="hover:text-foreground hover:underline"
                onClick={() => setReplyTo(replyTo === u.id ? null : u.id)}
              >
                Reply
              </button>
            )}
            {u.mine && (
              <button type="button" className="hover:text-foreground hover:underline" onClick={() => setEditing(u.id)}>
                Edit
              </button>
            )}
            {(u.mine || hasRole('MANAGER')) && (
              <button
                type="button"
                className="hover:text-bad hover:underline"
                onClick={() => {
                  if (window.confirm('Remove this comment?')) remove.mutate(u.id);
                }}
              >
                Remove
              </button>
            )}
          </div>
        )}
        {replyTo === u.id && (
          <Composer
            placeholder="Write a reply"
            submitLabel="Reply"
            busy={reply.isPending}
            attach
            onSubmit={(body, mentions, files) => reply.mutate({ body, mentions, parent: u.id, files })}
            onCancel={() => setReplyTo(null)}
          />
        )}
      </div>
      {(u.replies?.length ?? 0) > 0 && <ul className="mt-2 grid gap-3">{u.replies!.map((r) => one(r, true))}</ul>}
    </li>
  );

  return (
    <div className="grid gap-3 rounded-md border p-3">
      <PanelHeading>
        <span className="inline-flex items-center gap-1.5">
          <MessageSquare className="size-3.5" /> Comments
          {count > 0 && ` (${count})`}
        </span>
      </PanelHeading>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No comments yet.</p>
      ) : (
        <ul className="grid gap-4">
          {items.map((u) => (u.kind === 'note' ? <NoteRow key={u.id} n={u} onTranslate={refresh} /> : one(u, false)))}
        </ul>
      )}

      {unresolved && (
        <div role="status" className="grid gap-2 rounded-md bg-warn-soft p-2.5 text-sm">
          {unresolved.items.map((u) => (
            <div key={u.word} className="grid gap-1">
              <span>{u.matches.length ? `Which ${u.word}?` : `Nobody called “${u.word}” was found.`}</span>
              <span className="flex flex-wrap gap-1.5">
                {u.matches.map((p) => (
                  <button
                    key={p.email}
                    type="button"
                    className="rounded-full border bg-card px-2.5 py-1 text-xs hover:bg-accent"
                    onClick={() => {
                      edit.mutate({
                        id: unresolved.id,
                        body: unresolved.body,
                        mentions: [p.email],
                      });
                      setUnresolved(null);
                    }}
                  >
                    {p.name}
                  </button>
                ))}
              </span>
            </div>
          ))}
          <button type="button" className="justify-self-start text-xs underline" onClick={() => setUnresolved(null)}>
            Leave it as it is
          </button>
        </div>
      )}

      {canWrite && (
        <Composer
          placeholder="Write a comment"
          submitLabel="Comment"
          busy={post.isPending}
          attach
          onSubmit={(body, mentions, files) => post.mutate({ body, mentions, files })}
        />
      )}
    </div>
  );
}

/** How many comments each listed item has, for the little badge on a row. Empty until the server has comments. */
export function useCommentCounts(itemType: ItemType, refs: string[]) {
  const list = [...new Set(refs.filter(Boolean))].sort().slice(0, 200);
  return useQuery({
    queryKey: ['updates', 'counts', itemType, list.join(',')],
    queryFn: async (): Promise<Record<string, number>> => {
      try {
        return await call<Record<string, number>>(`/counts?item_type=${itemType}&refs=${encodeURIComponent(list.join(','))}`);
      } catch {
        return {};
      }
    },
    enabled: list.length > 0,
    staleTime: 30_000,
    retry: false,
  });
}

export function CommentCount({ n }: { n?: number }) {
  if (!n) return null;
  return (
    <span
      className="ml-1.5 inline-flex items-center gap-0.5 text-xs text-muted-foreground"
      title={`${n} ${n === 1 ? 'comment' : 'comments'}`}
    >
      <MessageSquare className="size-3" aria-hidden="true" />
      <span className="tabular-nums">{n}</span>
      <span className="sr-only">{n === 1 ? 'comment' : 'comments'}</span>
    </span>
  );
}

/** Who is it waiting on?, and the comments: everything people say and wait on for one item. */
export function UpdatesPanel({ itemType, itemRef }: { itemType: ItemType; itemRef: string }) {
  return (
    <div className="grid gap-3">
      <BallStrip itemType={itemType} itemRef={itemRef} />
      <FilesStrip itemType={itemType} itemRef={itemRef} />
      <CommentsBox itemType={itemType} itemRef={itemRef} />
    </div>
  );
}
