'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Lock, Pencil } from 'lucide-react';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Field = {
  field: string;
  label: string;
  type: string;
  editable: boolean;
  why_locked?: string;
  choices?: string[] | { value: string; label: string }[];
  value?: unknown;
};

const show = (v: unknown) => (v === null || v === undefined || v === '' ? '–' : String(v));

/**
 * The details of any record (job, quote request, purchase order, shipment, customer, vendor, product) as a list you can edit in place:
 * click a value, type, press Enter. Dates, amounts and people are read as you type them ("fri", "$1,200", a name). A locked field
 * says why. Changes are saved to the record at once, with the server's own rules.
 */
export function RecordFields({ kind, refId, title = 'Details' }: { kind: string; refId: string; title?: string }) {
  const client = useQueryClient();
  const base = `/api/v1/records/${encodeURIComponent(kind)}/${encodeURIComponent(refId)}`;
  const [editing, setEditing] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ['record-fields', kind, refId],
    queryFn: async (): Promise<Field[] | null> => {
      const res = await authFetch(`${base}/fields`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? (((await res.json()) as { fields?: Field[] }).fields ?? []) : null;
    },
    retry: false,
  });
  const save = useMutation({
    mutationFn: async ({ field, text }: { field: string; text: string }) => {
      const res = await authFetch(base, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ field, text }),
      });
      const body = (await res.json().catch(() => null)) as { detail?: string; understood?: string; message?: string } | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      return body ?? {};
    },
    onSuccess: (r) => {
      toast.success('Saved', { description: r.understood });
      setEditing(null);
      void client.invalidateQueries({ queryKey: ['record-fields', kind, refId] });
      void client.invalidateQueries({ queryKey: ['jobs'] });
      void client.invalidateQueries({ queryKey: ['rfqs'] });
      void client.invalidateQueries({ queryKey: ['shipments'] });
      void client.invalidateQueries({ queryKey: ['orders'] });
    },
    onError: (e) => toast.error('That wasn’t saved', { description: e.message }),
  });

  if (!list.data || list.data.length === 0) return null;
  return (
    <section aria-label={title} className="grid gap-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      <dl className="grid grid-cols-[minmax(7rem,max-content)_minmax(0,1fr)] gap-x-4 gap-y-1 text-[13px]">
        {list.data.map((f) => (
          <div key={f.field} className="contents">
            <dt className="py-1 text-muted-foreground">{f.label}</dt>
            <dd className="min-w-0 py-0.5">
              {editing === f.field ? (
                <Editor
                  field={f}
                  busy={save.isPending}
                  onCancel={() => setEditing(null)}
                  onSave={(text) =>
                    text.trim() === show(f.value).replace('–', '') ? setEditing(null) : save.mutate({ field: f.field, text })
                  }
                />
              ) : f.editable ? (
                <button
                  type="button"
                  onClick={() => setEditing(f.field)}
                  className={cn(
                    'group inline-flex max-w-full items-center gap-1.5 rounded px-1 py-0.5 text-left break-words hover:bg-muted',
                    show(f.value) === '–' && 'text-muted-foreground',
                  )}
                  title="Click to change"
                >
                  {show(f.value)}
                  <Pencil className="size-3 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100" aria-hidden />
                </button>
              ) : (
                <span
                  className="inline-flex items-center gap-1.5 px-1 py-0.5 text-muted-foreground"
                  title={f.why_locked || 'This can’t be changed'}
                >
                  {show(f.value)}
                  <Lock className="size-3 shrink-0" aria-hidden />
                  {f.why_locked && <span className="text-xs">{f.why_locked}</span>}
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Editor({ field, busy, onSave, onCancel }: { field: Field; busy: boolean; onSave: (text: string) => void; onCancel: () => void }) {
  const choices = (field.choices ?? []).map((c) => (typeof c === 'string' ? { value: c, label: c } : c));
  const base = 'h-8 w-full max-w-sm rounded-md border border-ring bg-background px-2 text-[13px] outline-none ring-2 ring-ring/30';
  if (field.type === 'choice' && choices.length > 0) {
    return (
      <select
        autoFocus
        className={base}
        disabled={busy}
        defaultValue={String(field.value ?? '')}
        onChange={(e) => e.target.value && onSave(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && onCancel()}
        onBlur={onCancel}
      >
        <option value="">Choose…</option>
        {choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      autoFocus
      className={base}
      disabled={busy}
      defaultValue={field.value === null || field.value === undefined ? '' : String(field.value)}
      placeholder={field.type === 'date' ? 'e.g. fri, nov 20, +3d' : field.type === 'email' ? 'a name or an email address' : undefined}
      onFocus={(e) => e.currentTarget.select()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onSave(e.currentTarget.value);
        else if (e.key === 'Escape') onCancel();
      }}
      onBlur={onCancel}
    />
  );
}
