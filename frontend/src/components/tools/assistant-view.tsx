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
import { LandedCostTool, TariffRates } from './landed-cost';
import { Tasks } from './tasks';

type Tool = { key: string; title: string; line: string; ai: boolean };
const GROUPS: { name: string; tools: Tool[] }[] = [
  {
    name: 'Read, write and translate',
    tools: [
      { key: 'documents', title: 'Read a document', line: 'Pull the details out of a PO, drawing or packing slip.', ai: true },
      { key: 'translate', title: 'Translate', line: 'English, Vietnamese and Chinese, with our own words kept.', ai: true },
      { key: 'calls', title: 'Phone calls', line: 'Turn a call recording or a pasted call into a short note.', ai: true },
      { key: 'glossary', title: 'Our words', line: 'The part and process words the translator must keep.', ai: false },
    ],
  },
];
// The other tools are still here, but they live where the work is done. They stay reachable by address and from these links.
const ELSEWHERE: [string, string, string][] = [
  ['Compare drawings and check a drawing', 'on every job and order, in the Actions menu', '/toolkit?tab=compare'],
  ['Part names', 'inside New order, in the check step', '/toolkit?tab=parts'],
  ['Where is my order?', 'the order lookup', '/toolkit?tab=status'],
  ['Export a board', 'one board as Excel, CSV or PDF', '/toolkit?tab=export'],
  ['Bills checked, and test or duplicate orders', 'in the Control room', '/toolkit?tab=bills'],
  ['Company names', 'under Contacts', '/contacts?section=companies'],
  ['Landed cost and markup (managers)', 'what a vendor quote costs landed', '/toolkit?tab=landed'],
  ['My notifications', 'in your account menu', '/toolkit?tab=notifications'],
  ['Automations and what the AI suggested', 'in the Control room', '/admin/automations'],
  ['Email', 'under Secretary', '/secretary/mail'],
];
const ALL_KEYS = [
  'documents',
  'translate',
  'email',
  'drawings',
  'compare',
  'calls',
  'parts',
  'glossary',
  'history',
  'tasks',
  'suspected',
  'status',
  'export',
  'automations',
  'bills',
  'scorecard',
  'notifications',
  'companies',
  'landed',
] as const;
const KEYS = ALL_KEYS as readonly string[] as [string, ...string[]];
const TITLE: Record<string, string> = {
  ...Object.fromEntries(GROUPS.flatMap((g) => g.tools.map((t) => [t.key, t.title]))),
  email: 'Email',
  drawings: 'Drawing check',
  compare: 'Compare drawings',
  parts: 'Part names',
  status: 'Where is a customer’s order?',
  export: 'Export a board',
  bills: 'Bills checked',
  suspected: 'Test and duplicate orders',
  landed: 'Landed cost and markup',
  notifications: 'My notifications',
  companies: 'Company names',
  automations: 'Automations',
  scorecard: 'Automation scorecard',
  history: 'What the AI suggested',
  tasks: 'Run a task',
};

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
    landed: (
      <div className="grid gap-4">
        <LandedCostTool />
        <TariffRates />
      </div>
    ),
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
          <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Other tools">
            <h2 className="text-sm font-semibold">Other tools live where you use them</h2>
            <ul className="grid gap-1 text-[13px]">
              {ELSEWHERE.map(([name, where, href]) => (
                <li key={name}>
                  <a href={href} className="underline underline-offset-2">
                    {name}
                  </a>
                  <span className="text-muted-foreground">: {where}</span>
                </li>
              ))}
            </ul>
          </section>
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
