'use client';

import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { TabsWithMore } from '@/components/data/tabs-more';
import { SavingsCalculator, UpgradeProposal } from '@/components/sales/leads-view';
import { Accounts } from './accounts';
import { Find } from './find';
import { Pipeline } from './pipeline';
import { Settings } from './settings';

const SECTIONS = ['pipeline', 'find', 'accounts', 'settings', 'savings', 'upgrade'] as const;

/**
 * The lead engine in one place: who to talk to next and why, a way to add people, the companies behind them, and the rules it follows.
 * Nothing here sends an email; every email is a draft that waits in Approvals.
 */
export function LeadsHome() {
  const [section, setSection] = useQueryState('lead', parseAsStringLiteral(SECTIONS).withDefault('pipeline'));
  return (
    <div className="grid min-w-0 gap-4">
      <TabsWithMore
        label="Lead sections"
        show={4}
        value={section}
        onChange={(v) => void setSection(v === 'pipeline' ? null : v)}
        tabs={[
          ['pipeline', 'Pipeline'],
          ['find', 'Find'],
          ['accounts', 'Accounts'],
          ['settings', 'Settings'],
          ['savings', 'Savings calculator'],
          ['upgrade', 'Upgrade proposal'],
        ]}
      />
      {section === 'pipeline' && <Pipeline />}
      {section === 'find' && <Find />}
      {section === 'accounts' && <Accounts />}
      {section === 'settings' && <Settings />}
      {section === 'savings' && <SavingsCalculator />}
      {section === 'upgrade' && <UpgradeProposal />}
    </div>
  );
}
