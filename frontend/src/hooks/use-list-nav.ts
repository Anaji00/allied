'use client';

import { startTransition } from 'react';
import { useHotkeys } from 'react-hotkeys-hook';

// A focused table row handles its own arrow keys (moving focus, not the
// selection), so the page-level arrows step aside for it. J/K still work.
const onRow = (e: KeyboardEvent) => e.key.startsWith('Arrow') && e.target instanceof Element && e.target.closest('tbody tr') !== null;

/**
 * J/K (or the arrow keys) move the selection through a list, Escape clears it.
 * `open(undefined)` closes; the page decides what selecting means.
 */
export function useListNav<T>(rows: T[], index: number, openNow: (row?: T) => void) {
  // A transition, so moving through the list runs the record morph like a click does.
  const open = (row?: T) => startTransition(() => openNow(row));
  useHotkeys(['j', 'ArrowDown'], () => rows[index + 1] && open(rows[index + 1]), { preventDefault: true, ignoreEventWhen: onRow }, [rows, index]);
  useHotkeys(
    ['k', 'ArrowUp'],
    () => (index > 0 ? open(rows[index - 1]) : index === -1 && rows[0] && open(rows[0])),
    { preventDefault: true, ignoreEventWhen: onRow },
    [rows, index],
  );
  useHotkeys('escape', () => open(undefined), [open]);
}
