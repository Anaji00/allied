'use client';

import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Kbd } from '@/components/ui/kbd';

const SECTIONS: { title: string; rows: [string, string][] }[] = [
  {
    title: 'Anywhere',
    rows: [
      ['Ctrl K', 'Search everything, or do something'],
      ['?', 'Show this list'],
    ],
  },
  {
    title: 'Today',
    rows: [
      ['J / K', 'Move down / up the list'],
      ['E', 'Do the first action'],
      ['H', 'Snooze an alert for a day'],
      ['Enter', 'Open where it points'],
    ],
  },
  {
    title: 'Boards',
    rows: [
      ['Click a cell', 'Type or pick a value'],
      ['Enter', 'Save and move down'],
      ['Tab / Shift Tab', 'Move right / left'],
      ['Esc', 'Cancel'],
      ['Ctrl Z', 'Put back the last change'],
      ['Drag a value', 'Copy it onto another cell in the same column'],
    ],
  },
];

/** Press ? anywhere (outside a box you are typing in) to see the keyboard shortcuts. */
export function ShortcutsSheet() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (
        e.key === '?' &&
        tag !== 'INPUT' &&
        tag !== 'TEXTAREA' &&
        tag !== 'SELECT' &&
        !(e.target as HTMLElement | null)?.isContentEditable
      ) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Everything here can also be done with the mouse.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          {SECTIONS.map((s) => (
            <section key={s.title} className="grid gap-1.5">
              <h3 className="text-xs font-semibold text-muted-foreground">{s.title}</h3>
              <ul className="grid gap-1 text-[13px]">
                {s.rows.map(([keys, what]) => (
                  <li key={keys} className="flex items-baseline justify-between gap-3">
                    <span>{what}</span>
                    <Kbd>{keys}</Kbd>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
