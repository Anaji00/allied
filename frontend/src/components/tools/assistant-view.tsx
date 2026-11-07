'use client';

import { Activity, useState } from 'react';
import { parseAsString, parseAsStringLiteral, useQueryState } from 'nuqs';
import { NativeSelect, SectionTabs } from '@/components/data/filters';
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
import { AssistantChat } from '@/components/assistant/assistant-chat';
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
  ['search', 'Search documents'],
] as const;
type Tab = (typeof TABS)[number][0];
// The four most used stay as tabs; the rest sit behind one menu.
const MAIN = TABS.slice(0, 4);
const MORE = TABS.slice(4);

export function AssistantView() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS.map(([t]) => t)).withDefault('ask'));
  // Other pages can open the assistant with a question ready to send.
  const [prompt] = useQueryState('prompt', parseAsString.withDefault(''));
  const { data: status } = useAiStatus();
  // A tab mounts when first opened and then lives in an <Activity>: switching
  // away keeps its state (a slow AI call, an answer, a half-typed form) while
  // React pauses its effects, so a hidden tab stops polling.
  const [visited, setVisited] = useState<Set<Tab>>(() => new Set([tab]));
  if (!visited.has(tab)) setVisited(new Set(visited).add(tab));

  const panes: Record<Tab, React.ReactNode> = {
    ask: <AssistantChat initialPrompt={prompt} />,
    search: <Ask />,
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

  const open = (t: Tab) => void setTab(t === 'ask' ? null : t);
  const inMore = MORE.some(([t]) => t === tab);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <SectionTabs label="Assistant tools" value={tab} onChange={open} tabs={MAIN} />
        <NativeSelect
          aria-label="More tools"
          value={inMore ? tab : ''}
          onChange={(e) => e.target.value && open(e.target.value as Tab)}
          className={`mb-1 h-8 w-auto text-xs ${inMore ? 'border-foreground font-medium' : 'text-muted-foreground'}`}
        >
          <option value="">More tools…</option>
          {MORE.map(([t, label]) => (
            <option key={t} value={t}>
              {label}
            </option>
          ))}
        </NativeSelect>
      </div>
      {tab !== 'ask' && tab !== 'documents' && tab !== 'tasks' && <AiStatusLine />}
      {TABS.map(([key]) =>
        visited.has(key) ? (
          <Activity key={key} mode={tab === key ? 'visible' : 'hidden'}>
            {panes[key]}
          </Activity>
        ) : null,
      )}
    </div>
  );
}
