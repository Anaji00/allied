'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { authFetch } from '@/lib/api';
import { api, ok } from '@/lib/api/client';
import { postForm } from '@/components/data/voice';
import type { Recording } from '@/hooks/use-recorder';
import { optimistic } from '@/lib/optimistic';

export type Lang = 'zh' | 'vi';
export const LANGS: Record<Lang, { label: string; short: string }> = {
  zh: { label: '中文 (Mandarin)', short: '中文' },
  vi: { label: 'Tiếng Việt (Vietnamese)', short: 'Tiếng Việt' },
};

/** A note from management to the engineers on one job. The English text is the one that counts. */
export type Note = {
  id: number;
  board_id: string;
  item_id: string;
  item_name: string;
  author: string;
  text: string;
  /** Asked-for translations; `unsure` lists terms the translator wasn't sure of, `error` when it failed. */
  translations: Partial<Record<Lang, { text: string | null; unsure?: string[]; error?: string }>>;
  recipients: string[];
  urgent: boolean;
  /** Who has read it, and when. */
  read_by: Record<string, string>;
  unread: string[];
  voice: { url: string; mime?: string | null; seconds?: number | null; transcript?: string | null } | null;
  created_at: string | null;
};

export type NoteIn = { item_id: string; text: string; languages: Lang[]; urgent: boolean; to?: string[] };

const notesKey = (boardId: string) => ['engineering-notes', boardId] as const;

/** Every note on a board in the last 90 days, newest first; the cards pick theirs out. */
export function useBoardNotes(boardId: string, enabled: boolean) {
  return useQuery({
    queryKey: notesKey(boardId),
    queryFn: async () =>
      (await ok(api.GET('/api/v1/engineering-notes', { params: { query: { board_id: boardId } } }))) as unknown as Note[],
    enabled,
    refetchInterval: 60_000,
  });
}

/** Notes on this item sent to `me` that `me` hasn't read. */
export const unreadFor = (notes: Note[] | undefined, itemId: string, me: string) =>
  (notes ?? []).filter((n) => n.item_id === itemId && n.unread.includes(me)).length;

export function usePostNote(boardId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ voice, ...note }: NoteIn & { voice?: Recording | null }) => {
      if (!voice) return (await ok(api.POST('/api/v1/engineering-notes', { body: note }))) as unknown as Note;
      const form = new FormData();
      form.append('file', voice.blob, voice.filename);
      form.append('item_id', note.item_id);
      form.append('text', note.text);
      form.append('languages', note.languages.join(','));
      form.append('urgent', String(note.urgent));
      form.append('to', (note.to ?? []).join(','));
      return postForm<Note>('/engineering-notes/voice', form);
    },
    onSuccess: (n) =>
      toast.success(`Sent to ${n.recipients.length} ${n.recipients.length === 1 ? 'person' : 'people'}`, {
        description: n.urgent ? 'Urgent: also sent as a text and a phone push.' : undefined,
      }),
    onError: (e) => toast.error('The note wasn’t sent', { description: e.message }),
    onSettled: () => client.invalidateQueries({ queryKey: notesKey(boardId) }),
  });
}

/** Marks a note read for this person; the receipt shows at once. */
export function useMarkRead(boardId: string, me: string) {
  const client = useQueryClient();
  const o = optimistic(client, notesKey(boardId), (old: Note[], id: number) =>
    old.map((n) =>
      n.id === id ? { ...n, read_by: { ...n.read_by, [me]: new Date().toISOString() }, unread: n.unread.filter((e) => e !== me) } : n,
    ),
  );
  return useMutation({
    mutationFn: async (id: number) => ok(api.POST('/api/v1/engineering-notes/{note_id}/read', { params: { path: { note_id: id } } })),
    onMutate: o.onMutate,
    onError: (e, _id, ctx) => {
      o.rollback(ctx);
      toast.error(e.message);
    },
    onSettled: o.onSettled,
  });
}

/** People who can be sent a note: the board's people with an email (from the rule options). */
export function useNotePeople(role: string, enabled: boolean) {
  return useQuery({
    queryKey: ['autopilot', 'recipe-options', role],
    queryFn: async () =>
      (
        (await ok(api.GET('/api/v1/autopilot/recipes/options', { params: { query: { board_role: role } } }))) as unknown as {
          people?: { value: string; label: string }[];
        }
      ).people?.filter((p) => p.value.includes('@')) ?? [],
    enabled,
    staleTime: 10 * 60_000,
  });
}

/**
 * A voice note's recording as a blob URL. `<audio src>` can't send the sign-in header, so it is
 * fetched like any other API call. The caller revokes the URL when done.
 */
export async function voiceUrl(url: string) {
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`Couldn’t load the recording (${res.status}).`);
  return URL.createObjectURL(await res.blob());
}
