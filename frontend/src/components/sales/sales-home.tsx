'use client';

import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { SectionTabs } from '@/components/data/filters';
import { LeadsHome } from '@/components/leads/leads-home';
import { WearView } from './wear-view';

const SECTIONS = ['leads', 'wear'] as const;

/** Prospects and outreach, and equipment wear with replacement quotes: kept apart from the order work. */
export function SalesHome() {
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(SECTIONS).withDefault('leads'));
  return (
    <div className="grid min-w-0 gap-4">
      <SectionTabs
        label="Sales sections"
        value={tab}
        onChange={(v) => void setTab(v === 'leads' ? null : v)}
        tabs={[
          ['leads', 'Leads'],
          ['wear', 'Wear & quotes'],
        ]}
      />
      {tab === 'leads' ? <LeadsHome /> : <WearView />}
    </div>
  );
}
