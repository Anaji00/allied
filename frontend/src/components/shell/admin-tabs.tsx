'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { cn } from '@/lib/utils';

const TABS: { href: string; label: string; minRole?: 'OPERATOR' | 'MANAGER' }[] = [
  { href: '/admin', label: 'Overview' },
  { href: '/health', label: 'System' },
  { href: '/admin/automations', label: 'Automations' },
  { href: '/quality', label: 'Material test papers' },
  { href: '/alerts', label: 'People & alerts' },
  { href: '/orders?tab=sage', label: 'Sage & imports', minRole: 'MANAGER' },
  { href: '/admin/audit', label: 'Audit trail', minRole: 'MANAGER' },
];

/** Everything that is not daily work sits under one "Admin" menu item; these tabs move between its pages. */
export function AdminTabs() {
  const pathname = usePathname();
  const { hasRole } = useAuth();
  return (
    <nav aria-label="Control room" className="mb-4 flex gap-1 overflow-x-auto border-b">
      {TABS.filter((t) => !t.minRole || hasRole(t.minRole)).map((t) => {
        const on = t.href === '/admin' ? pathname === '/admin' : pathname === t.href || pathname.startsWith(`${t.href}/`);
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
