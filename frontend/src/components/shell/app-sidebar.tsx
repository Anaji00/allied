'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ChevronRight } from 'lucide-react';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from '@/components/ui/sidebar';
import { useQuery } from '@tanstack/react-query';
import { authFetch } from '@/lib/api';
import { navLabel, sectionLabel, useLanguage } from '@/lib/language';
import { useAuth } from '@/context/AuthContext';
import { isActive, navFor } from '@/lib/nav';
import { useAutomationStatus } from '@/lib/queries';
import { cn } from '@/lib/utils';
import { Count } from '@/components/data/motion';
import { useInbox } from '@/components/inbox/use-inbox';
import { BrandMark } from './brand-mark';

export function AppSidebar() {
  const pathname = usePathname();
  const { hasRole, facility } = useAuth();
  const { language } = useLanguage();
  const { setOpenMobile } = useSidebar();
  const groups = navFor(hasRole);
  // Admin pages are used rarely, so that group starts folded; it opens by itself on one of its pages.
  const [folded, setFolded] = useState<Set<string>>(() => new Set(['Admin']));
  const toggle = (name: string) =>
    setFolded((f) => {
      const next = new Set(f);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  // Same queries as the Inbox page, so the count costs no extra requests.
  const waiting = useInbox().items;
  const critical = waiting.some((i) => i.severity === 'critical');
  const attention = useQuery({
    queryKey: ['orders', 'attention'],
    queryFn: async (): Promise<{ total?: number; slips_failed?: number } | null> => {
      const res = await authFetch('/api/v1/orders/attention', { cache: 'no-store' });
      if (!res.ok) return null;
      return (await res.json()) as { total?: number; slips_failed?: number };
    },
    refetchInterval: 60_000,
    retry: false,
  }).data;
  const orderAttention = attention?.total ?? 0;

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link href="/" onClick={() => setOpenMobile(false)} />}>
              <BrandMark className="size-7" />
              <span className="grid leading-tight group-data-[collapsible=icon]:hidden">
                <span className="font-semibold text-foreground">Allied</span>
                <span className="text-xs text-muted-foreground">Operations{facility ? ` · ${facility}` : ''}</span>
              </span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {groups.map((group) => {
          const hasCurrent = group.items.some((i) => isActive(i.href, pathname));
          const shut = folded.has(group.section) && !hasCurrent;
          return (
            <SidebarGroup key={group.section}>
              {group.section === 'Admin' ? (
                <SidebarGroupLabel
                  render={
                    <button
                      type="button"
                      aria-expanded={!shut}
                      onClick={() => toggle(group.section)}
                      className="w-full cursor-pointer justify-between hover:text-sidebar-foreground"
                    />
                  }
                >
                  {sectionLabel(group.section, language)}
                  <ChevronRight className={cn('size-3.5 transition-transform', !shut && 'rotate-90')} aria-hidden />
                </SidebarGroupLabel>
              ) : (
                <SidebarGroupLabel>{sectionLabel(group.section, language)}</SidebarGroupLabel>
              )}
              <SidebarMenu className={cn(shut && 'hidden')}>
                {group.items.map((item) => {
                  const active = isActive(item.href, pathname);
                  const label = navLabel(item.href, item.label, language);
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={active}
                        tooltip={label}
                        render={<Link href={item.href} aria-current={active ? 'page' : undefined} onClick={() => setOpenMobile(false)} />}
                      >
                        <item.icon />
                        <span>{label}</span>
                      </SidebarMenuButton>
                      {item.href === '/inbox' && waiting.length > 0 && (
                        <SidebarMenuBadge className={cn('tabular-nums', critical && 'font-semibold text-bad')}>
                          <Count value={waiting.length} />
                        </SidebarMenuBadge>
                      )}
                      {item.href === '/orders' && orderAttention > 0 && (
                        <SidebarMenuBadge className={cn('tabular-nums', (attention?.slips_failed ?? 0) > 0 && 'font-semibold text-bad')}>
                          <Count value={orderAttention} />
                        </SidebarMenuBadge>
                      )}
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroup>
          );
        })}
      </SidebarContent>

      <SidebarFooter>
        <ConnectionStatus onNavigate={() => setOpenMobile(false)} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

/** Reported, not asserted: nothing is claimed until the server has answered. */
function ConnectionStatus({ onNavigate }: { onNavigate: () => void }) {
  const { data, isError } = useAutomationStatus();
  const link = (key: string) => data?.links?.find((l) => l.key === key);
  const rows = [
    { label: 'monday.com', link: link('monday'), on: 'Live', off: 'Not connected' },
    { label: 'Sage 50', link: link('sage_write'), on: 'Writing', off: 'Import files' },
  ];

  return (
    <Link
      href="/health?tab=monday"
      onClick={onNavigate}
      className="grid gap-1 rounded-md p-2 text-xs text-sidebar-foreground hover:bg-sidebar-accent group-data-[collapsible=icon]:justify-items-center group-data-[collapsible=icon]:px-0"
    >
      {rows.map((row) => {
        const known = Boolean(data) && !isError;
        const live = Boolean(row.link?.live);
        return (
          <span
            key={row.label}
            className="flex items-center gap-2"
            title={`${row.label}: ${known ? (live ? row.on : row.off) : 'checking'}`}
          >
            <span className={cn('size-2 shrink-0 rounded-full', !known ? 'bg-muted-foreground/40' : live ? 'bg-ok' : 'bg-warn')} />
            <span className="group-data-[collapsible=icon]:hidden">{row.label}</span>
            <span className="ml-auto text-muted-foreground group-data-[collapsible=icon]:hidden">
              {!known ? '…' : live ? row.on : row.off}
            </span>
          </span>
        );
      })}
    </Link>
  );
}
