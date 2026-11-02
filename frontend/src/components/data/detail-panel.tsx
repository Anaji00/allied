'use client';

import { ViewTransition, startTransition, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useMediaQuery } from '@/hooks/use-media-query';

/**
 * The record beside its list. On wide screens it sits inline, so the list stays
 * visible and J/K keep working; on narrow ones it slides over as a sheet.
 */
export function DetailPanel({
  open,
  onClose,
  title,
  description,
  children,
  recordName,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** Same name as the row's (DataTable recordName): the row's label travels into this heading. */
  recordName?: string;
}) {
  const wide = useMediaQuery('(min-width: 1280px)');
  const close = () => startTransition(onClose);

  if (wide) {
    if (!open) return null;
    return (
      // Slides in beside the list; the heading's morph comes from the row.
      <ViewTransition enter="panel-in" exit="panel-out" default="none">
        <aside
          aria-label="Details"
          className="sticky top-16 grid max-h-[calc(100svh-5rem)] min-w-0 grid-rows-[auto_minmax(0,1fr)] overflow-hidden rounded-lg border bg-card"
        >
          <header className="flex items-start gap-3 border-b px-4 py-3">
            <div className="grid min-w-0 gap-0.5">
              <h2 className="truncate text-base font-semibold">
                {recordName ? (
                  <ViewTransition name={recordName} share="record" default="none">
                    <span className="inline-block max-w-full truncate align-bottom">{title}</span>
                  </ViewTransition>
                ) : (
                  title
                )}
              </h2>
              {description && <div className="text-xs text-muted-foreground">{description}</div>}
            </div>
            <Button variant="ghost" size="icon-sm" className="ml-auto shrink-0" onClick={close} aria-label="Close details">
              <X />
            </Button>
          </header>
          <div className="overflow-y-auto px-4 py-4">{children}</div>
        </aside>
      </ViewTransition>
    );
  }

  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      {/* data-[side=right]: outranks the sheet's own 3/4 width, so phones get the whole screen. */}
      <SheetContent side="right" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>{title}</SheetTitle>
          {description && <SheetDescription render={<div />}>{description}</SheetDescription>}
        </SheetHeader>
        <div className="overflow-y-auto px-4 py-4">{children}</div>
      </SheetContent>
    </Sheet>
  );
}

/** Label and value pairs down the panel. */
export function Facts({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
