'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { type FormField, type FormTemplate } from '@/components/projects/required-form';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';

const TYPES: [FormField['type'], string][] = [
  ['text', 'Short answer'],
  ['longtext', 'Long answer'],
  ['choice', 'Pick one'],
  ['number', 'Number'],
  ['date', 'Date'],
  ['yesno', 'Tick box'],
];
const SELECT =
  'h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50';

type Draft = Omit<FormTemplate, 'id'> & { id?: string };
const blank = (): Draft => ({
  name: '',
  enabled: true,
  applies_to: [],
  instructions: '',
  fields: [],
});

async function send(path: string, method: string, body?: unknown) {
  const res = await authFetch(`/api/v1/forms/templates${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t save (${res.status}).`);
  }
  return res.status === 204 ? null : res.json().catch(() => null);
}

const slug = (text: string, taken: Set<string>) => {
  const base =
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_|_$/g, '')
      .slice(0, 30) || 'question';
  let out = base;
  for (let n = 2; taken.has(out); n++) out = `${base}_${n}`;
  return out;
};

export function FormsEditor() {
  const client = useQueryClient();
  const [editing, setEditing] = useState<Draft | null>(null);
  const forms = useQuery({
    queryKey: ['forms', 'templates'],
    queryFn: async (): Promise<FormTemplate[] | null> => {
      const res = await authFetch('/api/v1/forms/templates', {
        cache: 'no-store',
      });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`Couldn’t load the forms (${res.status}).`);
      return res.json();
    },
  });
  const save = useMutation({
    mutationFn: async (v: { path: string; method: string; body?: unknown; done: string }) => {
      await send(v.path, v.method, v.body);
      return v.done;
    },
    onSuccess: (done) => {
      toast.success(done);
      setEditing(null);
      void client.invalidateQueries({ queryKey: ['forms'] });
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  if (forms.isPending) return <RowsSkeleton rows={3} />;
  if (forms.isError) return <ErrorState error={forms.error} onRetry={() => void forms.refetch()} />;
  if (!forms.data) return <p className="text-sm text-muted-foreground">Forms aren’t available yet.</p>;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">
          A form someone must fill in before sending design documents. It applies to milestones whose name contains one of its words.
        </p>
        <Button size="sm" className="ml-auto" onClick={() => setEditing(blank())}>
          <Plus /> Add a form
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {forms.data.map((f) => (
          <article key={f.id} className="grid content-start gap-2 rounded-lg border bg-card p-4">
            <header className="flex items-center gap-2">
              <h3 className="text-sm font-semibold">{f.name}</h3>
              <ToneBadge tone={f.enabled ? 'ok' : 'neutral'} className="ml-auto">
                {f.enabled ? 'On' : 'Off'}
              </ToneBadge>
            </header>
            <p className="text-xs text-muted-foreground">
              {f.fields.length} questions · for milestones with: {f.applies_to.join(', ') || 'nothing yet'}
            </p>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setEditing(structuredClone(f))}>
                Edit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending}
                onClick={() =>
                  save.mutate({
                    path: `/${encodeURIComponent(f.id)}`,
                    method: 'PUT',
                    body: { ...f, enabled: !f.enabled },
                    done: f.enabled ? 'Turned off' : 'Turned on',
                  })
                }
              >
                {f.enabled ? 'Turn off' : 'Turn on'}
              </Button>
            </div>
          </article>
        ))}
      </div>
      {editing && (
        <FormDialog
          draft={editing}
          busy={save.isPending}
          onClose={() => setEditing(null)}
          onSave={(d) =>
            save.mutate(
              d.id
                ? {
                    path: `/${encodeURIComponent(d.id)}`,
                    method: 'PUT',
                    body: d,
                    done: 'Form saved',
                  }
                : { path: '', method: 'POST', body: d, done: 'Form added' },
            )
          }
          onDelete={
            editing.id
              ? () => {
                  if (window.confirm(`Delete “${editing.name}”?`))
                    save.mutate({
                      path: `/${encodeURIComponent(editing.id!)}`,
                      method: 'DELETE',
                      done: 'Form deleted',
                    });
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

function FormDialog({
  draft,
  busy,
  onClose,
  onSave,
  onDelete,
}: {
  draft: Draft;
  busy: boolean;
  onClose: () => void;
  onSave: (d: Draft) => void;
  onDelete?: () => void;
}) {
  const [d, setD] = useState<Draft>(draft);
  const [words, setWords] = useState(draft.applies_to.join(', '));
  const setField = (i: number, patch: Partial<FormField>) =>
    setD((cur) => ({
      ...cur,
      fields: cur.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)),
    }));

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{d.id ? 'Edit form' : 'New form'}</DialogTitle>
          <DialogDescription>The questions someone answers before sending documents.</DialogDescription>
        </DialogHeader>
        <form
          id="form-editor"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (d.fields.length === 0) {
              toast.error('Add at least one question before saving.');
              return;
            }
            if (d.fields.some((f) => !f.label.trim())) {
              toast.error('Every question needs a label.');
              return;
            }
            if (d.fields.some((f) => f.type === 'choice' && (f.options ?? []).filter((o) => o.trim()).length < 2)) {
              toast.error('Multiple-choice questions need at least two options.');
              return;
            }
            onSave({
              ...d,
              applies_to: words
                .split(',')
                .map((w) => w.trim())
                .filter(Boolean),
            });
          }}
        >
          <Field label="Name">
            <Input required value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
          </Field>
          <Field label="Used for milestones with these words in the name" hint="Separate with commas: design, drawing, release">
            <Input value={words} onChange={(e) => setWords(e.target.value)} />
          </Field>
          <Field label="Instructions (shown above the questions)">
            <Textarea rows={2} value={d.instructions} onChange={(e) => setD({ ...d, instructions: e.target.value })} />
          </Field>
          <div className="grid gap-2">
            <p className="text-sm font-medium">Questions</p>
            {d.fields.map((f, i) => (
              <div key={f.key} className="grid gap-2 rounded-md border p-3">
                <div className="flex flex-wrap gap-2">
                  <Input
                    className="min-w-0 flex-1 basis-40"
                    aria-label="Question"
                    placeholder="Question"
                    value={f.label}
                    onChange={(e) => setField(i, { label: e.target.value })}
                  />
                  <select
                    aria-label="Kind of answer"
                    className={SELECT}
                    value={f.type}
                    onChange={(e) => setField(i, { type: e.target.value as FormField['type'] })}
                  >
                    {TYPES.map(([t, label]) => (
                      <option key={t} value={t}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Remove question ${f.label || i + 1}`}
                    onClick={() => setD({ ...d, fields: d.fields.filter((_, j) => j !== i) })}
                  >
                    <Trash2 />
                  </Button>
                </div>
                <Input
                  aria-label="Help text"
                  placeholder="Help text (optional)"
                  value={f.help ?? ''}
                  onChange={(e) => setField(i, { help: e.target.value })}
                />
                {f.type === 'choice' && (
                  <Input
                    aria-label="Choices"
                    placeholder="Choices, separated by commas"
                    value={(f.options ?? []).join(', ')}
                    onChange={(e) =>
                      setField(i, {
                        options: e.target.value
                          .split(',')
                          .map((o) => o.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                )}
                <div className="flex flex-wrap items-center gap-4 text-sm">
                  {f.type === 'yesno' ? (
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="size-4"
                        checked={Boolean(f.must_be_yes)}
                        onChange={(e) => setField(i, { must_be_yes: e.target.checked })}
                      />
                      Must be ticked
                    </label>
                  ) : (
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="size-4"
                        checked={Boolean(f.required)}
                        onChange={(e) => setField(i, { required: e.target.checked })}
                      />
                      Required
                    </label>
                  )}
                  {(f.type === 'text' || f.type === 'longtext') && (
                    <label className="flex items-center gap-2">
                      At least
                      <Input
                        aria-label="Fewest characters"
                        className="h-8 w-20"
                        inputMode="numeric"
                        value={f.min_chars ?? ''}
                        onChange={(e) =>
                          setField(i, {
                            min_chars: e.target.value ? Number(e.target.value.replace(/\D/g, '')) : undefined,
                          })
                        }
                      />
                      characters
                    </label>
                  )}
                </div>
              </div>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="justify-self-start"
              onClick={() =>
                setD((cur) => ({
                  ...cur,
                  fields: [
                    ...cur.fields,
                    {
                      key: slug(`question ${cur.fields.length + 1}`, new Set(cur.fields.map((f) => f.key))),
                      label: '',
                      type: 'text',
                      required: true,
                    },
                  ],
                }))
              }
            >
              <Plus /> Add a question
            </Button>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="size-4" checked={d.enabled} onChange={(e) => setD({ ...d, enabled: e.target.checked })} />
            Turned on
          </label>
        </form>
        <DialogFooter>
          {onDelete && (
            <Button type="button" variant="ghost" className="mr-auto text-bad" onClick={onDelete}>
              Delete
            </Button>
          )}
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {!d.name.trim() && <span className="mr-auto text-xs text-muted-foreground">Give it a name to save.</span>}
          <Button type="submit" form="form-editor" disabled={busy || !d.name.trim()}>
            {busy && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
