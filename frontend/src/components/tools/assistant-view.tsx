'use client';

import { Activity, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import {
  AiStatusLine,
  CallTool,
  DrawingTool,
  EmailTool,
  GlossaryTool,
  HistoryTool,
  PartTool,
  TranslateTool,
  useAiStatus,
} from './ai-tools';
import { DrawingCompare } from './drawing-compare';
import { DocumentReader } from './document-reader';
import { AutomationsTool, ExportBoardTool, OrderStatusTool, SuspectedOrdersTool } from './more-tools';
import { BillHistoryTool, MyNotificationsTool, ScorecardTool } from './records-tools';
import { CompanyNamesTool } from '@/components/organize/company-names';
import { Tasks } from './tasks';

type Tool = { key: string; title: string; line: string; ai: boolean };
const GROUPS: { name: string; tools: Tool[] }[] = [
  {
    name: 'Read and write',
    tools: [
      { key: 'documents', title: 'Read a document', line: 'Pull the details out of a PO, drawing or packing slip.', ai: true },
      { key: 'translate', title: 'Translate', line: 'English, Vietnamese and Chinese, with our own words kept.', ai: true },
      { key: 'email', title: 'Email', line: 'Read a customer email, or draft a reply to check and send.', ai: true },
      { key: 'calls', title: 'Phone calls', line: 'Turn a call recording or a pasted call into a short note.', ai: true },
      { key: 'glossary', title: 'Our words', line: 'The part and process words the translator must keep.', ai: false },
    ],
  },
  {
    name: 'Check',
    tools: [
      { key: 'drawings', title: 'Drawing check', line: 'Look a drawing over for missing or odd details.', ai: true },
      { key: 'compare', title: 'Compare drawings', line: 'See what changed between two revisions as a red and blue picture.', ai: false },
      { key: 'parts', title: 'Part names', line: 'Match what a customer wrote to our part list.', ai: true },
      { key: 'suspected', title: 'Test and duplicate orders', line: 'Orders that look like tests or copies. Only reports.', ai: false },
    ],
  },
  {
    name: 'Look up and take out',
    tools: [
      { key: 'status', title: 'Where is my order?', line: 'The answer you would give a caller, from the order itself.', ai: false },
      { key: 'export', title: 'Export a board', line: 'A monday board as Excel, CSV or PDF.', ai: false },
    ],
  },
  {
    name: 'Record',
    tools: [
      { key: 'history', title: 'AI suggestions', line: 'Every suggestion, and whether a person approved it.', ai: false },
      {
        key: 'automations',
        title: 'Automations',
        line: 'Ready-made rules that draft and remind for you, and what each would do today.',
        ai: false,
      },
      { key: 'bills', title: 'Bills checked', line: 'Vendor bills compared with their purchase orders, and how each came out.', ai: false },
      { key: 'scorecard', title: 'Automation scorecard', line: 'How often automatic drafts were sent or cancelled.', ai: false },
      {
        key: 'notifications',
        title: 'My notifications',
        line: 'Choose how each kind of news reaches you, quiet hours, and a daily summary.',
        ai: false,
      },
      {
        key: 'companies',
        title: 'Company names',
        line: 'One company, plant and family for every spelling seen on the boards. Kept in the app only.',
        ai: false,
      },
      { key: 'tasks', title: 'Run a task', line: 'Read an email into an order, or read items from a monday board.', ai: false },
    ],
  },
];
const KEYS = GROUPS.flatMap((g) => g.tools.map((t) => t.key));
const TITLE = Object.fromEntries(GROUPS.flatMap((g) => g.tools.map((t) => [t.key, t.title])));

/** The Toolkit: every helper in one place, each with a plain line about what it does and whether it uses AI. */
export function AssistantView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(KEYS));
  const { data: status } = useAiStatus();
  // A tool mounts when first opened and then lives in an <Activity>: switching away keeps its state (a slow AI call, an answer,
  // a half-typed form) while React pauses its effects.
  const [visited, setVisited] = useState<Set<string>>(() => new Set(tab ? [tab] : []));
  if (tab && !visited.has(tab)) setVisited(new Set(visited).add(tab));

  const panes: Record<string, React.ReactNode> = {
    documents: <DocumentReader />,
    translate: <TranslateTool />,
    email: <EmailTool />,
    drawings: <DrawingTool />,
    compare: <DrawingCompare />,
    calls: <CallTool recordingsReady={status?.recordings?.ready} />,
    parts: <PartTool />,
    glossary: <GlossaryTool />,
    history: <HistoryTool />,
    tasks: <Tasks />,
    suspected: <SuspectedOrdersTool />,
    status: <OrderStatusTool />,
    export: <ExportBoardTool />,
    automations: <AutomationsTool />,
    companies: <CompanyNamesTool />,
    bills: <BillHistoryTool />,
    scorecard: <ScorecardTool />,
    notifications: <MyNotificationsTool />,
  };
  const uses = GROUPS.flatMap((g) => g.tools).find((t) => t.key === tab)?.ai;

  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4">
      {!tab ? (
        <>
          <AiStatusLine />
          {GROUPS.map((g) => (
            <section key={g.name} className="grid gap-2" aria-label={g.name}>
              <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{g.name}</h2>
              <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {g.tools.map((t) => (
                  <li key={t.key}>
                    <button
                      type="button"
                      onClick={() => void setTab(t.key as (typeof KEYS)[number])}
                      className="grid h-full w-full gap-1 rounded-lg border bg-card p-3 text-left hover:bg-muted/40"
                    >
                      <span className="flex items-center gap-2 text-sm font-medium">
                        {t.title}
                        <span className="ml-auto rounded-full bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground">
                          {t.ai ? 'Uses AI' : 'No AI'}
                        </span>
                      </span>
                      <span className="text-xs text-muted-foreground">{t.line}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void setTab(null)}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-4" /> All tools
            </button>
            <span className="text-sm font-medium">{TITLE[tab]}</span>
          </div>
          {uses && <AiStatusLine />}
        </>
      )}
      {KEYS.map((key) =>
        visited.has(key) ? (
          <Activity key={key} mode={tab === key ? 'visible' : 'hidden'}>
            {panes[key]}
          </Activity>
        ) : null,
      )}
    </div>
  );
}
