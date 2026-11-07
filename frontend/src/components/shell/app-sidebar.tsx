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
                  {group.section}
                  <ChevronRight className={cn('size-3.5 transition-transform', !shut && 'rotate-90')} aria-hidden />
                </SidebarGroupLabel>
              ) : (
                <SidebarGroupLabel>{group.section}</SidebarGroupLabel>
              )}
              <SidebarMenu className={cn(shut && 'hidden')}>
                {group.items.map((item) => {
                  const active = isActive(item.href, pathname);
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={active}
                        tooltip={item.label}
                        render={<Link href={item.href} aria-current={active ? 'page' : undefined} onClick={() => setOpenMobile(false)} />}
                      >
                        <item.icon />
                        <span>{item.label}</span>
                      </SidebarMenuButton>
                      {item.href === '/inbox' && waiting.length > 0 && (
                        <SidebarMenuBadge className={cn('tabular-nums', critical && 'font-semibold text-bad')}>
                          <Count value={waiting.length} />
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
