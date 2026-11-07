'use client';

import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

/**
 * A count of things that ticks from its old value to the new one, and flashes
 * once when it goes up, so a change made by the 30-second refresh isn't missed.
 * Only for counts: money, margins and dates never animate.
 */
export function Count({ value, className }: { value: number; className?: string }) {
  const digits = useRef<HTMLSpanElement>(null);
  const [last, setLast] = useState({ value, rises: 0 });
  if (last.value !== value) setLast({ value, rises: last.rises + (value > last.value ? 1 : 0) });

  useEffect(() => {
    if (!last.rises || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    digits.current?.animate(
      [
        { color: 'var(--bad)', backgroundColor: 'var(--bad-soft)' },
        { color: 'var(--bad)', backgroundColor: 'var(--bad-soft)', offset: 0.3 },
        {},
      ],
      { duration: 900, easing: 'ease-out' },
    );
  }, [last.rises]);

  return (
    <span className={cn('relative inline-grid tabular-nums', className)}>
      {/* The ticking digits are drawn by CSS (a counter), which screen readers don't read reliably. */}
      <span className="sr-only">{value}</span>
      <span ref={digits} aria-hidden="true" className="count-tick rounded-[3px]" style={{ '--n': value } as React.CSSProperties} />
    </span>
  );
}

/**
 * Which keys arrived in this render of a list that was already showing, so they
 * can be marked new. A whole new set (another page, another filter, the first
 * load) shares no keys with the last one and marks nothing.
 */
export function useFreshKeys(keys: string[]) {
  const signature = keys.join('\u0000');
  const [state, setState] = useState(() => ({ signature, seen: new Set(keys), fresh: new Set<string>() }));
  if (state.signature !== signature) {
    const continued = keys.some((k) => state.seen.has(k));
    setState({
      signature,
      seen: new Set(keys),
      fresh: continued ? new Set(keys.filter((k) => !state.seen.has(k))) : new Set<string>(),
    });
  }
  return state.fresh;
}
