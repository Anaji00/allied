'use client';

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** Underlined tabs that switch a page or panel between sections. */
export function SectionTabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  small,
}: {
  tabs: readonly (readonly [T, ReactNode])[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  small?: boolean;
}) {
  // Tabs pattern: one tab in the Tab order; Left/Right/Home/End move between them.
  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, i: number) {
    const last = tabs.length - 1;
    const to = e.key === 'ArrowRight' ? (i === last ? 0 : i + 1) : e.key === 'ArrowLeft' ? (i === 0 ? last : i - 1) : e.key === 'Home' ? 0 : e.key === 'End' ? last : -1;
    if (to < 0) return;
    e.preventDefault();
    onChange(tabs[to][0]);
    (e.currentTarget.parentElement?.children[to] as HTMLElement | undefined)?.focus();
  }

  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b">
      {tabs.map(([key, text], i) => (
        <button
          key={key}
          role="tab"
          type="button"
          aria-selected={value === key}
          tabIndex={value === key ? 0 : -1}
          onClick={() => onChange(key)}
          onKeyDown={(e) => onKeyDown(e, i)}
          className={cn(
            '-mb-px border-b-2 px-3 whitespace-nowrap',
            small ? 'py-1.5 text-[13px]' : 'py-2 text-sm',
            value === key ? 'border-foreground font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

export const chipClass = (active: boolean) =>
  cn(
    'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium whitespace-nowrap transition-colors',
    active ? 'border-foreground bg-foreground text-background' : 'bg-card text-muted-foreground hover:text-foreground',
  );

/** One-of-many filter chips, each with an optional count. */
export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly { value: T; label: string; count?: number; tone?: 'bad' | 'warn' }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} className={chipClass(value === o.value)} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count != null && (
            <span
              className={cn(
                'tabular-nums',
                value !== o.value && o.count > 0 && o.tone === 'bad' && 'text-bad',
                value !== o.value && o.count > 0 && o.tone === 'warn' && 'text-warn',
                value === o.value && 'opacity-70',
              )}
            >
              {o.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

/** A labelled form control, with an optional hint under it. */
export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn('grid gap-1.5 text-sm', className)}>
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

/** A plain select styled like the inputs; native so it works well on phones. */
export function NativeSelect({ className, ...props }: React.ComponentProps<'select'>) {
  return (
    <select
      {...props}
      className={cn(
        'h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30',
        className,
      )}
    />
  );
}

/** A small heading inside a panel. */
export function PanelHeading({ children }: { children: ReactNode }) {
  return <h3 className="text-xs font-medium text-muted-foreground">{children}</h3>;
}
