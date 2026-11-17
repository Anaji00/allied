'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Item = { kind: 'email' | 'bill' | 'order' | 'slip'; ref: string; title: string; status_word: string; age_days: number; link: string };
type Inbox = { total: number; counts: Record<string, number>; items: Item[] };

const FILTERS: [string, string][] = [
  ['needs_me', 'All'],
  ['emails', 'Emails'],
  ['bills', 'Bills'],
  ['slips', 'Packing slips'],
  ['orders', 'Orders'],
];
const COUNT_KEY: Record<string, string> = { emails: 'email', bills: 'bill', slips: 'slip', orders: 'order' };
const age = (d: number) => (d < 1 ? 'today' : d === 1 ? '1 day' : `${d} days`);

/** One short list of everything in Orders that is waiting on a person, oldest first. Stays out of the way when there is nothing. */
export function NeedsYou() {
  const router = useRouter();
  const [open, setOpen] = useState(true);
  const [filter, setFilter] = useState('needs_me');
  const q = useQuery({
    queryKey: ['orders', 'inbox', filter],
    queryFn: async (): Promise<Inbox | null> => {
      const res = await authFetch(`/api/v1/orders/inbox?filter=${filter}&limit=100`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Inbox) : null;
    },
    refetchInterval: 60_000,
    retry: false,
  });
  const data = q.data;
  if (!data || (data.total === 0 && filter === 'needs_me')) return null;
  return (
    <section aria-label="Waiting on you" className="rounded-lg border bg-card text-[13px]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight className={cn('size-4 transition-transform', open && 'rotate-90')} aria-hidden />
        <span className="font-medium">Waiting on you</span>
        <span className="text-muted-foreground tabular-nums">{data.total}</span>
      </button>
      {open && (
        <div className="grid gap-2 border-t px-3 py-2.5">
          {data.items.length === 0 ? (
            <p className="text-muted-foreground">Nothing here.</p>
          ) : (
            <ul className="grid divide-y">
              {data.items.slice(0, 12).map((i) => (
                <li key={`${i.kind}-${i.ref}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
                  <span className="min-w-0 flex-1 truncate font-medium">{i.title}</span>
                  <span className="text-muted-foreground">{i.status_word}</span>
                  <span className="text-xs text-muted-foreground">{age(i.age_days)}</span>
                  <Button size="xs" variant="outline" onClick={() => router.push(i.link)}>
                    Open
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {data.items.length > 12 && (
            <p className="text-xs text-muted-foreground">and {data.items.length - 12} more, oldest first above.</p>
          )}
        </div>
      )}
    </section>
  );
}
