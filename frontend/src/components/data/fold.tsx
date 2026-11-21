'use client';

import { useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

const KEY = 'allied.folds';

function read(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, boolean>;
  } catch {
    return {};
  }
}

/**
 * One section of a page that opens and closes. It remembers what each person left open, so a page shows what they use.
 * Pages are one column of these, with the next action at the top, instead of tabs and side panels.
 */
export function Fold({
  id,
  title,
  summary,
  defaultOpen = true,
  children,
}: {
  id: string;
  title: ReactNode;
  /** A short note shown when it is closed, such as a count. */
  summary?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(() => (typeof window === 'undefined' ? defaultOpen : (read()[id] ?? defaultOpen)));
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(KEY, JSON.stringify({ ...read(), [id]: next }));
    } catch {
      // Private windows refuse storage: it just won't be remembered.
    }
  };
  return (
    <section className="grid min-w-0 gap-2 rounded-lg border bg-card">
      <button type="button" aria-expanded={open} onClick={toggle} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <ChevronRight className={cn('size-4 shrink-0 transition-transform', open && 'rotate-90')} aria-hidden />
        <span className="text-sm font-semibold">{title}</span>
        {!open && summary ? <span className="text-xs text-muted-foreground">{summary}</span> : null}
      </button>
      {open && <div className="min-w-0 border-t px-3 py-3">{children}</div>}
    </section>
  );
}
