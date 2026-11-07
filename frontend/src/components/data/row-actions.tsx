'use client';

import { Loader2, MoreHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

export type RowAction = { label: string; onClick: () => void; busy?: boolean; disabled?: boolean; hint?: string };

/**
 * The end of a table row: the one thing most people do next as a button, and everything
 * else behind a "…" menu. Clicking either does not open the row's detail panel.
 */
export function RowActions({ main, more = [], rowLabel }: { main?: RowAction | null; more?: RowAction[]; rowLabel: string }) {
  if (!main && !more.length) return null;
  return (
    <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      {main && (
        <Button
          size="xs"
          variant="outline"
          disabled={main.disabled || main.busy}
          title={main.hint}
          onClick={main.onClick}
          aria-label={`${main.label}: ${rowLabel}`}
        >
          {main.busy && <Loader2 className="animate-spin" />}
          {main.label}
        </Button>
      )}
      {more.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger
            className="grid size-6 place-items-center rounded-md outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`More for ${rowLabel}`}
          >
            <MoreHorizontal className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {more.map((a) => (
              <DropdownMenuItem key={a.label} disabled={a.disabled || a.busy} onClick={a.onClick}>
                {a.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
