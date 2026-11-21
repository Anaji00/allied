'use client';

import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DrawingCompare } from './drawing-compare';

/** Open it from anywhere: window.dispatchEvent(new Event('allied:compare')). Drop two drawings to see what differs. */
export function CompareHost() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = () => setOpen(true);
    window.addEventListener('allied:compare', on);
    return () => window.removeEventListener('allied:compare', on);
  }, []);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-h-[92svh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Compare two drawings</DialogTitle>
          <DialogDescription>Drop the old one and the new one. Red is only on the first, blue only on the second.</DialogDescription>
        </DialogHeader>
        {open && <DrawingCompare />}
      </DialogContent>
    </Dialog>
  );
}

export const openCompare = () => window.dispatchEvent(new Event('allied:compare'));
