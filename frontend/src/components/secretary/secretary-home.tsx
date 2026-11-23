'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Sparkles } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Counts = {
  mail_needing_action?: number;
  email_drafts_waiting?: number;
  drafts_waiting?: number;
  alerts_unread?: number;
  need_action?: number;
};
type Attention = { emails_unread?: number; bills_to_check?: number };

type AiReply = { message?: string; lines?: string[]; drafts?: unknown[]; examples?: string[] };
type Summary = {
  mail_needing_reply?: { count: number };
  drafts_waiting?: number;
  order_emails?: { count: number };
  bills_to_check?: { count: number } | null;
  late_orders?: { count: number };
};
const ask = (prompt: string) => window.dispatchEvent(new CustomEvent('allied:ask', { detail: { prompt } }));
const STARTERS = [
  'Give me my summary for today',
  'Follow up with everyone who owes us something',
  'Write a follow-up to the vendors who owe us a price',
];
const openNew = (kind: string) => window.dispatchEvent(new CustomEvent('allied:new', { detail: { kind } }));

type Card = { title: string; line: string; href?: string; run?: () => void; count?: number; minRole?: 'OPERATOR' | 'MANAGER' };

function Tile({ c }: { c: Card }) {
  const body = (
    <>
      <span className="flex items-center gap-2 text-sm font-medium">
        {c.title}
        {c.count !== undefined && c.count > 0 && (
          <span className="ml-auto rounded-full bg-warn-soft px-2 py-0.5 text-xs font-medium text-warn tabular-nums">{c.count}</span>
        )}
      </span>
      <span className="text-xs text-muted-foreground">{c.line}</span>
    </>
  );
  const cls = 'grid h-full w-full gap-1 rounded-lg border bg-card p-3 text-left hover:bg-muted/40';
  return c.href ? (
    <Link href={c.href} className={cls}>
      {body}
    </Link>
  ) : (
    <button type="button" onClick={c.run} className={cls}>
      {body}
    </button>
  );
}

/**
 * Where the secretary starts: what needs doing now, the everyday jobs one click away, and a plain list of what the system does by
 * itself. Every tile opens the screen that does the job.
 */
export function SecretaryHome() {
  const { hasRole, employeeName } = useAuth();
  const [prompt, setPrompt] = useState('');
  const [reply, setReply] = useState<AiReply | null>(null);
  const [asking, setAsking] = useState(false);
  // The rule-based secretary answers here: it makes drafts for a person to check and sends nothing. An older server falls back to the Ask panel.
  async function runAsk(text: string) {
    setAsking(true);
    try {
      const res = await authFetch('/api/v1/ai/secretary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: text }),
      });
      if (res.status === 404) return ask(text);
      const out = (await res.json().catch(() => null)) as (AiReply & { detail?: string }) | null;
      setReply(res.ok && out ? out : { message: typeof out?.detail === 'string' ? out.detail : 'That did not work. Try again.' });
    } finally {
      setAsking(false);
    }
  }
  const summary = useQuery({
    queryKey: ['secretary', 'summary'],
    queryFn: async (): Promise<Summary | null> => {
      const res = await authFetch('/api/v1/secretary/summary', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Summary) : null;
    },
    refetchInterval: 60_000,
    retry: false,
    enabled: hasRole('OPERATOR'),
  }).data;
  const counts = useQuery({
    queryKey: ['me', 'counts'],
    queryFn: async (): Promise<Counts | null> => {
      const res = await authFetch('/api/v1/me/counts', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Counts) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  }).data;
  const attention = useQuery({
    queryKey: ['orders', 'attention'],
    queryFn: async (): Promise<Attention | null> => {
      const res = await authFetch('/api/v1/orders/attention', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Attention) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  }).data;

  const now: Card[] = [
    {
      title: 'Everything waiting on you',
      line: 'The one list: approvals, problems and follow-ups.',
      href: '/inbox',
      count: counts?.need_action,
    },
    {
      title: 'Mail that needs a reply',
      line: 'From vendors and customers. A reply is drafted for you to check.',
      href: '/secretary/mail',
      count: summary?.mail_needing_reply?.count ?? counts?.mail_needing_action,
      minRole: 'OPERATOR',
    },
    {
      title: 'Emails waiting for your OK',
      line: 'Read, change and send them, or cancel.',
      href: '/secretary/mail?filter=drafts',
      count: summary?.drafts_waiting ?? counts?.email_drafts_waiting ?? counts?.drafts_waiting,
      minRole: 'OPERATOR',
    },
    {
      title: 'Order emails and supplier bills',
      line: 'Orders that came by email, and bills to check.',
      href: '/orders?tab=emails',
      count: summary
        ? (summary.order_emails?.count ?? 0) + (summary.bills_to_check?.count ?? 0)
        : (attention?.emails_unread ?? 0) + (attention?.bills_to_check ?? 0),
    },
    { title: 'Who owes us something', line: 'Vendors, customers and carriers, oldest first.', href: '/chase' },
  ];
  const aiTasks: Card[] = [
    {
      title: 'Draft a reply with AI',
      line: 'Open the mail that needs a reply and press Draft a reply.',
      href: '/secretary/mail',
      minRole: 'OPERATOR',
    },
    { title: 'Translate', line: 'English, Vietnamese and Chinese.', href: '/toolkit?tab=translate' },
    { title: 'Read a document or call', line: 'Pull out the details, or turn a call into a note.', href: '/toolkit?tab=documents' },
  ];
  const everyday: Card[] = [
    {
      title: 'Write an email',
      line: 'To a vendor, a customer or your team. Saved as a draft.',
      href: '/secretary/mail?filter=drafts&new=1',
      minRole: 'OPERATOR',
    },
    { title: 'Log a quote request', line: 'A customer asked for a price.', run: () => openNew('rfq'), minRole: 'OPERATOR' },
    {
      title: 'Make a sales order',
      line: 'A customer ordered. It also makes the vendor purchase orders.',
      run: () => openNew('order'),
      minRole: 'OPERATOR',
    },
    { title: 'Make a purchase order', line: 'Order from a vendor.', run: () => openNew('po'), minRole: 'OPERATOR' },
    { title: 'Start a job', line: 'Follow one piece of work from request to shipment.', run: () => openNew('job'), minRole: 'OPERATOR' },
    { title: 'Add a shipment', line: 'Freight on the way, and when it arrives.', run: () => openNew('shipment'), minRole: 'OPERATOR' },
    {
      title: 'Price a quote',
      line: 'The vendor price plus duty and freight, at different markups. Duty rates are added in the tool first.',
      href: '/toolkit?tab=landed',
      minRole: 'MANAGER',
    },
    { title: 'Where is a customer’s order?', line: 'Look up one order for a status check-in.', href: '/toolkit?tab=status' },
    { title: 'Find a drawing change', line: 'Compare two drawing versions side by side.', href: '/toolkit?tab=compare' },
    { title: 'Send orders to Sage', line: 'Download the files, then import them into Sage.', href: '/orders?tab=sage', minRole: 'MANAGER' },
    {
      title: 'See what is late',
      line: 'Late orders, stuck jobs and quotes with no answer.',
      href: '/boards?view=late',
      count: summary?.late_orders?.count,
    },
  ];
  const auto = [
    'Reads the shared mailbox and drafts replies. Nothing is sent until you approve it.',
    'Keeps monday and the app in step. Changes you make wait for a manager to send them to monday.',
    'Chases vendors and customers who are slow, with a draft follow-up ready.',
    'Warns you about late shipments, quotes with no answer and bills that do not match.',
    'Makes the Sage files for sales orders and purchase orders, so nothing is typed twice.',
  ];

  const visible = (cards: Card[]) => cards.filter((c) => !c.minRole || hasRole(c.minRole));
  return (
    <div className="grid min-w-0 gap-6">
      <section className="grid gap-3 rounded-xl border bg-gradient-to-br from-card to-muted/40 p-5" aria-label="AI secretary">
        <div className="flex items-center gap-2">
          <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
            <Sparkles className="size-4" />
          </span>
          <div className="grid">
            <h2 className="text-base font-semibold">Your AI secretary</h2>
            <p className="text-xs text-muted-foreground">
              Tell it what you need in your own words. It writes drafts and finds answers. You check everything before it goes anywhere.
            </p>
          </div>
        </div>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (prompt.trim()) {
              void runAsk(prompt.trim());
              setPrompt('');
            }
          }}
        >
          <input
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="For example: give me my summary for today, or reply to Helen"
            aria-label="Tell the AI secretary what you need"
            className="h-10 min-w-0 flex-1 rounded-lg border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            disabled={!prompt.trim() || asking}
          >
            Ask <ArrowRight className="size-4" />
          </button>
        </form>
        {reply && (
          <div role="status" className="grid gap-1.5 rounded-lg border bg-background p-3 text-[13px]">
            <p className="font-medium">{reply.message}</p>
            {(reply.lines ?? []).map((l, i) => (
              <p key={i} className="text-muted-foreground">
                {l}
              </p>
            ))}
            {(reply.drafts ?? []).length > 0 && (
              <Link href="/secretary/mail?filter=drafts" className="justify-self-start underline underline-offset-2">
                See {reply.drafts!.length === 1 ? 'the draft' : `the ${reply.drafts!.length} drafts`} waiting for your OK. Nothing is sent.
              </Link>
            )}
            {(reply.examples ?? []).length > 0 && (
              <ul className="grid list-disc gap-0.5 pl-5 text-xs text-muted-foreground">
                {reply.examples!.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {STARTERS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => void runAsk(t)}
              className="rounded-full border bg-background px-3 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {t}
            </button>
          ))}
        </div>
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {visible(aiTasks).map((c) => (
            <li key={c.title}>
              <Tile c={c} />
            </li>
          ))}
        </ul>
      </section>
      <p className="text-sm text-muted-foreground">
        {employeeName ? `Hello, ${employeeName.split(' ')[0]}. ` : ''}This is where the day’s work starts. Pick what you need; each box
        opens the screen that does it.
      </p>
      <section className="grid gap-2" aria-label="Needs doing now">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Needs doing now</h2>
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {visible(now).map((c) => (
            <li key={c.title}>
              <Tile c={c} />
            </li>
          ))}
        </ul>
      </section>
      <section className="grid gap-2" aria-label="Everyday jobs">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Everyday jobs</h2>
        <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {visible(everyday).map((c) => (
            <li key={c.title}>
              <Tile c={c} />
            </li>
          ))}
        </ul>
      </section>
      <section className={cn('grid gap-2 rounded-lg border bg-card p-4')} aria-label="What the system does by itself">
        <h2 className="text-sm font-semibold">What the system does for you</h2>
        <ul className="grid list-disc gap-1 pl-5 text-[13px] text-muted-foreground">
          {auto.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          A person approves anything that goes outside Allied, or that commits money, a date or a specification.
        </p>
      </section>
    </div>
  );
}
