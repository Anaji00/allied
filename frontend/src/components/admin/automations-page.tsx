'use client';

import { AutomationsTool } from '@/components/tools/more-tools';
import { HistoryTool } from '@/components/tools/ai-tools';
import { ScorecardTool } from '@/components/tools/records-tools';
import { ExtractionStats, MailTemplates } from './control-extras';

/** Everything about the rules and the AI in one place: switch a rule, see how it has done, and see what the AI suggested. */
export function AutomationsPage() {
  return (
    <div className="grid max-w-4xl gap-6">
      <AutomationsTool />
      <ScorecardTool />
      <MailTemplates />
      <ExtractionStats />
      <section className="grid gap-2">
        <h2 className="text-sm font-semibold">What the AI suggested</h2>
        <HistoryTool />
      </section>
    </div>
  );
}
