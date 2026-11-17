'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { words } from '@/lib/format';

export type FactDoc = { confirmed?: boolean; confirmed_by?: string | null; warnings?: string[] };

const shown = (v: unknown) => (Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? '' : String(v ?? ''));

/**
 * The facts read off one page. Reading a scan can get a number wrong, so a person checks them against the page,
 * fixes any that are off (clearing a box makes it blank again) and presses "Looks right". Nothing is used until then.
 */
export function DocFacts({
  doc,
  facts,
  canEdit,
  pending,
  onConfirm,
}: {
  doc: FactDoc;
  facts: [string, unknown][];
  canEdit: boolean;
  pending: boolean;
  onConfirm: (fields: Record<string, string>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const label = (k: string) => words(k).replace(/\bPo\b/g, 'PO').replace(/\bSo\b/g, 'SO');
  const line = facts.map(([k, v]) => `${label(k)}: ${shown(v)}`).join(' · ');
  const changed = () =>
    Object.fromEntries(facts.filter(([k, v]) => draft[k] !== undefined && draft[k] !== shown(v)).map(([k]) => [k, draft[k]]));

  const warn = (doc.warnings ?? []).map((w) => (
    <span key={w} className="text-xs text-warn">
      {w}
    </span>
  ));
  if (doc.confirmed && !editing) {
    return (
      <span className="text-muted-foreground">
        {line}
        <span className="ml-2 text-ok">Checked{doc.confirmed_by ? ` by ${doc.confirmed_by}` : ''}</span>
        {warn}
      </span>
    );
  }
  return (
    <span className="grid gap-1.5">
      {editing ? (
        <span className="grid gap-1.5 sm:grid-cols-2">
          {facts.map(([k, v]) => (
            <label key={k} className="grid gap-0.5 text-xs">
              <span className="text-muted-foreground">{label(k)}</span>
              <Input value={draft[k] ?? shown(v)} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
            </label>
          ))}
        </span>
      ) : (
        <span className="text-muted-foreground">{line}</span>
      )}
      {warn}
      <span className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-warn">Read from a scan: check it against the page.</span>
        {canEdit && (
          <>
            <Button size="xs" disabled={pending} onClick={() => onConfirm(editing ? changed() : {})}>
              {editing ? 'Save and confirm' : 'Looks right'}
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setEditing(!editing)}>
              {editing ? 'Cancel' : 'Fix a number'}
            </Button>
          </>
        )}
      </span>
    </span>
  );
}
