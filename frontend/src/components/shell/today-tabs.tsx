'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { todayTab, useLanguage } from '@/lib/language';
import { cn } from '@/lib/utils';

const TABS = [
  { href: '/inbox', label: 'Inbox' },
  { href: '/mine', label: 'My day' },
  { href: '/ball', label: 'Who is it waiting on?' },
];

/** The three "what is waiting" pages share one menu item, "Today"; these tabs move between them. */
export function TodayTabs() {
  const pathname = usePathname();
  const { language } = useLanguage();
  return (
    <nav aria-label="Today" className="mb-4 flex gap-1 border-b">
      {TABS.map((t) => {
        const on = pathname === t.href;
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={on ? 'page' : undefined}
            className={cn(
              '-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap',
              on ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {todayTab(t.href, t.label, language)}
          </Link>
        );
      })}
    </nav>
  );
}
