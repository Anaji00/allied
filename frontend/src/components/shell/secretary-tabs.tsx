'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { cn } from '@/lib/utils';

const TABS: { href: string; label: string; exact?: boolean; also?: string[]; minRole?: 'OPERATOR' | 'MANAGER' }[] = [
  { href: '/secretary', label: 'Start here', exact: true },
  { href: '/inbox', label: 'Today', also: ['/chase', '/mine', '/ball'] },
  { href: '/secretary/mail', label: 'Email', minRole: 'OPERATOR' },
  { href: '/orders', label: 'Orders & Sage', also: ['/rfqs'] },
  { href: '/logistics', label: 'Freight and shipping', also: ['/shipments', '/carriers'] },
  { href: '/contacts', label: 'Contacts', also: ['/vendors'] },
  { href: '/sales', label: 'Sales', minRole: 'OPERATOR' },
  { href: '/help', label: 'Help' },
];

/** The Secretary workspace: the pages of the day's work, one click apart. */
export function SecretaryTabs() {
  const pathname = usePathname();
  const { hasRole } = useAuth();
  return (
    <nav aria-label="Secretary" className="mb-4 flex gap-1 overflow-x-auto border-b">
      {TABS.filter((t) => !t.minRole || hasRole(t.minRole)).map((t) => {
        const on = t.exact ? pathname === t.href : [t.href, ...(t.also ?? [])].some((h) => pathname === h || pathname.startsWith(`${h}/`));
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={on ? 'page' : undefined}
            className={cn(
              '-mb-px shrink-0 border-b-2 px-3 py-2 text-sm whitespace-nowrap',
              on ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
