'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTheme } from 'next-themes';
import { LogOut, Monitor, Moon, Search, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Kbd } from '@/components/ui/kbd';
import { Separator } from '@/components/ui/separator';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useAuth } from '@/context/AuthContext';
import { pageFor } from '@/lib/nav';
import { useHealth } from '@/lib/queries';
import { cn } from '@/lib/utils';
import { BrandMark } from './brand-mark';

export function TopBar({ onOpenPalette, shortcut }: { onOpenPalette: () => void; shortcut: string }) {
  const pathname = usePathname();
  const page = pageFor(pathname);

  return (
    <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-2 border-b bg-background/90 px-3 backdrop-blur md:px-4">
      {/* Phones open the menu from the tab bar's "More"; the mark stands in for the trigger. */}
      <BrandMark className="size-6 md:hidden" />
      <SidebarTrigger className="-ml-1 hidden md:inline-flex" />
      <Separator orientation="vertical" className="mr-1 hidden data-vertical:h-4 md:block" />
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="truncate text-sm font-semibold">{page.label}</h1>
        {page.description && (
          <p className="hidden truncate text-xs text-muted-foreground xl:block">{page.description}</p>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenPalette}
          className="justify-start gap-2 text-muted-foreground sm:w-60"
          aria-label="Search and commands"
        >
          <Search />
          <span className="hidden flex-1 text-left sm:inline">Search and commands</span>
          <Kbd className="hidden sm:inline-flex">{shortcut}</Kbd>
        </Button>
        <StatusPill />
        <UserMenu />
      </div>
    </header>
  );
}

/** One dot for everyone; the detail lives on the System health page. */
function StatusPill() {
  const { data, isError, isPending } = useHealth();
  const state = isPending
    ? { dot: 'bg-muted-foreground/40', text: 'Checking…', tip: 'Asking the server how it is.' }
    : isError
      ? { dot: 'bg-bad', text: 'Server unreachable', tip: 'The API did not answer. Pages may show old data.' }
      : data?.simulation_mode
        ? { dot: 'bg-warn', text: 'Sage practice mode', tip: 'Sage writes are simulated until a live connection is switched on.' }
        : { dot: 'bg-ok', text: 'All systems normal', tip: 'API, database and Sage connection are up.' };

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/health"
            className="hidden items-center gap-2 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground md:inline-flex"
          />
        }
      >
        <span className={cn('size-2 rounded-full', state.dot)} />
        {state.text}
      </TooltipTrigger>
      <TooltipContent>{state.tip}</TooltipContent>
    </Tooltip>
  );
}

function initials(name: string) {
  const parts = name.replace(/@.*/, '').split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

function UserMenu() {
  const { employeeName, employeeEmail, userRole, facility, logoutEmployee } = useAuth();
  const { theme, setTheme } = useTheme();
  const role = String(userRole || '').toLowerCase();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="grid size-8 place-items-center rounded-full border bg-muted text-xs font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Account and theme"
      >
        {initials(employeeName || employeeEmail)}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="grid gap-0.5">
            <span className="truncate font-medium text-foreground">{employeeName || employeeEmail}</span>
            {employeeName && employeeEmail && employeeName !== employeeEmail && (
              <span className="truncate font-normal">{employeeEmail}</span>
            )}
            <span className="font-normal capitalize">
              {role}
              {facility ? ` · ${facility}` : ''}
            </span>
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel>Theme</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={theme ?? 'system'} onValueChange={(value) => setTheme(String(value))}>
            <DropdownMenuRadioItem value="system">
              <Monitor /> Match the computer
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="light">
              <Sun /> Light
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="dark">
              <Moon /> Dark
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={logoutEmployee}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
