'use client';

import { useRouter } from 'next/navigation';
import { useTheme } from 'next-themes';
import { LogOut, Monitor, Moon, Sun } from 'lucide-react';
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import { useAuth } from '@/context/AuthContext';
import { navFor } from '@/lib/nav';

/**
 * Every typed word must appear; a match in the name outranks one in the
 * description. cmdk's default fuzzy match let "light" pick Shipments, because
 * its letters appear in order inside "Freight on the way…".
 */
function rank(value: string, search: string, keywords: string[] = []) {
  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return 1;
  const name = value.toLowerCase();
  const all = `${name} ${keywords.join(' ').toLowerCase()}`;
  if (!words.every((w) => all.includes(w))) return 0;
  if (name.startsWith(words[0])) return 1;
  if (words.every((w) => name.includes(w))) return 0.8;
  return 0.3;
}

/**
 * Ctrl+K: every page this role can open, plus theme and sign-out.
 * Order, heat-number and shipment search arrive with the Orders and Inbox
 * screens (phase 2), when there is a page to open them on.
 */
export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const { setTheme } = useTheme();
  const { hasRole, logoutEmployee } = useAuth();
  const groups = navFor(hasRole);

  const run = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Search and commands"
      description="Go to a page or run a command"
      className="sm:max-w-xl"
    >
      {/* This CommandDialog leaves the cmdk root to the caller. */}
      <Command filter={rank}>
        <CommandInput placeholder="Go to a page or run a command…" />
        <CommandList>
          <CommandEmpty>Nothing matches. Try a page name like “shipments”.</CommandEmpty>
          {groups.map((group) => (
            <CommandGroup key={group.section} heading={group.section}>
              {group.items.map((item) => (
                <CommandItem
                  key={item.href}
                  value={item.label}
                  keywords={[item.description, group.section]}
                  onSelect={() => run(() => router.push(item.href))}
                >
                  <item.icon />
                  <span className="shrink-0 whitespace-nowrap">{item.label}</span>
                  <span className="ml-auto hidden min-w-0 truncate pl-4 text-xs text-muted-foreground sm:block">
                    {item.description}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
          <CommandSeparator />
          <CommandGroup heading="Theme">
            <CommandItem value="Light theme" keywords={['appearance']} onSelect={() => run(() => setTheme('light'))}>
              <Sun /> Light theme
            </CommandItem>
            <CommandItem value="Dark theme" keywords={['appearance', 'night']} onSelect={() => run(() => setTheme('dark'))}>
              <Moon /> Dark theme
            </CommandItem>
            <CommandItem value="Match the computer" keywords={['theme', 'system', 'appearance']} onSelect={() => run(() => setTheme('system'))}>
              <Monitor /> Match the computer
            </CommandItem>
          </CommandGroup>
          <CommandSeparator />
          <CommandGroup heading="Account">
            <CommandItem value="Sign out" keywords={['log out', 'account']} onSelect={() => run(logoutEmployee)}>
              <LogOut /> Sign out
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
