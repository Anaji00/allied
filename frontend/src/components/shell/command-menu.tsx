'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { useTheme } from 'next-themes';
import { toast } from 'sonner';
import {
  Factory,
  FilePlus,
  LayoutGrid,
  LogOut,
  MessageSquare,
  Monitor,
  Paperclip,
  Moon,
  ReceiptText,
  Route,
  Ship,
  Sparkles,
  Sun,
  User,
  type LucideIcon,
} from 'lucide-react';
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
import { plain } from '@/lib/format';
import { authFetch } from '@/lib/api';
import { navFor } from '@/lib/nav';
import { downloadFile } from '@/components/data/item-files';

type SearchHit = { type: string; title: string; sub?: string; ref: string; link?: string };
type SearchGroup = { type: string; label: string; hits: SearchHit[] };
type SearchResult = { query: string; total: number; groups: SearchGroup[] };

const ICON: Record<string, LucideIcon> = {
  job: Route,
  order: ReceiptText,
  rfq: FilePlus,
  vendor_job: Factory,
  shipment: Ship,
  comment: MessageSquare,
  board_item: LayoutGrid,
  person: User,
  file: Paperclip,
};

/** Where a search hit opens. The server gives a route hint, but comments and people have none, and the board route is "/". */
export function routeFor(hit: SearchHit): string {
  const enc = encodeURIComponent;
  if (hit.type === 'person') return `mailto:${hit.ref}`;
  if (hit.type === 'comment') {
    const [kind, ...rest] = hit.ref.split(':');
    return routeFor({ type: kind, title: hit.title, ref: rest.join(':'), link: '' });
  }
  // The server's link points at the record itself; the board route is "/" here. Without a link, it is built from the type and reference.
  if (hit.link) return hit.link.replace(/^\/boards(?=[?/]|$)/, '/');
  switch (hit.type) {
    case 'order':
      return `/orders?order=${enc(hit.ref)}`;
    case 'rfq':
      return `/rfqs?id=${enc(hit.ref)}`;
    case 'vendor_job':
      return `/vendors?job=${enc(hit.ref)}`;
    case 'shipment':
      return `/shipments?id=${enc(hit.ref)}`;
    case 'milestone':
      return `/projects?milestone=${enc(hit.ref)}`;
    default:
      return '/';
  }
}

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
 * Ctrl+K and the header search: type to find anything (orders, quote requests, vendor orders, shipments,
 * comments, board rows, people) in one search, do a quick action, or go to any page this role can open.
 */
export function CommandMenu({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const { setTheme } = useTheme();
  const { hasRole, logoutEmployee } = useAuth();
  const groups = navFor(hasRole);
  const [typed, setTyped] = useState('');
  // Which item is lit. Until someone moves it, it is the first search result, even when the results arrive after typing.
  const [picked, setPicked] = useState<string | null>(null);
  const text = useLater(typed.trim());
  const searching = open && text.length >= 2;

  // One search on the server covers every kind of record.
  const found = useQuery({
    queryKey: ['search', text],
    queryFn: async (): Promise<SearchResult | null> => {
      const res = await authFetch(`/api/v1/search?q=${encodeURIComponent(text)}&limit=8`, { cache: 'no-store' });
      return res.ok ? ((await res.json()) as SearchResult) : null;
    },
    enabled: searching,
    staleTime: 30_000,
    retry: false,
  });
  // The server's search lists Jobs first; keep it that way.
  const resultGroups = [...(found.data?.groups ?? [])]
    .sort((x, y) => Number(y.type === 'job') - Number(x.type === 'job'))
    .filter((g) => g.hits.length > 0);
  const firstHit = searching ? resultGroups[0]?.hits[0] : undefined;
  const lit = picked ?? (firstHit ? `${resultGroups[0].type} ${firstHit.ref} ${firstHit.title}` : undefined);

  const run = (fn: () => void) => {
    onOpenChange(false);
    setTyped('');
    fn();
  };
  const open_ = (hit: SearchHit) =>
    run(() => {
      // A file is downloaded with the sign-in, not opened as a page.
      if (hit.type === 'file') {
        const id = /\/files\/(\d+)/.exec(hit.link ?? '')?.[1] ?? hit.ref;
        void downloadFile(id, hit.title).catch((e: Error) => toast.error('Couldn’t download it', { description: e.message }));
        return;
      }
      const to = routeFor(hit);
      if (to.startsWith('mailto:')) window.location.href = to;
      else router.push(to);
    });

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
      <Command filter={rank} value={lit} onValueChange={setPicked}>
        <CommandInput
          value={typed}
          onValueChange={(v) => {
            setTyped(v);
            setPicked(null);
          }}
          placeholder="Search orders, requests, vendors, comments, people… or do something"
        />
        <CommandList>
          <CommandEmpty>
            {found.isFetching
              ? 'Searching…'
              : 'No match yet. Try an order number, a vendor or a page name like “shipments”, or ask the assistant.'}
          </CommandEmpty>
          {searching && resultGroups.length > 0 ? (
            <>
              {resultGroups.map((g) => {
                const Icon = ICON[g.type] ?? LayoutGrid;
                return (
                  <CommandGroup key={g.type} heading={g.label}>
                    {g.hits.map((h) => (
                      <CommandItem
                        key={`${g.type}-${h.ref}-${h.title}`}
                        value={`${g.type} ${h.ref} ${h.title}`}
                        keywords={[text]}
                        onSelect={() => open_(h)}
                      >
                        <Icon />
                        <span className="truncate">{h.title}</span>
                        {h.sub && <span className="ml-auto truncate pl-4 text-xs text-muted-foreground">{plain(h.sub)}</span>}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                );
              })}
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
              onSelect={() => run(() => window.dispatchEvent(new CustomEvent('allied:ask', { detail: { prompt: typed.trim() } })))}
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
