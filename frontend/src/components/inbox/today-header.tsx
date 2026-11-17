'use client';

import { cn } from '@/lib/utils';

type Tile = {
  key: string;
  label: string;
  count: number;
  tone?: 'bad' | 'warn';
};

const hello = (d = new Date()) => (d.getHours() < 12 ? 'Good morning' : d.getHours() < 18 ? 'Good afternoon' : 'Good evening');

/**
 * The top of the Inbox: a quiet greeting and four big numbers that are also the filter.
 * One glance says what needs you; one tap shows just that.
 */
export function TodayHeader({ name, tiles, value, onPick }: { name: string; tiles: Tile[]; value: string; onPick: (key: string) => void }) {
  const first = name.split(/[\s@]/)[0];
  const total = tiles[0]?.count ?? 0;
  const today = new Date().toLocaleDateString('en-CA', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
  return (
    <header className="grid min-w-0 gap-4">
      <div className="grid gap-0.5">
        <h2 className="text-xl font-semibold tracking-tight sm:text-2xl">
          {hello()}
          {first ? `, ${first[0].toUpperCase()}${first.slice(1)}` : ''}
        </h2>
        <p className="text-sm text-muted-foreground">
          {today} · {total === 0 ? 'All clear.' : total === 1 ? '1 thing needs you.' : `${total} things need you.`}
        </p>
      </div>
      <div role="tablist" aria-label="Show" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {tiles.map((t) => {
          const on = value === t.key;
          return (
            <button
              key={t.key}
              role="tab"
              type="button"
              aria-selected={on}
              onClick={() => onPick(t.key)}
              className={cn(
                'group grid min-w-0 gap-1 rounded-2xl border bg-card px-4 py-3 text-left transition-all',
                'hover:-translate-y-px hover:shadow-sm focus-visible:ring-2 focus-visible:ring-ring outline-none',
                on ? 'border-foreground/60 shadow-sm ring-1 ring-foreground/10' : 'border-border',
              )}
            >
              <span
                className={cn(
                  'text-3xl leading-none font-semibold tracking-tight tabular-nums transition-colors',
                  t.count > 0 && t.tone === 'bad' && 'text-bad',
                  t.count > 0 && t.tone === 'warn' && 'text-warn',
                  t.count === 0 && 'text-muted-foreground/60',
                )}
              >
                {t.count}
              </span>
              <span className={cn('text-xs', on ? 'font-medium text-foreground' : 'text-muted-foreground')}>{t.label}</span>
            </button>
          );
        })}
      </div>
    </header>
  );
}
