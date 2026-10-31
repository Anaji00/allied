'use client';

import { useHotkeys } from 'react-hotkeys-hook';

/**
 * J/K (or the arrow keys) move the selection through a list, Escape clears it.
 * `open(undefined)` closes; the page decides what selecting means.
 */
export function useListNav<T>(rows: T[], index: number, open: (row?: T) => void) {
  useHotkeys(['j', 'ArrowDown'], () => rows[index + 1] && open(rows[index + 1]), { preventDefault: true }, [rows, index]);
  useHotkeys(
    ['k', 'ArrowUp'],
    () => (index > 0 ? open(rows[index - 1]) : index === -1 && rows[0] && open(rows[0])),
    { preventDefault: true },
    [rows, index],
  );
  useHotkeys('escape', () => open(undefined), [open]);
}
