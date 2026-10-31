'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowUp, Loader2, Paperclip, X } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useAuth } from '@/context/AuthContext';
import { executeSystemAction, fetchCacheSummary, fetchRAGSources, fetchRAGTemplates, queryRAGChat, uploadChatPDF } from '@/lib/api';
import { words } from '@/lib/format';
import { copyText } from './ai-output';


type Citation = { source_id: string; source_type: string; title: string; excerpt: string; relevance_score: number; metadata?: Record<string, unknown> };
type Action = { action_type: string; button_label: string; prefill_data: Record<string, any> };
type Message =
  | { role: 'user'; content: string; attachment?: string }
  | { role: 'assistant'; content: string; citations?: Citation[]; actions?: Action[]; model?: string; error?: boolean; data?: unknown };

const SOURCE_TEXT: Record<string, string> = {
  HISTORICAL_ESTIMATE: 'Past estimate',
  WELDING_WPS: 'Welding procedure',
  SUPPLIER_AGREEMENT: 'Supplier agreement',
  ASTM_STANDARD: 'ASTM standard',
  MTR_CERT: 'Mill certificate',
  CACHED_ORDER: 'Order',
  ITEM_MASTER: 'Catalogue item',
  CUSTOMER_MASTER: 'Customer',
  VENDOR_MASTER: 'Supplier',
  INTERNAL_DOC: 'Internal document',
  SCANNED_DOC: 'Scanned document',
};

const clean = (t: string) => String(t || '').replace(/[\p{Extended_Pictographic}️]/gu, '').trim();
const history = (m: Message) => (m.role === 'user' ? m.content : m.content);

/** Where an order-shaped suggestion should go: the New order form, filled in, never created blind. */
function orderLink(p: Record<string, any>) {
  const line = p.line_items?.[0] ?? {};
  const params = new URLSearchParams({ new: 'true' });
  const customer = p.customer_name;
  const part = p.item_id ?? line.raw_item_text ?? line.item_id;
  const price = p.price ?? p.unit_price ?? line.unit_price;
  if (customer) params.set('customer', String(customer));
  if (part) params.set('part', String(part));
  if (price != null && price !== '') params.set('price', String(price));
  return `/orders?${params}`;
}

export function Ask({ initialPrompt = '' }: { initialPrompt?: string }) {
  const router = useRouter();
  const { hasRole } = useAuth();
  const canAct = hasRole('OPERATOR');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState(initialPrompt);
  const [file, setFile] = useState<File | null>(null);
  const [useModel, setUseModel] = useState(true);
  const [confirming, setConfirming] = useState<Action | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const templates = useQuery({ queryKey: ['rag', 'templates'], queryFn: async () => (await fetchRAGTemplates()) as { id: string; title: string; prompt: string }[], staleTime: Infinity });
  const info = useQuery({
    queryKey: ['rag', 'info'],
    queryFn: async () => {
      const [kb, cache] = await Promise.allSettled([fetchRAGSources(), fetchCacheSummary()]);
      return { kb: kb.status === 'fulfilled' ? kb.value : null, cache: cache.status === 'fulfilled' ? cache.value : null };
    },
    staleTime: 5 * 60_000,
  });

  const push = (m: Message) => setMessages((prev) => [...prev, m]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const ask = useMutation({
    mutationFn: async ({ q, attached }: { q: string; attached: File | null }) => {
      let pdf: string | null = null;
      if (attached) pdf = ((await uploadChatPDF(attached)) as { file_path: string }).file_path;
      return (await queryRAGChat({
        query: q || 'Please read the attached document.',
        conversation_history: messages.filter((m) => !('error' in m && m.error)).map((m) => ({ role: m.role, content: history(m) })),
        dev_bypass: !useModel,
        agentic_mode: false,
        uploaded_pdf_path: pdf,
      })) as { answer: string; citations?: Citation[]; suggested_actions?: Action[]; model_used?: string };
    },
    onSuccess: (res) =>
      push({ role: 'assistant', content: res.answer, citations: res.citations ?? [], actions: res.suggested_actions ?? [], model: res.model_used }),
    onError: (e) => push({ role: 'assistant', error: true, content: `Sorry, I couldn’t get an answer. ${e.message}` }),
  });

  const run = useMutation({
    mutationFn: async (a: Action) => (await executeSystemAction(a.action_type, a.prefill_data)) as { status?: string; data?: unknown; detail?: string; message?: string },
    onSuccess: (res, a) => {
      const done = !res.status || res.status === 'success';
      push({
        role: 'assistant',
        content: done ? `Done: ${clean(a.button_label)}.` : `${clean(a.button_label)}: ${res.detail || res.message || res.status}`,
        data: res.data !== undefined ? res.data : res,
      });
      setConfirming(null);
    },
    onError: (e, a) => {
      push({ role: 'assistant', error: true, content: `${clean(a.button_label)} didn’t work. ${e.message}` });
      setConfirming(null);
    },
  });

  function send(text?: string) {
    const q = (text ?? input).trim();
    if ((!q && !file) || ask.isPending) return;
    push({ role: 'user', content: q || 'Please read the attached document.', attachment: file?.name });
    ask.mutate({ q, attached: file });
    setInput('');
    setFile(null);
  }

  function onAction(a: Action) {
    const p = a.prefill_data ?? {};
    switch (a.action_type) {
      case 'STAGE_SAGE_ORDER':
      case 'EXECUTE_END_TO_END_ORDER':
      case 'RUN_DRY_RUN':
        router.push(orderLink(p));
        return;
      case 'STAGE_OPERATIONS':
        router.push('/orders');
        return;
      case 'VIEW_MTR':
        router.push(`/compliance/vault?tab=certificates${p.heat_number ? `&heat=${encodeURIComponent(p.heat_number)}` : ''}`);
        return;
      case 'COPY_PROPOSAL':
        if (p.proposal_text) void copyText(String(p.proposal_text));
        else toast.error('There’s no proposal text to copy');
        return;
      case 'EXECUTE_CSV_IMPORT':
        if (!p.csv_text) {
          router.push('/operations?tab=lists');
          return;
        }
        break;
    }
    setConfirming(a);
  }

  const busy = ask.isPending || run.isPending;

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <section aria-label="Conversation" className="grid h-[min(70svh,720px)] grid-rows-[minmax(0,1fr)_auto] overflow-hidden rounded-lg border bg-card">
        <div ref={listRef} className="grid content-start gap-4 overflow-y-auto p-4">
          {messages.length === 0 ? (
            <div className="grid gap-3 py-6">
              <h2 className="text-base font-semibold">What do you need to know?</h2>
              <p className="max-w-prose text-sm text-muted-foreground">
                Ask about steel grades, welding procedures, past prices, supplier terms or mill certificates. Answers list the records
                they came from.
              </p>
              {(templates.data?.length ?? 0) > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {templates.data!.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      title={t.prompt}
                      onClick={() => send(t.prompt)}
                      className="rounded-full border px-3 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      {t.title}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            messages.map((m, i) => <Bubble key={i} message={m} onAction={onAction} busy={busy} />)
          )}
          {busy && (
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Working on it…
            </p>
          )}
        </div>

        <form
          className="grid gap-2 border-t p-3"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          {file && (
            <span className="inline-flex items-center gap-1 justify-self-start rounded bg-muted px-2 py-0.5 text-xs">
              {file.name}
              <button type="button" aria-label="Remove the attachment" onClick={() => setFile(null)}>
                <X className="size-3" />
              </button>
            </span>
          )}
          <div className="flex items-end gap-2">
            {canAct && (
              <>
                <Button type="button" variant="ghost" size="icon-sm" aria-label="Attach a PDF" disabled={busy} onClick={() => fileRef.current?.click()}>
                  <Paperclip />
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".pdf"
                  className="hidden"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    e.target.value = '';
                  }}
                />
              </>
            )}
            <textarea
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="Ask a question…"
              aria-label="Your question"
              className="max-h-40 min-h-8 flex-1 resize-none rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none field-sizing-content focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
            <Button type="submit" size="icon-sm" aria-label="Send" disabled={busy || (!input.trim() && !file)}>
              <ArrowUp />
            </Button>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={useModel} onChange={(e) => setUseModel(e.target.checked)} />
            Use the AI model when it’s running (off gives quick, fixed answers)
          </label>
        </form>
      </section>

      <aside className="grid gap-3 rounded-lg border bg-card p-4 text-[13px]">
        <h2 className="text-sm font-semibold">What it can search</h2>
        <Facts
          items={[
            ['Reference documents', String(info.data?.kb?.total_documents ?? '–')],
            ['Recent orders', String(info.data?.cache?.cached_orders_count ?? '–')],
            ['Suppliers', String(info.data?.cache?.registered_vendors_count ?? '–')],
            ['Mill cert heats', String(info.data?.cache?.indexed_heat_numbers_count ?? '–')],
          ]}
        />
        <p className="text-xs text-muted-foreground">
          Suggested next steps never run by themselves. Orders open in the New order form; anything else asks you first.
        </p>
      </aside>

      <Dialog open={Boolean(confirming)} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirming ? clean(confirming.button_label) : ''}?</DialogTitle>
            <DialogDescription>
              {canAct
                ? 'The assistant suggested this. Check what it will use before running it.'
                : 'Running suggested steps needs an operator account.'}
            </DialogDescription>
          </DialogHeader>
          {confirming && (
            <div className="max-h-72 overflow-y-auto rounded-md border bg-muted/40 p-3">
              <Facts
                items={[
                  ['Step', words(confirming.action_type)],
                  ...Object.entries(confirming.prefill_data ?? {}).map(
                    ([k, v]) => [words(k), typeof v === 'object' ? JSON.stringify(v) : String(v)] as [string, string],
                  ),
                ]}
              />
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
            {canAct && (
              <Button disabled={run.isPending} onClick={() => confirming && run.mutate(confirming)}>
                {run.isPending && <Loader2 className="animate-spin" />}
                Run it
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Bubble({ message: m, onAction, busy }: { message: Message; onAction: (a: Action) => void; busy: boolean }) {
  if (m.role === 'user') {
    return (
      <div className="ml-auto grid max-w-[85%] gap-1 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
        <p className="whitespace-pre-wrap">{m.content}</p>
        {m.attachment && <span className="text-xs opacity-75">Attached: {m.attachment}</span>}
      </div>
    );
  }
  if (m.error) {
    return (
      <p role="alert" className="max-w-[85%] rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">
        {m.content}
      </p>
    );
  }
  const citations = m.citations ?? [];
  const actions = m.actions ?? [];
  return (
    <div className="grid max-w-[92%] gap-2 text-sm">
      <RichText text={m.content} />
      {m.data !== undefined && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">Show the result</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted/50 p-2 font-mono">{JSON.stringify(m.data, null, 2)}</pre>
        </details>
      )}
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {actions.map((a, i) => (
            <Button key={i} size="sm" variant="outline" disabled={busy} onClick={() => onAction(a)}>
              {clean(a.button_label)}
            </Button>
          ))}
        </div>
      )}
      {citations.length > 0 && (
        <details className="text-[13px]">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {citations.length} source{citations.length === 1 ? '' : 's'}
          </summary>
          <ol className="mt-1 grid gap-1.5">
            {citations.map((c, i) => (
              <li key={`${c.source_id}-${i}`} className="grid gap-0.5 rounded-md border p-2">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{c.title}</span>
                  <ToneBadge>{SOURCE_TEXT[c.source_type] ?? words(c.source_type)}</ToneBadge>
                  <span className="text-xs text-muted-foreground">{Math.round((c.relevance_score || 0) * 100)}% match</span>
                </span>
                <span className="text-muted-foreground">{c.excerpt}</span>
                <span className="font-mono text-[11px] text-muted-foreground">{c.source_id}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
      {m.model && <span className="text-[11px] text-muted-foreground">Answered by {m.model}</span>}
    </div>
  );
}

// Answers come back as light markdown: headings, bullets, **bold** and ```
// code blocks. Only those are rendered, as React nodes, so nothing from the
// server becomes HTML.
function RichText({ text }: { text: string }) {
  const parts = String(text || '').split(/```[a-z]*\n?([\s\S]*?)```/i);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre key={i} className="overflow-x-auto rounded-md bg-muted/50 p-2 font-mono text-xs">
            {part.trim()}
          </pre>
        ) : (
          <Blocks key={i} text={part} />
        ),
      )}
    </>
  );
}

function Blocks({ text }: { text: string }) {
  const out: React.ReactNode[] = [];
  let bullets: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length)
      out.push(
        <p key={out.length} className="whitespace-pre-wrap">
          {inline(para.join('\n'))}
        </p>,
      );
    if (bullets.length)
      out.push(
        <ul key={out.length} className="grid list-disc gap-0.5 pl-5">
          {bullets.map((b, j) => (
            <li key={j}>{inline(b)}</li>
          ))}
        </ul>,
      );
    para = [];
    bullets = [];
  };
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    const heading = /^#{1,6}\s+(.*)$/.exec(line.trim());
    const bullet = /^\s*[*-]\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      out.push(
        <h3 key={out.length} className="font-semibold">
          {inline(heading[1])}
        </h3>,
      );
    } else if (bullet) {
      if (para.length) {
        const keep = bullets;
        bullets = [];
        flush();
        bullets = keep;
      }
      bullets.push(bullet[1]);
    } else if (!line.trim()) {
      flush();
    } else {
      if (bullets.length) flush();
      para.push(line);
    }
  }
  flush();
  return <>{out}</>;
}

const inline = (t: string) =>
  t.split(/(\*\*[^*]+\*\*)/g).map((chunk, j) => (chunk.startsWith('**') && chunk.endsWith('**') ? <strong key={j}>{chunk.slice(2, -2)}</strong> : chunk));
