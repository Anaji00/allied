'use client';

import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

/** Appears only while rows are ticked: how many, the one group action, and a way to clear. */
export function BulkBar({
  count,
  noun,
  actionLabel,
  busy,
  onAction,
  onClear,
}: {
  count: number;
  noun: string;
  actionLabel: string;
  busy?: boolean;
  onAction: () => void;
  onClear: () => void;
}) {
  if (count === 0) return null;
  return (
    <div role="group" aria-label="Group action" className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
      <span className="font-medium tabular-nums">
        {count} {noun}
        {count === 1 ? '' : 's'} ticked
      </span>
      <Button size="sm" disabled={busy} onClick={onAction}>
        {busy && <Loader2 className="animate-spin" />}
        {actionLabel} {count}
      </Button>
      <Button size="sm" variant="ghost" onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
