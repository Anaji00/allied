'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Menu } from 'lucide-react';
import { useSidebar } from '@/components/ui/sidebar';
import { useInbox } from '@/components/inbox/use-inbox';
import { isActive, PHONE_TABS } from '@/lib/nav';
import { cn } from '@/lib/utils';

const tab =
  'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium text-muted-foreground outline-none focus-visible:bg-accent [&_svg]:size-5';

/** Phones only: the daily pages one thumb away, and "More" for the full menu. */
export function PhoneTabBar() {
  const pathname = usePathname();
  const { openMobile, setOpenMobile } = useSidebar();
  // Same queries as the sidebar badge, so the count costs no extra requests.
  const waiting = useInbox().items;
  const critical = waiting.some((i) => i.severity === 'critical');
  const onTab = PHONE_TABS.some((item) => isActive(item.href, pathname));

  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
    >
      <div className="flex h-16">
        {PHONE_TABS.map((item) => {
          const active = isActive(item.href, pathname);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={cn(tab, active && 'text-foreground')}
            >
              <span className={cn('grid h-7 w-12 place-items-center rounded-full', active && 'bg-accent')}>
                <item.icon />
              </span>
              <span className="truncate">{item.label}</span>
              {item.href === '/inbox' && waiting.length > 0 && (
                <span
                  className={cn(
                    'absolute top-1.5 left-1/2 ml-2 min-w-5 rounded-full px-1 text-center text-[10px] leading-5 font-semibold tabular-nums',
                    critical ? 'bg-bad text-white' : 'bg-foreground text-background',
                  )}
                  aria-label={`${waiting.length} waiting`}
                >
                  {waiting.length > 99 ? '99+' : waiting.length}
                </span>
              )}
            </Link>
          );
        })}
        <button
          type="button"
          onClick={() => setOpenMobile(true)}
          aria-expanded={openMobile}
          className={cn(tab, !onTab && 'text-foreground')}
        >
          <span className={cn('grid h-7 w-12 place-items-center rounded-full', !onTab && 'bg-accent')}>
            <Menu />
          </span>
          <span>More</span>
        </button>
      </div>
    </nav>
  );
}
