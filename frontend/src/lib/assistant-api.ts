import { authFetch } from '@/lib/api';

/** What the assistant puts on screen besides words: a record, a document, a draft, a change to confirm. */
export type CardButton = {
  label: string;
  action: 'open' | 'download' | 'confirm' | 'cancel' | 'edit' | 'undo' | 'push';
  href?: string;
  action_id?: string;
};
export type Card = {
  kind: 'record' | 'document' | 'draft' | 'proposal' | 'push' | 'info';
  title: string;
  summary?: string;
  buttons: CardButton[];
  record?: { type: string; id: string };
  action_id?: string;
};
export type Plan = { action_ids: string[]; count: number; confirm_all?: boolean };
export type ChatMessage = { role: 'user' | 'assistant'; content: string };
export type ChatReply = { reply: string; cards?: Card[]; plan?: Plan | null; model?: string };
export type VoiceReply = {
  transcript: string;
  reply_text: string;
  spoken_text?: string;
  spoken?: { mode: 'browser' | 'off'; text?: string; lang?: string };
  action_cards?: Card[];
  plan?: Plan | null;
  voice_intent?: string | null;
  held?: { hint?: string }[];
  tool_actions_executed?: { summary?: string; status?: string }[];
};
export type Decision = { status: string; result?: { done?: string; error?: string } };

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const res = await authFetch(`/api/v1${path}`, init);
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown, signal?: AbortSignal): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
});

/** Only the recent conversation goes to the model: keeps answers on topic and fast. */
export const CONTEXT = 12;

export const chat = (messages: ChatMessage[], signal?: AbortSignal) =>
  send<ChatReply>('/assistant/chat', json('POST', { messages: messages.slice(-CONTEXT) }, signal));

/**
 * The same answer as `chat`, but each step is reported as it starts ("Checking vendor jobs…"), so a
 * slow answer shows progress. Falls back to the plain route if the stream isn't there.
 */
export async function chatStream(messages: ChatMessage[], onStep: (text: string) => void, signal?: AbortSignal): Promise<ChatReply> {
  const res = await authFetch('/api/v1/assistant/chat/stream', json('POST', { messages: messages.slice(-CONTEXT) }, signal));
  if (res.status === 404 || !res.body) return chat(messages, signal);
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Events end with a blank line; a line starting with ":" is a keep-alive.
    let cut: number;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const line = block.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      const event = JSON.parse(line.slice(5).trim()) as { type: string; text?: string; message?: string } & Partial<ChatReply>;
      if (event.type === 'step' && event.text) onStep(event.text);
      else if (event.type === 'error') throw new Error(event.message || 'The assistant isn’t available right now.');
      else if (event.type === 'done') return event as ChatReply;
    }
  }
  throw new Error('The answer was cut short. Try again.');
}

export const decide = (id: string, confirm: boolean) =>
  send<Decision>(`/assistant/actions/${encodeURIComponent(id)}`, json('POST', { confirm }));

export const decideMany = (ids: string[], confirm: boolean) =>
  send<{ results: { id: string; status: string; error?: string | null }[] }>('/assistant/actions/batch', json('POST', { ids, confirm }));

export const editDraft = (id: string, changes: { to?: string; subject?: string; body?: string }) =>
  send<unknown>(`/assistant/actions/${encodeURIComponent(id)}`, json('PATCH', { changes }));

/** A spoken task: the recording (or dictated words), the chat so far, and the changes waiting on a yes. */
export function voiceCommand(opts: {
  file?: Blob;
  filename?: string;
  transcript?: string;
  messages: ChatMessage[];
  confirmIds: string[];
  signal?: AbortSignal;
}) {
  const form = new FormData();
  if (opts.file) form.append('file', opts.file, opts.filename ?? 'voice.webm');
  if (opts.transcript) form.append('transcript', opts.transcript);
  form.append('messages', JSON.stringify(opts.messages.slice(-CONTEXT)));
  form.append('confirm_ids', opts.confirmIds.join(','));
  return send<VoiceReply>('/assistant/voice-command', { method: 'POST', body: form, signal: opts.signal });
}

/** A protected file (a packing slip) opened in a new tab, where it can be read or printed. */
export async function openDocument(href: string) {
  const res = await authFetch(href);
  if (!res.ok) throw new Error(`Couldn’t open it (${res.status}).`);
  const url = URL.createObjectURL(await res.blob());
  window.open(url, '_blank', 'noopener');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
