'use client';

import Link from 'next/link';
import { ControlStatus } from './control-status';
import { DatabaseCheck } from './control-extras';
import { Nudge } from './nudge';
import { useAuth } from '@/context/AuthContext';

type Entry = { title: string; line: string; href: string; minRole?: 'OPERATOR' | 'MANAGER' };
const GROUPS: { name: string; entries: Entry[] }[] = [
  {
    name: 'Freight and shipping',
    entries: [
      { title: 'Shipments', line: 'Freight on the way, and who it is waiting on.', href: '/logistics' },
      { title: 'Packing slips', line: 'The queue of slips to print or retry.', href: '/logistics?section=slips' },
      {
        title: 'Carriers and freight prices',
        line: 'Your own prices, when each was last checked, and live quotes.',
        href: '/logistics?section=carriers',
        minRole: 'OPERATOR',
      },
    ],
  },
  {
    name: 'Money and quality',
    entries: [
      {
        title: 'Sage files',
        line: 'Send orders to Sage, bring lists in, and see what went out.',
        href: '/orders?tab=sage',
        minRole: 'MANAGER',
      },
      { title: 'Mill certificates', line: 'Material test papers for each batch.', href: '/quality' },
      {
        title: 'Bills checked',
        line: 'Vendor bills compared with their purchase orders.',
        href: '/toolkit?tab=bills',
        minRole: 'OPERATOR',
      },
      {
        title: 'Test and duplicate orders',
        line: 'Orders that look like tests or copies. Only reports.',
        href: '/toolkit?tab=suspected',
        minRole: 'MANAGER',
      },
    ],
  },
  {
    name: 'Setup and records',
    entries: [
      {
        title: 'Lead engine',
        line: 'Sales leads, follow-ups due today, and outreach drafts waiting for your OK.',
        href: '/sales?tab=leads',
        minRole: 'OPERATOR',
      },
      { title: 'Required forms', line: 'Which forms each kind of job needs.', href: '/health?tab=forms', minRole: 'MANAGER' },
      { title: 'Job templates', line: 'The steps a new job starts with.', href: '/health?tab=templates', minRole: 'MANAGER' },
      { title: 'People and alerts', line: 'Accounts, who gets told, and the daily summary.', href: '/alerts' },
      { title: 'Audit trail', line: 'Who did what, and when.', href: '/admin/audit', minRole: 'MANAGER' },
    ],
  },
  {
    name: 'Automation (rules, no AI)',
    entries: [
      {
        title: 'Ready-made automations',
        line: 'Switch a rule off, ask-first or automatic, and see what it would do today.',
        href: '/admin/automations',
      },
      {
        title: 'Automation scorecard',
        line: 'How often automatic drafts were sent or cancelled.',
        href: '/admin/automations',
        minRole: 'MANAGER',
      },
      { title: 'What runs by itself', line: 'Every automatic job and its mode.', href: '/health?tab=automation' },
      {
        title: 'monday changes waiting',
        line: 'What is staged for monday, for a manager to send.',
        href: '/health?tab=changes',
        minRole: 'OPERATOR',
      },
    ],
  },
  {
    name: 'System',
    entries: [
      { title: 'Health and backups', line: 'Is everything running, and when was the last backup.', href: '/health' },
      { title: 'monday.com link', line: 'Connection and read status.', href: '/health?tab=monday' },
    ],
  },
];

/** The back office in one place: each card opens the screen that does the job. Nothing here is copied; it all links. */
export function AdminHome() {
  const { hasRole } = useAuth();
  return (
    <div className="grid min-w-0 gap-5">
      <Nudge />
      <ControlStatus />
      <DatabaseCheck />
      {GROUPS.map((g) => {
        const entries = g.entries.filter((e) => !e.minRole || hasRole(e.minRole));
        if (!entries.length) return null;
        return (
          <section key={g.name} className="grid gap-2" aria-label={g.name}>
            <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{g.name}</h2>
            <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {entries.map((e) => (
                <li key={e.title}>
                  <Link href={e.href} className="grid h-full gap-1 rounded-lg border bg-card p-3 hover:bg-muted/40">
                    <span className="text-sm font-medium">{e.title}</span>
                    <span className="text-xs text-muted-foreground">{e.line}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
