'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, Search } from 'lucide-react';
import { authFetch } from '@/lib/api';

type Task = { title: string; how: string; href: string; words?: string };

const TASKS: Task[] = [
  {
    title: 'Answer a customer or vendor email',
    how: 'Open Email, find the mail, press Draft a reply. Read it, change it, then send it from Waiting for your OK.',
    href: '/secretary/mail',
    words: 'reply mail respond answer',
  },
  {
    title: 'Write a new email',
    how: 'Press New draft. Say who it is for and what it says. It waits for your OK; nothing is sent by itself.',
    href: '/secretary/mail?filter=drafts&new=1',
    words: 'compose write email message',
  },
  {
    title: 'Log a quote request (RFQ)',
    how: 'A customer asked for a price. Open Orders & Sage, then Quote requests, and add it.',
    href: '/orders?tab=rfqs',
    words: 'rfq quote request price customer asked',
  },
  {
    title: 'Start a job',
    how: 'Open the quote request and press Open the job. Or press New, then Job.',
    href: '/boards?board=jobs',
    words: 'job project new start customer po',
  },
  {
    title: 'Make a sales order or a purchase order',
    how: 'Press New, then choose Sales order or Purchase order. The app checks the parts first and writes nothing until you confirm.',
    href: '/orders',
    words: 'order po purchase sales customer vendor',
  },
  {
    title: 'Approve the vendor’s shop drawing (Gate 1)',
    how: 'Open the job. In Production gates, a named engineer presses Approve. Nobody else can.',
    href: '/boards?board=jobs',
    words: 'gate approve drawing shop engineer athish',
  },
  {
    title: 'Approve the measurement photos (Gate 2)',
    how: 'Open the job. In Production gates, the named engineer approves Gate 2 before the 50% payment and pickup.',
    href: '/boards?board=jobs',
    words: 'gate 2 photos pics measurement approve',
  },
  {
    title: 'Record a payment to a vendor',
    how: 'Open the job. In Payments to vendors, press Record a payment once. It updates the job, the ledger and writes a remittance draft.',
    href: '/boards?board=jobs',
    words: 'pay payment deposit 50% 20% vendor wire',
  },
  {
    title: 'Send a drawing to a vendor',
    how: 'Open the job. In Drawings for the vendor, check the drawing and make a clean copy. Only a verified copy can go to a vendor.',
    href: '/boards?board=jobs',
    words: 'drawing vendor redact customer details send dwg',
  },
  {
    title: 'Compare two drawing versions',
    how: 'Open Toolkit, Compare drawings. It shows what changed in red and blue.',
    href: '/toolkit?tab=compare',
    words: 'compare drawing revision change',
  },
  {
    title: 'Add a shipment and follow it',
    how: 'Press New, then Shipment. Open it to set how it travels and to confirm delivery.',
    href: '/logistics',
    words: 'shipment freight forwarder ups truck track bol',
  },
  {
    title: 'Chase a vendor who is slow',
    how: 'Open Who owes us something. A follow-up is already drafted for you to check and send.',
    href: '/chase',
    words: 'chase follow up vendor late owe waiting',
  },
  {
    title: 'See what is late',
    how: 'Open Boards, then Late or urgent. It lists late orders, stuck jobs and quotes with no answer.',
    href: '/boards?view=late',
    words: 'late overdue urgent behind',
  },
  {
    title: 'Send orders to Sage',
    how: 'Open Orders & Sage, then Sage. Download the file, import it into Sage yourself. This marks those orders as sent.',
    href: '/orders?tab=sage',
    words: 'sage export invoice accounting file import',
  },
  {
    title: 'Find out where an order is',
    how: 'Open Toolkit, Where is my order? Type the order number.',
    href: '/toolkit?tab=status',
    words: 'status where order track',
  },
  {
    title: 'Translate a message',
    how: 'Open Toolkit, Translate. English, Vietnamese and Chinese.',
    href: '/toolkit?tab=translate',
    words: 'translate vietnamese chinese mandarin',
  },
  {
    title: 'Tell someone to get something done (managers)',
    how: 'In the Control room, use Get something done. Use Practice only first to see who would get it.',
    href: '/admin',
    words: 'nudge pester remind milestone blast text',
  },
];

/** The server's best guess at a task's screen, turned into the real address. */
function realPath(link: string) {
  const map: [string, string][] = [
    ['/payments', '/boards?board=jobs'],
    ['/gates', '/boards?board=jobs'],
    ['/vendors', '/contacts?section=vendors'],
    ['/control', '/admin'],
    ['/jobs', '/boards?board=jobs'],
    ['/sage', '/orders?tab=sage'],
    ['/landed-cost', '/toolkit?tab=landed'],
    ['/logistics', '/logistics'],
    ['/search', '/help'],
  ];
  const hit = map.find(([from]) => link === from || link.startsWith(`${from}/`) || link.startsWith(`${from}?`));
  return hit ? hit[1] : link;
}

const WORDS: [string, string][] = [
  ['PO', 'A purchase order. Allied’s order to a vendor. Written Allied PO# on screen.'],
  ['Customer PO#', 'The number on the customer’s own order. It ties the vendor orders to one job.'],
  ['RFQ', 'A request for a quote: a customer asking for a price.'],
  [
    'Engineer check (Gate 1 / Gate 2)',
    'Two checks by an engineer. Gate 1: the vendor’s shop drawing. Gate 2: the measurement photos. A job cannot move on without them, unless a manager passes it with a written reason.',
  ],
  ['Deposit, 50%, 20%', 'The payment parts a vendor is paid in, for example payment 1 of 3. Some vendors want 100% up front.'],
  ['Practice mode', 'Nothing is written into Sage. You download files and import them into Sage yourself.'],
  ['Remove customer details', 'A vendor must never see the customer’s name. The app checks a drawing and makes a clean copy first.'],
  ['Blind ship', 'The vendor ships without the customer’s name on the paperwork.'],
  ['Awaiting invoice', 'Work that is delivered but not billed yet.'],
  ['Draft', 'An email the app wrote for you. It is not sent until you approve it.'],
  ['Waiting for your OK', 'Something the app prepared that needs a person to press the button.'],
  ['Practice only', 'Shows what would happen and sends nothing.'],
  ['Undo', 'Most buttons wait a few seconds before they act. Press Undo in the notice to stop it.'],
  ['Changes waiting for monday', 'Edits made here that go to monday only when a manager sends them.'],
];

const PEOPLE: [string, string][] = [
  ['Doug Hamlin', 'Founder and CEO'],
  ['Athish Singh', 'Solutions engineer. Approves Gate 1 and Gate 2.'],
  ['Karissa M. Rakestraw', 'Office manager, and the sales rep on customer records'],
  ['Engineers', 'Allen Steeves, Erin Smith, Paul Wang, Quan Tran, Shane Flaherty, Shawn Bettencourt, Taras Zhurakovskyi'],
];

/**
 * Help in plain words: what you want to do and where to click, the words we use, what is safe to press, and who does what.
 * Nothing here changes anything; every task opens the real screen.
 */
export function HelpHome() {
  const [q, setQ] = useState('');
  const needle = q.trim().toLowerCase();
  // Tasks and words the server knows (and what this person may do). They add to the ones written here.
  const server = useQuery({
    queryKey: ['help', 'tasks', needle],
    queryFn: async () => {
      const res = await authFetch(`/api/v1/search/tasks?q=${encodeURIComponent(needle)}`, { cache: 'no-store' }).catch(() => null);
      return res?.ok
        ? ((
            (await res.json()) as {
              tasks?: { id: string; title: string; why: string; link: string; allowed_for_me?: boolean; not_allowed_because?: string }[];
            }
          ).tasks ?? [])
        : [];
    },
    enabled: needle.length > 1,
    retry: false,
  }).data;
  const glossary = useQuery({
    queryKey: ['help', 'glossary'],
    queryFn: async () => {
      const res = await authFetch('/api/v1/glossary/app', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? (((await res.json()) as { terms?: { term: string; plain: string }[] }).terms ?? []) : [];
    },
    staleTime: 300_000,
    retry: false,
  }).data;
  const tasks = needle ? TASKS.filter((t) => `${t.title} ${t.how} ${t.words ?? ''}`.toLowerCase().includes(needle)) : TASKS;
  const all: [string, string][] = [
    ...WORDS,
    ...(glossary ?? [])
      .filter((t) => !WORDS.some(([w]) => w.toLowerCase() === t.term.toLowerCase()))
      .map((t): [string, string] => [t.term, t.plain]),
  ];
  const words = needle ? all.filter(([w, d]) => `${w} ${d}`.toLowerCase().includes(needle)) : all;

  return (
    <div className="grid max-w-4xl gap-6">
      <div className="grid gap-2">
        <p className="text-sm text-muted-foreground">
          Tell us what you want to do. Each answer opens the right screen. Nothing on this page changes anything.
        </p>
        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="For example: pay a vendor, send a drawing, late"
            aria-label="Search the help"
            className="h-10 w-full rounded-lg border bg-background pr-3 pl-9 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>
      </div>

      <section className="rounded-lg border bg-card p-4" aria-label="Safe to press">
        <h2 className="text-sm font-semibold">You can press things safely</h2>
        <ul className="mt-1 grid list-disc gap-1 pl-5 text-[13px] text-muted-foreground">
          <li>Nothing is sent to a customer, vendor or Sage until a person presses Send.</li>
          <li>Most buttons wait a few seconds. Press Undo in the notice to stop them.</li>
          <li>monday only changes when a manager sends the waiting changes.</li>
          <li>If something is not allowed, the screen says why in plain words.</li>
        </ul>
      </section>

      <section id="what-changes" className="rounded-lg border bg-card p-4" aria-label="What changes real data">
        <h2 className="text-sm font-semibold">The only buttons that change real things</h2>
        <ul className="mt-1 grid gap-1.5 text-[13px] text-muted-foreground">
          <li>
            <b className="text-foreground">Send an email:</b> press once, then press again to confirm. You then have a few seconds to undo.
          </li>
          <li>
            <b className="text-foreground">Download for Sage:</b> makes the file and marks those orders as sent, so they are not in the next
            file. You still import it into Sage yourself.
          </li>
          <li>
            <b className="text-foreground">Send to monday (managers):</b> sends the waiting changes to the real board. It cannot be taken
            back from here.
          </li>
          <li>
            <b className="text-foreground">Import a list, Check the mailbox now:</b> add or change real records, or make orders from new
            mail. Each asks first.
          </li>
          <li>
            <b className="text-foreground">Record a payment, Approve a check:</b> are saved with your name and cannot be quietly changed.
          </li>
        </ul>
      </section>

      <section id="who-can" className="rounded-lg border bg-card p-4" aria-label="Who can do what">
        <h2 className="text-sm font-semibold">Who can do what</h2>
        <dl className="mt-1 grid gap-x-4 gap-y-1 text-[13px] sm:grid-cols-[max-content_minmax(0,1fr)]">
          <dt className="font-medium">Viewer</dt>
          <dd className="text-muted-foreground">Looks only.</dd>
          <dt className="font-medium">Operator</dt>
          <dd className="text-muted-foreground">
            Writes drafts, logs requests, starts jobs, edits boards. Cannot approve a PO or the design.
          </dd>
          <dt className="font-medium">Manager</dt>
          <dd className="text-muted-foreground">
            Approves, sends changes to monday, records payments, imports lists, passes a check with a reason.
          </dd>
          <dt className="font-medium">Admin</dt>
          <dd className="text-muted-foreground">Everything, plus accounts and settings.</dd>
        </dl>
        <p className="mt-1 text-xs text-muted-foreground">If a button is missing or not allowed, the screen says why. Ask a manager.</p>
      </section>

      <section id="tasks" className="grid gap-2" aria-label="What do you want to do">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">What do you want to do?</h2>
        {(server ?? []).length > 0 && (
          <ul className="grid gap-2 sm:grid-cols-2">
            {server!
              .filter((t) => !TASKS.some((x) => x.title.toLowerCase() === t.title.toLowerCase()))
              .map((t) => (
                <li key={t.id}>
                  {t.allowed_for_me === false ? (
                    <div className="grid h-full gap-1 rounded-lg border border-dashed bg-card p-3 opacity-80">
                      <span className="text-sm font-medium">{t.title}</span>
                      <span className="text-xs text-muted-foreground">{t.not_allowed_because ?? 'You cannot do this. Ask a manager.'}</span>
                    </div>
                  ) : (
                    <Link href={realPath(t.link)} className="grid h-full gap-1 rounded-lg border bg-card p-3 hover:bg-muted/40">
                      <span className="flex items-center gap-2 text-sm font-medium">
                        {t.title}
                        <ArrowRight className="ml-auto size-4 text-muted-foreground" />
                      </span>
                      <span className="text-xs text-muted-foreground">{t.why}</span>
                    </Link>
                  )}
                </li>
              ))}
          </ul>
        )}
        {tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing matches. Try one word, such as vendor, drawing or payment.</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {tasks.map((t) => (
              <li key={t.title}>
                <Link href={t.href} className="grid h-full gap-1 rounded-lg border bg-card p-3 hover:bg-muted/40">
                  <span className="flex items-center gap-2 text-sm font-medium">
                    {t.title}
                    <ArrowRight className="ml-auto size-4 text-muted-foreground" />
                  </span>
                  <span className="text-xs text-muted-foreground">{t.how}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section id="words" className="grid gap-2" aria-label="Words we use">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Words we use</h2>
        <dl className="grid gap-x-4 gap-y-2 rounded-lg border bg-card p-4 text-[13px] sm:grid-cols-[max-content_minmax(0,1fr)]">
          {words.map(([w, d]) => (
            <div key={w} className="contents">
              <dt className="font-medium">{w}</dt>
              <dd className="text-muted-foreground">{d}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section id="people" className="grid gap-2" aria-label="Who does what">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Who does what</h2>
        <dl className="grid gap-x-4 gap-y-2 rounded-lg border bg-card p-4 text-[13px] sm:grid-cols-[max-content_minmax(0,1fr)]">
          {PEOPLE.map(([n, r]) => (
            <div key={n} className="contents">
              <dt className="font-medium">{n}</dt>
              <dd className="text-muted-foreground">{r}</dd>
            </div>
          ))}
        </dl>
        <p className="text-xs text-muted-foreground">Titles come from what the team has told us. Ask a manager if one is wrong.</p>
      </section>
      <section id="wrong" className="rounded-lg border bg-card p-4" aria-label="If something looks wrong">
        <h2 className="text-sm font-semibold">If something looks wrong</h2>
        <ul className="mt-1 grid list-disc gap-1 pl-5 text-[13px] text-muted-foreground">
          <li>Problems show on Today, in the list of what is waiting on you.</li>
          <li>Tell a manager what you pressed and what you saw. A photo of the screen helps.</li>
          <li>Press Ctrl+K to search for anything. On a phone, use the search box at the top.</li>
          <li>The language can be changed from your account menu: English, Vietnamese or Chinese.</li>
        </ul>
      </section>
    </div>
  );
}
