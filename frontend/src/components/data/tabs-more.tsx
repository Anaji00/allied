'use client';

import type { ReactNode } from 'react';
import { NativeSelect, SectionTabs } from '@/components/data/filters';

/**
 * A few tabs in view, the rest behind one "More" menu, so a page with six sections doesn't open with six choices.
 * The menu shows the open section when it is one of the hidden ones.
 */
export function TabsWithMore<T extends string>({
  tabs,
  show,
  value,
  onChange,
  label,
  moreHint,
}: {
  tabs: readonly (readonly [T, ReactNode])[];
  /** How many tabs stay in view. */
  show: number;
  value: T;
  onChange: (value: T) => void;
  label: string;
  /** Shown on the More menu itself, e.g. "2 need you", so something waiting inside is not out of sight. */
  moreHint?: string;
}) {
  const main = tabs.slice(0, show);
  const more = tabs.slice(show);
  if (more.length === 0) return <SectionTabs label={label} value={value} onChange={onChange} tabs={tabs} />;
  const inMore = more.some(([t]) => t === value);
  return (
    <div className="flex flex-wrap items-end justify-between gap-2">
      <SectionTabs label={label} value={value} onChange={onChange} tabs={main} />
      <NativeSelect
        aria-label={`More ${label.toLowerCase()}`}
        value={inMore ? value : ''}
        onChange={(e) => e.target.value && onChange(e.target.value as T)}
        className={`mb-1 h-8 w-auto text-xs ${inMore ? 'border-foreground font-medium' : 'text-muted-foreground'}`}
      >
        <option value="">{moreHint ? `More… (${moreHint})` : 'More…'}</option>
        {more.map(([t, text]) => (
          <option key={t} value={t}>
            {text}
          </option>
        ))}
      </NativeSelect>
      {moreHint && !inMore && <span className="mb-1.5 text-xs font-medium text-warn">{moreHint}</span>}
    </div>
  );
}
