'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { useTheme } from 'next-themes';
import { Factory, FilePlus, LogOut, Monitor, Moon, ReceiptText, Sparkles, Sun } from 'lucide-react';
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
import { api, ok } from '@/lib/api/client';
import { fetchRfqs, fetchVendorJobs } from '@/lib/opsApi';
import { navFor } from '@/lib/nav';

type Hit = { id: string | number; title: string; sub: string };

/** Typed text, once it has paused for a moment. */
function useLater(value: string, ms = 250) {
  const [later, setLater] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setLater(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return later;
}

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
 * Ctrl+K: type to find an order, vendor job or quote request and open it, do a quick
 * action (new order, ask the assistant), or go to any page this role can open.
 */
export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const { setTheme } = useTheme();
  const { hasRole, logoutEmployee } = useAuth();
  const groups = navFor(hasRole);
  const [typed, setTyped] = useState('');
  const text = useLater(typed.trim());
  const searching = open && text.length >= 2;

  // Found as you type. Orders are searched on the server; vendor jobs and quote requests are short
  // lists, so they are read once and matched here.
  const orders = useQuery({
    queryKey: ['palette', 'orders', text],
    queryFn: async () =>
      (
        (await ok(api.GET('/api/v1/orders', { params: { query: { q: text, view: 'all', limit: 5, offset: 0 } } }))) as unknown as {
          items: { sales_order_number: string; customer_name?: string | null }[];
        }
      ).items.map((o): Hit => ({ id: o.sales_order_number, title: o.sales_order_number, sub: o.customer_name ?? '' })),
    enabled: searching,
    staleTime: 30_000,
  });
  const jobs = useQuery({
    queryKey: ['palette', 'jobs'],
    queryFn: async () =>
      ((await fetchVendorJobs()) as { jobs: { id: number; vendor: string; description?: string | null; po_number?: string | null }[] })
        .jobs,
    enabled: searching,
    staleTime: 60_000,
  });
  const rfqs = useQuery({
    queryKey: ['palette', 'rfqs'],
    queryFn: async () =>
      ((await fetchRfqs(true, false)) as { rfqs: { id: number; number: string; customer?: string | null; subject?: string | null }[] })
        .rfqs,
    enabled: searching,
    staleTime: 60_000,
  });
  const has = (...parts: (string | null | undefined)[]) => {
    const hay = parts.join(' ').toLowerCase();
    return text
      .toLowerCase()
      .split(/\s+/)
      .every((w) => hay.includes(w));
  };
  const jobHits = (jobs.data ?? []).filter((j) => has(j.vendor, j.description, j.po_number)).slice(0, 5);
  const rfqHits = (rfqs.data ?? []).filter((r) => has(r.number, r.customer, r.subject)).slice(0, 5);

  const run = (fn: () => void) => {
    onOpenChange(false);
    setTyped('');
    fn();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setTyped('');
        onOpenChange(next);
      }}
      title="Search and commands"
      description="Go to a page or run a command"
      className="sm:max-w-xl"
    >
      {/* This CommandDialog leaves the cmdk root to the caller. */}
      <Command filter={rank}>
        <CommandInput value={typed} onValueChange={setTyped} placeholder="Find an order, vendor or request, or do something…" />
        <CommandList>
          <CommandEmpty>Nothing matches. Try an order number, a vendor, or a page name like “shipments”.</CommandEmpty>
          {searching && (orders.data?.length || jobHits.length || rfqHits.length) ? (
            <>
              {orders.data && orders.data.length > 0 && (
                <CommandGroup heading="Orders">
                  {orders.data.map((o) => (
                    <CommandItem
                      key={o.id}
                      value={`order ${o.id}`}
                      keywords={[text]}
                      onSelect={() => run(() => router.push(`/orders?order=${encodeURIComponent(String(o.id))}`))}
                    >
                      <ReceiptText />
                      <span className="font-mono text-xs">{o.title}</span>
                      <span className="ml-auto truncate pl-4 text-xs text-muted-foreground">{o.sub}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
              {jobHits.length > 0 && (
                <CommandGroup heading="Vendor orders">
                  {jobHits.map((j) => (
                    <CommandItem
                      key={j.id}
                      value={`vendor job ${j.id}`}
                      keywords={[text]}
                      onSelect={() => run(() => router.push(`/vendors?job=${j.id}`))}
                    >
                      <Factory />
                      <span>{j.vendor}</span>
                      <span className="ml-auto truncate pl-4 text-xs text-muted-foreground">{j.description || j.po_number}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
              {rfqHits.length > 0 && (
                <CommandGroup heading="Quote requests">
                  {rfqHits.map((r) => (
                    <CommandItem
                      key={r.id}
                      value={`rfq ${r.id}`}
                      keywords={[text]}
                      onSelect={() => run(() => router.push(`/rfqs?id=${r.id}`))}
                    >
                      <FilePlus />
                      <span className="font-mono text-xs">{r.number}</span>
                      <span className="ml-auto truncate pl-4 text-xs text-muted-foreground">{r.customer || r.subject}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
              <CommandSeparator />
            </>
          ) : null}
          <CommandGroup heading="Do something">
            {hasRole('OPERATOR') && (
              <CommandItem
                value="New order"
                keywords={['create', 'add', 'order']}
                onSelect={() => run(() => router.push('/orders?new=true'))}
              >
                <FilePlus /> New order
              </CommandItem>
            )}
            <CommandItem
              forceMount
              value="Ask the assistant"
              keywords={['question', 'help', 'ask', typed]}
              onSelect={() => run(() => router.push(typed.trim() ? `/chat?prompt=${encodeURIComponent(typed.trim())}` : '/chat'))}
            >
              <Sparkles /> {typed.trim().length > 2 ? `Ask the assistant: “${typed.trim().slice(0, 40)}”` : 'Ask the assistant'}
            </CommandItem>
          </CommandGroup>
          <CommandSeparator />
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
                  <span className="ml-auto hidden min-w-0 truncate pl-4 text-xs text-muted-foreground sm:block">{item.description}</span>
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
            <CommandItem
              value="Match the computer"
              keywords={['theme', 'system', 'appearance']}
              onSelect={() => run(() => setTheme('system'))}
            >
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
