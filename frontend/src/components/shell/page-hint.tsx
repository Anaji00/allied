'use client';

import { useSyncExternalStore } from 'react';
import { usePathname } from 'next/navigation';
import { X } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { pageFor } from '@/lib/nav';

const KEY = 'allied.hints.dismissed';
const listeners = new Set<() => void>();

function read(): string {
  try {
    return localStorage.getItem(KEY) ?? '';
  } catch {
    return '';
  }
}
function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => void listeners.delete(cb);
}
function dismiss(id: string) {
  try {
    const all = new Set(read().split('\n').filter(Boolean));
    all.add(id);
    localStorage.setItem(KEY, [...all].join('\n'));
  } catch {
    // Private windows refuse storage: it simply comes back next visit.
  }
  listeners.forEach((l) => l());
}

/**
 * One quiet line under the page title that says what the screen is for. Closed with the ×, it stays closed
 * for that person on that screen. On the server it renders nothing, so there is no flash of it.
 */
export function PageHint() {
  const pathname = usePathname();
  const { employeeEmail } = useAuth();
  const page = pageFor(pathname);
  const id = `${employeeEmail || 'anyone'}|${pathname.split('/').slice(0, 3).join('/')}`;
  const closed = useSyncExternalStore(
    subscribe,
    () => read().split('\n').includes(id),
    () => true,
  );
  if (closed || !page.description) return null;
  return (
    <p className="mb-4 flex items-start gap-2 text-sm text-muted-foreground">
      <span className="min-w-0 flex-1">{page.description}</span>
      <button
        type="button"
        onClick={() => dismiss(id)}
        aria-label="Hide this note"
        title="Hide this note"
        className="grid size-6 shrink-0 place-items-center rounded-md hover:bg-muted hover:text-foreground"
      >
        <X className="size-3.5" />
      </button>
    </p>
  );
}
