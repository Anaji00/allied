'use client';

import { useState } from 'react';
import { parseAsString, parseAsStringLiteral, useQueryState } from 'nuqs';
import { SectionTabs } from '@/components/data/filters';
import { AiStatusLine, CallTool, DrawingTool, EmailTool, GlossaryTool, HistoryTool, PartTool, TranslateTool, useAiStatus } from './ai-tools';
import { Ask } from './ask';
import { DocumentReader } from './document-reader';
import { Tasks } from './tasks';

const TABS = [
  ['ask', 'Ask'],
  ['documents', 'Read a document'],
  ['translate', 'Translate'],
  ['email', 'Email'],
  ['drawings', 'Drawing check'],
  ['calls', 'Phone calls'],
  ['parts', 'Part names'],
  ['glossary', 'Glossary'],
  ['history', 'History'],
  ['tasks', 'Tasks'],
] as const;
type Tab = (typeof TABS)[number][0];

export function AssistantView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS.map(([t]) => t)).withDefault('ask'));
  // Other pages can open the assistant with a question ready to send.
  const [prompt] = useQueryState('prompt', parseAsString.withDefault(''));
  const { data: status } = useAiStatus();
  // A tab stays mounted once opened, so a slow AI call (or its answer)
  // survives switching away and back.
  const [visited, setVisited] = useState<Set<Tab>>(() => new Set([tab]));
  if (!visited.has(tab)) setVisited(new Set(visited).add(tab));

  const panes: Record<Tab, React.ReactNode> = {
    ask: <Ask initialPrompt={prompt} />,
    documents: <DocumentReader />,
    translate: <TranslateTool />,
    email: <EmailTool />,
    drawings: <DrawingTool />,
    calls: <CallTool recordingsReady={status?.recordings?.ready} />,
    parts: <PartTool />,
    glossary: <GlossaryTool />,
    history: <HistoryTool />,
    tasks: <Tasks />,
  };

  return (
    <div className="grid gap-4">
      <SectionTabs label="Assistant tools" value={tab} onChange={(t) => void setTab(t === 'ask' ? null : t)} tabs={TABS} />
      {tab !== 'ask' && tab !== 'documents' && tab !== 'tasks' && <AiStatusLine />}
      {TABS.map(([key]) =>
        visited.has(key) ? (
          <div key={key} hidden={tab !== key}>
            {panes[key]}
          </div>
        ) : null,
      )}
    </div>
  );
}
