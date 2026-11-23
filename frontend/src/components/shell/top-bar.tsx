'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTheme } from 'next-themes';
import { toast } from 'sonner';
import { KeyRound, Loader2, LogOut, Monitor, Moon, Search, Sun } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
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
import { navLabel, useLanguage } from '@/lib/language';
import { pageFor } from '@/lib/nav';
import { useHealth } from '@/lib/queries';
import { LanguageMenuGroup, LanguagePrompt } from './language-menu';
import { cn } from '@/lib/utils';
import { NewMenu } from '@/components/quick-create';
import { BrandMark } from './brand-mark';

export function TopBar({ onOpenPalette, shortcut }: { onOpenPalette: () => void; shortcut: string }) {
  const pathname = usePathname();
  const page = pageFor(pathname);
  const { language } = useLanguage();

  return (
    <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-2 border-b bg-background/90 px-3 backdrop-blur md:px-4">
      {/* Phones open the menu from the tab bar's "More"; the mark stands in for the trigger. */}
      <BrandMark className="size-6 md:hidden" />
      <SidebarTrigger className="-ml-1 hidden md:inline-flex" />
      <Separator orientation="vertical" className="mr-1 hidden data-vertical:h-4 md:block" />
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="shrink-0 text-sm font-semibold">{navLabel(page.href, page.label, language)}</h1>
        {page.description && <p className="hidden truncate text-xs text-muted-foreground 2xl:block">{page.description}</p>}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <NewMenu />
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenPalette}
          className="justify-start gap-2 text-muted-foreground sm:w-60"
          aria-label="Search and commands"
        >
          <Search />
          <span className="hidden flex-1 text-left sm:inline">Search everything</span>
          <Kbd className="hidden sm:inline-flex">{shortcut}</Kbd>
        </Button>
        <Link
          href="/help"
          aria-label="Help"
          title="Help: what you want to do, in plain words"
          className="inline-flex size-8 items-center justify-center rounded-full border text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          ?
        </Link>
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
        ? { dot: 'bg-warn', text: 'Sage practice mode', tip: 'Nothing is written into Sage. You download files and import them yourself.' }
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
  const parts = name
    .replace(/@.*/, '')
    .split(/[\s._-]+/)
    .filter(Boolean);
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?';
}

function UserMenu() {
  const { employeeName, employeeEmail, userRole, facility, logoutEmployee } = useAuth();
  const { theme, setTheme } = useTheme();
  const [pwOpen, setPwOpen] = useState(false);
  const role = String(userRole || '').toLowerCase();

  return (
    <>
      <LanguagePrompt />
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
          <LanguageMenuGroup />
          <DropdownMenuSeparator />
          <DropdownMenuItem render={<Link href="/toolkit?tab=notifications" />}>My notifications</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setPwOpen(true)}>
            <KeyRound /> Change password
          </DropdownMenuItem>
          <DropdownMenuItem onClick={logoutEmployee}>
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangePasswordDialog open={pwOpen} onOpenChange={setPwOpen} />
    </>
  );
}

function ChangePasswordDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const { replaceTokens } = useAuth();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await authFetch('/api/v1/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      const body = (await res.json().catch(() => null)) as { detail?: string; access_token?: string; refresh_token?: string } | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `Couldn’t change password (${res.status}).`);
      // Every older session ends, so this device takes the new tokens and stays signed in; other devices sign in again.
      if (body?.access_token && body?.refresh_token) replaceTokens(body.access_token, body.refresh_token);
      toast.success('Password updated', { description: 'Other devices will need to sign in again.' });
      setCurrentPassword('');
      setNewPassword('');
      onOpenChange(false);
    } catch (err) {
      toast.error('Password not changed', { description: err instanceof Error ? err.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Change password</DialogTitle>
          <DialogDescription>Use at least 8 characters.</DialogDescription>
        </DialogHeader>
        <form id="change-pw" className="grid gap-3" onSubmit={(e) => void submit(e)}>
          <Field label="Current password">
            <Input type="password" maxLength={128} value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} required />
          </Field>
          <Field label="New password">
            <Input
              type="password"
              minLength={8}
              maxLength={128}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </Field>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="change-pw" disabled={busy || newPassword.length < 8}>
            {busy && <Loader2 className="animate-spin" />}
            Save password
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
