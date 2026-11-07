'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowUp, AudioLines, Loader2, Square, X } from 'lucide-react';
import { DictateButton, addDictation } from '@/components/data/voice';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';
import {
  chatStream,
  decide,
  decideMany,
  editDraft,
  openDocument,
  type Card,
  type CardButton,
  type ChatMessage,
  type Plan,
  type VoiceReply,
} from '@/lib/assistant-api';
import { cn } from '@/lib/utils';
import { useVoiceAgent, type VoiceState } from './use-voice-agent';

type Msg = {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  cards?: Card[];
  plan?: Plan | null;
  error?: boolean;
  thinking?: boolean;
};
type CardState = { status: 'waiting' | 'done' | 'cancelled' | 'failed'; text?: string };

const STARTERS = ['What is late?', 'Which vendors need chasing?', 'Open quote requests', 'What needs me today?'];
const ASKING: Record<string, string> = { confirm: 'Sending…', push: 'Sending…', cancel: 'Cancelling…', undo: 'Undoing…' };

let counter = 0;
const nextId = () => ++counter;

/**
 * Chat first: ask in your own words, see the answer and any cards at once, confirm changes with a
 * click or by voice. The model never changes anything itself; every change waits for a yes.
 */
export function AssistantChat({ initialPrompt = '' }: { initialPrompt?: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState(initialPrompt);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState('');
  const [cards, setCards] = useState<Record<string, CardState>>({});
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const sent = useRef(false);

  const waitingIds = useCallback(
    (list: Msg[]) =>
      [...new Set(list.flatMap((m) => (m.cards ?? []).map((c) => c.action_id).filter((x): x is string => Boolean(x))))].filter(
        (id) => (cards[id]?.status ?? 'waiting') === 'waiting',
      ),
    [cards],
  );
  const history = (list: Msg[]): ChatMessage[] =>
    list.filter((m) => !m.error && !m.thinking && m.content).map((m) => ({ role: m.role, content: m.content }));

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [messages, busy]);

  async function send(text: string) {
    const said = text.trim();
    if (!said || busy) return;
    const mine = nextId();
    const before = messages;
    // The words appear at once; the answer fills in when it arrives.
    setMessages([...before, { id: mine, role: 'user', content: said }, { id: mine + 0.5, role: 'assistant', content: '', thinking: true }]);
    setDraft('');
    setStep('');
    setBusy(true);
    abort.current = new AbortController();
    try {
      const res = await chatStream([...history(before), { role: 'user', content: said }], setStep, abort.current.signal);
      setMessages((m) =>
        m.map((x) => (x.thinking ? { id: x.id, role: 'assistant', content: res.reply, cards: res.cards, plan: res.plan } : x)),
      );
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setMessages((m) => m.map((x) => (x.thinking ? { id: x.id, role: 'assistant', content: (e as Error).message, error: true } : x)));
    } finally {
      setBusy(false);
    }
  }

  // Another page can open the assistant with a question ready; ask it once.
  useEffect(() => {
    if (initialPrompt && !sent.current) {
      sent.current = true;
      void send(initialPrompt);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Stop waiting: the question goes back in the box so nothing is lost. */
  function stop() {
    abort.current?.abort();
    setMessages((m) => {
      const lastUser = [...m].reverse().find((x) => x.role === 'user');
      if (lastUser) setDraft(lastUser.content);
      return m.filter((x) => !x.thinking && x !== lastUser);
    });
    setBusy(false);
  }

  const mark = (ids: string[], s: CardState) => setCards((c) => ({ ...c, ...Object.fromEntries(ids.map((id) => [id, s])) }));
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['inbox'] });
    void client.invalidateQueries({ queryKey: ['vendors'] });
  };

  async function run(card: Card, b: CardButton) {
    const id = b.action_id ?? card.action_id;
    try {
      if (b.action === 'open' && b.href) return router.push(b.href);
      if (b.action === 'download' && b.href) return await openDocument(b.href);
      if (!id) return;
      if (b.action === 'confirm' || b.action === 'push') {
        mark([id], { status: 'waiting', text: ASKING.confirm });
        const r = await decide(id, true);
        mark(
          [id],
          r.status === 'FAILED'
            ? { status: 'failed', text: r.result?.error ?? 'It didn’t work.' }
            : { status: 'done', text: r.result?.done ?? 'Done.' },
        );
      } else if (b.action === 'cancel' || b.action === 'undo') {
        mark([id], { status: 'waiting', text: ASKING.cancel });
        await decide(id, false);
        mark([id], { status: 'cancelled', text: 'Cancelled. Nothing was changed.' });
      }
      refresh();
    } catch (e) {
      if (id) mark([id], { status: 'failed', text: (e as Error).message });
    }
  }

  async function runPlan(plan: Plan, confirm: boolean) {
    mark(plan.action_ids, { status: 'waiting', text: confirm ? 'Sending…' : 'Cancelling…' });
    try {
      const { results } = await decideMany(plan.action_ids, confirm);
      for (const r of results)
        mark(
          [r.id],
          r.status === 'FAILED' || r.status === 'SKIPPED'
            ? { status: 'failed', text: r.error ?? r.status }
            : { status: confirm ? 'done' : 'cancelled', text: confirm ? 'Done.' : 'Cancelled.' },
        );
      refresh();
    } catch (e) {
      mark(plan.action_ids, { status: 'failed', text: (e as Error).message });
    }
  }

  // Voice: the conversation continues in the same thread.
  const voice = useVoiceAgent({
    context: () => ({ messages: history(messages), confirmIds: waitingIds(messages) }),
    onTurn: (r: VoiceReply) => {
      // What was just said settles the cards that were waiting, and "print it" opens the last document.
      const waiting = waitingIds(messages);
      if (r.voice_intent === 'confirm') mark(waiting, { status: 'done', text: 'Done.' });
      if (r.voice_intent === 'cancel') mark(waiting, { status: 'cancelled', text: 'Cancelled. Nothing was changed.' });
      if (r.voice_intent === 'print') {
        const doc = [...messages]
          .reverse()
          .flatMap((m) => [...(m.cards ?? [])].reverse())
          .find((c) => c.kind === 'document');
        const href = doc?.buttons.find((b) => b.href)?.href;
        if (href) void openDocument(href).catch((e: Error) => toast.error('Couldn’t open it', { description: e.message }));
      }
      if (r.voice_intent === 'confirm' || r.voice_intent === 'cancel') refresh();
      setMessages((m) => [
        ...m,
        ...(r.transcript ? [{ id: nextId(), role: 'user' as const, content: r.transcript }] : []),
        { id: nextId(), role: 'assistant' as const, content: r.reply_text, cards: r.action_cards, plan: r.plan },
      ]);
    },
    onError: (message) => toast.error('Voice didn’t work', { description: message }),
  });

  return (
    <div className="mx-auto grid h-[calc(100svh-13rem)] min-h-96 max-w-3xl grid-rows-[minmax(0,1fr)_auto] gap-3">
      <div className="overflow-y-auto pr-1" aria-live="polite">
        {messages.length === 0 ? (
          <div className="grid h-full content-center justify-items-center gap-4 text-center">
            <p className="text-lg font-medium">What do you need?</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Ask in your own words, or tap the wave to talk. Changes always wait for your OK.
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {STARTERS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-full border bg-card px-3 py-1.5 text-sm hover:bg-accent"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="grid gap-4 py-2">
            {messages.map((m) => (
              <li key={m.id} className={cn('grid gap-2', m.role === 'user' && 'justify-items-end')}>
                {m.thinking ? (
                  <Thinking step={step} />
                ) : (
                  <p
                    className={cn(
                      'max-w-[85%] rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed whitespace-pre-wrap',
                      m.role === 'user' ? 'bg-foreground text-background' : m.error ? 'bg-bad-soft text-bad' : 'bg-muted',
                    )}
                  >
                    {m.content}
                  </p>
                )}
                {m.plan && m.plan.count > 1 && <PlanBar plan={m.plan} state={cards} onRun={runPlan} />}
                {(m.cards ?? []).map((c, i) => (
                  <CardView
                    key={i}
                    card={c}
                    state={cards[c.action_id ?? '']}
                    onButton={run}
                    onEdited={() => mark([c.action_id!], { status: 'waiting' })}
                  />
                ))}
              </li>
            ))}
            <div ref={end} />
          </ol>
        )}
      </div>

      <form
        className="flex items-end gap-2 rounded-3xl border bg-card p-2 shadow-sm focus-within:ring-3 focus-within:ring-ring/40"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <textarea
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send(draft);
            }
          }}
          placeholder="Ask anything…"
          aria-label="Your message"
          className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none field-sizing-content"
        />
        <DictateButton compact onText={(t) => setDraft((cur) => addDictation(cur, t))} />
        {busy ? (
          <Button type="button" size="icon" variant="outline" aria-label="Stop" onClick={stop}>
            <Square />
          </Button>
        ) : draft.trim() ? (
          <Button type="submit" size="icon" aria-label="Send">
            <ArrowUp />
          </Button>
        ) : (
          <Button type="button" size="icon" aria-label="Talk to the assistant" onClick={voice.start}>
            <AudioLines />
          </Button>
        )}
      </form>

      {voice.state !== 'off' && (
        <VoiceScreen state={voice.state} level={voice.level} heard={voice.heard} said={voice.said} onTap={voice.tap} onClose={voice.stop} />
      )}
    </div>
  );
}

function Thinking({ step }: { step: string }) {
  // Said only when true: the model can take several seconds when it checks more than one thing.
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  // The real step when the server reports one; otherwise only what is true from the clock.
  const note = step || (seconds >= 10 ? 'Still working. This one needs a few checks.' : seconds >= 4 ? 'Looking it up…' : '');
  return (
    <span className="inline-flex items-center gap-2 rounded-2xl bg-muted px-4 py-3" role="status" aria-label="Thinking">
      <span className="inline-flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span key={i} className="size-1.5 animate-pulse rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 160}ms` }} />
        ))}
      </span>
      {note && <span className="text-sm text-muted-foreground">{note}</span>}
    </span>
  );
}

function PlanBar({ plan, state, onRun }: { plan: Plan; state: Record<string, CardState>; onRun: (p: Plan, confirm: boolean) => void }) {
  const open = plan.action_ids.filter((id) => (state[id]?.status ?? 'waiting') === 'waiting' && !state[id]?.text);
  if (!open.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card px-3 py-2 text-sm">
      <span>{open.length} changes are waiting for you.</span>
      <Button size="sm" onClick={() => onRun({ ...plan, action_ids: open }, true)}>
        Confirm all {open.length}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => onRun({ ...plan, action_ids: open }, false)}>
        Cancel all
      </Button>
    </div>
  );
}

function CardView({
  card,
  state,
  onButton,
  onEdited,
}: {
  card: Card;
  state?: CardState;
  onButton: (c: Card, b: CardButton) => void;
  onEdited: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const settled = state && state.status !== 'waiting';
  const working = state?.status === 'waiting' && state.text;
  return (
    <div className={cn('grid max-w-[85%] gap-2 rounded-2xl border bg-card p-3 text-sm', card.kind === 'push' && 'border-warn/50')}>
      <span className="font-medium">{card.title}</span>
      {card.summary && <p className="text-muted-foreground whitespace-pre-wrap">{card.summary}</p>}
      {state?.text && (
        <p
          className={cn('text-xs', state.status === 'failed' ? 'text-bad' : state.status === 'done' ? 'text-ok' : 'text-muted-foreground')}
        >
          {state.text}
        </p>
      )}
      {!settled && !working && !editing && (
        <div className="flex flex-wrap gap-2">
          {card.buttons.map((b) => (
            <Button
              key={b.label}
              size="sm"
              variant={b.action === 'confirm' || b.action === 'push' ? 'default' : 'outline'}
              onClick={() => (b.action === 'edit' ? setEditing(true) : onButton(card, b))}
            >
              {b.label}
            </Button>
          ))}
        </div>
      )}
      {working && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      {editing && card.action_id && (
        <DraftEditor
          id={card.action_id}
          onDone={(saved) => {
            setEditing(false);
            if (saved) onEdited();
          }}
        />
      )}
    </div>
  );
}

/** Change the email before it goes: who, subject and words. Saving keeps it waiting for Send. */
function DraftEditor({ id, onDone }: { id: string; onDone: (saved: boolean) => void }) {
  const { data } = useQuery({
    queryKey: ['assistant', 'draft', id],
    queryFn: async () => {
      const res = await authFetch('/api/v1/assistant/actions');
      const list = ((await res.json()) as { actions: { id: string; args?: { to?: string; subject?: string; body?: string } }[] }).actions;
      return list.find((a) => a.id === id)?.args ?? {};
    },
  });
  const [form, setForm] = useState<{ to?: string; subject?: string; body?: string }>({});
  const [saving, setSaving] = useState(false);
  const value = (k: 'to' | 'subject' | 'body') => form[k] ?? data?.[k] ?? '';
  if (!data) return <p className="text-xs text-muted-foreground">Loading the draft…</p>;
  return (
    <form
      className="grid gap-2"
      onSubmit={async (e) => {
        e.preventDefault();
        setSaving(true);
        try {
          await editDraft(id, { to: value('to'), subject: value('subject'), body: value('body') });
          toast.success('Saved. It still waits for your OK to send.');
          onDone(true);
        } catch (err) {
          toast.error('Couldn’t save it', { description: (err as Error).message });
          setSaving(false);
        }
      }}
    >
      <Input aria-label="To" value={value('to')} onChange={(e) => setForm({ ...form, to: e.target.value })} />
      <Input aria-label="Subject" value={value('subject')} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
      <Textarea aria-label="Message" rows={5} value={value('body')} onChange={(e) => setForm({ ...form, body: e.target.value })} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving}>
          {saving && <Loader2 className="animate-spin" />}
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => onDone(false)}>
          Back
        </Button>
      </div>
    </form>
  );
}

const LABEL: Record<VoiceState, string> = { off: '', listening: 'Listening…', thinking: 'Thinking…', speaking: 'Tap to interrupt' };

/** The voice screen: one big circle that moves with your voice, what it heard, and a way out. */
function VoiceScreen({
  state,
  level,
  heard,
  said,
  onTap,
  onClose,
}: {
  state: VoiceState;
  level: number;
  heard: string;
  said: string;
  onTap: () => void;
  onClose: () => void;
}) {
  const scale = state === 'listening' ? 1 + level * 0.6 : state === 'speaking' ? 1.1 : 1;
  return (
    <div
      role="dialog"
      aria-label="Talking with the assistant"
      className="fixed inset-0 z-50 grid grid-rows-[auto_1fr_auto] bg-background/95 p-6 backdrop-blur"
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
    >
      <div className="flex justify-end">
        <Button size="icon" variant="ghost" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="grid content-center justify-items-center gap-6">
        <button
          type="button"
          onClick={onTap}
          aria-label={LABEL[state]}
          className={cn(
            'size-40 rounded-full transition-transform duration-100 ease-out outline-none focus-visible:ring-4 focus-visible:ring-ring/50',
            state === 'thinking' ? 'animate-pulse bg-muted-foreground/40' : state === 'speaking' ? 'bg-info' : 'bg-foreground',
          )}
          style={{ transform: `scale(${scale})` }}
        />
        <p className="text-lg font-medium" role="status">
          {LABEL[state]}
        </p>
        <p className="max-w-md text-center text-sm text-muted-foreground">{state === 'speaking' ? said : heard}</p>
      </div>
      <p className="text-center text-xs text-muted-foreground">
        Say “confirm” or “cancel” when changes are waiting. Emails need “send it”.
      </p>
    </div>
  );
}
