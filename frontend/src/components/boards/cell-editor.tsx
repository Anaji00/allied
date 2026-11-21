'use client';

import { useRef } from 'react';
import { useAppPeople } from '@/components/data/pick';
import type { Column } from './use-boards';

export type Move = 'down' | 'right' | 'left' | 'stay';

/**
 * Type into a board cell like a spreadsheet. Enter saves and moves down, Tab moves right, Shift+Tab left, Esc cancels.
 * Status and dropdown cells open a list that finds a choice as you type. Nothing leaves the app: a save is a change waiting for monday.
 */
export function CellEditor({
  column,
  value,
  onSave,
  onCancel,
}: {
  column: Column;
  value: string;
  /** The new text and where the cursor goes next. */
  onSave: (text: string, move: Move) => void;
  onCancel: () => void;
}) {
  const done = useRef(false);
  const people = useAppPeople().data ?? [];
  const isPeople = ['people', 'person', 'multiple-person'].includes(column.type);
  const finish = (text: string, move: Move) => {
    if (done.current) return;
    done.current = true;
    onSave(text, move);
  };
  const cancel = () => {
    if (done.current) return;
    done.current = true;
    onCancel();
  };
  const keys = (e: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>, text: string) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      cancel();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      finish(text, 'down');
    } else if (e.key === 'Tab') {
      e.preventDefault();
      e.stopPropagation();
      finish(text, e.shiftKey ? 'left' : 'right');
    }
  };
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  const base =
    'h-8 w-full min-w-24 rounded-md border border-ring bg-background px-2 text-[13px] outline-none ring-2 ring-ring/30 focus-visible:ring-ring/50';

  if (column.type === 'status' || column.type === 'color' || column.type === 'dropdown') {
    const labels = Object.keys(column.labels ?? {});
    return (
      <select
        autoFocus
        aria-label={column.title}
        className={base}
        defaultValue={labels.includes(value) ? value : ''}
        onClick={stop}
        onKeyDown={(e) => keys(e, e.currentTarget.value)}
        onChange={(e) => e.target.value && finish(e.target.value, 'stay')}
        onBlur={cancel}
      >
        <option value="">Choose…</option>
        {labels.map((l) => (
          <option key={l} value={l}>
            {l}
          </option>
        ))}
      </select>
    );
  }
  if (column.type === 'checkbox') {
    return (
      <select
        autoFocus
        aria-label={column.title}
        className={base}
        defaultValue={/^(yes|true|checked|v|1)$/i.test(value) ? 'Yes' : value ? 'No' : ''}
        onClick={stop}
        onKeyDown={(e) => keys(e, e.currentTarget.value)}
        onChange={(e) => e.target.value && finish(e.target.value, 'stay')}
        onBlur={cancel}
      >
        <option value="">Choose…</option>
        <option value="Yes">Yes</option>
        <option value="No">No</option>
      </select>
    );
  }
  return (
    <>
      {isPeople && (
        <datalist id={`people-${column.id}`}>
          {people.map((p) => (
            <option key={p.email} value={p.name || p.email} />
          ))}
        </datalist>
      )}
      <input
        list={isPeople ? `people-${column.id}` : undefined}
        autoFocus
        aria-label={column.title}
        className={base}
        type="text"
        placeholder={column.type === 'date' ? 'e.g. fri, nov 20, +3d' : undefined}
        inputMode={column.type === 'numbers' ? 'decimal' : undefined}
        defaultValue={value}
        onClick={stop}
        onFocus={(e) => e.currentTarget.select()}
        onKeyDown={(e) => keys(e, e.currentTarget.value)}
        onBlur={(e) => (e.currentTarget.value.trim() === value.trim() ? cancel() : finish(e.currentTarget.value, 'stay'))}
      />
    </>
  );
}
