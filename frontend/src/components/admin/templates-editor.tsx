'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { ErrorState, RowsSkeleton } from '@/components/data/states';
import { ROLES, roleLabel, useTemplates, type Template } from '@/components/projects/plan-job';
import { type FormTemplate } from '@/components/projects/required-form';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';

const BASE = '/api/v1/workspace/project-templates';
type Draft = Omit<Template, 'id'> & { id?: string };

async function send(path: string, method: string, body?: unknown) {
  const res = await authFetch(`${BASE}${path}`, {
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
}

/** Managers: the standard steps of a kind of job, and how many working days after the start each is due. */
export function TemplatesEditor() {
  const client = useQueryClient();
  const templates = useTemplates();
  const [editing, setEditing] = useState<Draft | null>(null);
  const save = useMutation({
    mutationFn: async (v: { path: string; method: string; body?: unknown; done: string }) => {
      await send(v.path, v.method, v.body);
      return v.done;
    },
    onSuccess: (done) => {
      toast.success(done);
      setEditing(null);
      void client.invalidateQueries({ queryKey: ['templates'] });
    },
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  if (templates.isPending) return <RowsSkeleton rows={3} />;
  if (templates.isError) return <ErrorState error={templates.error} onRetry={() => void templates.refetch()} />;
  if (!templates.data) return <p className="text-sm text-muted-foreground">Job templates aren’t available yet.</p>;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">
          The standard steps of a kind of job. “Plan a new job” on the Projects page uses them.
        </p>
        <Button size="sm" className="ml-auto" onClick={() => setEditing({ name: '', milestones: [] })}>
          <Plus /> Add a template
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {templates.data.map((t) => (
          <article key={t.id} className="grid content-start gap-2 rounded-lg border bg-card p-4">
            <h3 className="text-sm font-semibold">{t.name}</h3>
            <p className="text-xs text-muted-foreground">
              {t.milestones.length} steps · up to day {Math.max(0, ...t.milestones.map((m) => m.offset_days))}
            </p>
            <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setEditing(structuredClone(t))}>
              Edit
            </Button>
          </article>
        ))}
      </div>
      {editing && (
        <TemplateDialog
          draft={editing}
          busy={save.isPending}
          onClose={() => setEditing(null)}
          onSave={(d) =>
            save.mutate(
              d.id
                ? { path: `/${encodeURIComponent(d.id)}`, method: 'PUT', body: d, done: 'Template saved' }
                : { path: '', method: 'POST', body: d, done: 'Template added' },
            )
          }
          onDelete={
            editing.id
              ? () => {
                  if (window.confirm(`Delete “${editing.name}”?`))
                    save.mutate({ path: `/${encodeURIComponent(editing.id!)}`, method: 'DELETE', done: 'Template deleted' });
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

function TemplateDialog({
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
  const forms = useQuery({
    queryKey: ['forms', 'templates'],
    queryFn: async (): Promise<FormTemplate[]> => {
      const res = await authFetch('/api/v1/forms/templates', { cache: 'no-store' });
      return res.ok ? ((await res.json()) as FormTemplate[]) : [];
    },
  });
  const setStep = (i: number, patch: Partial<Template['milestones'][number]>) =>
    setD((cur) => ({ ...cur, milestones: cur.milestones.map((m, j) => (j === i ? { ...m, ...patch } : m)) }));

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{d.id ? 'Edit template' : 'New template'}</DialogTitle>
          <DialogDescription>Each step says how many working days after the start it is due, and who does it.</DialogDescription>
        </DialogHeader>
        <form
          id="template-editor"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            onSave(d);
          }}
        >
          <Field label="Name">
            <Input required value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
          </Field>
          <div className="grid gap-2">
            <p className="text-sm font-medium">Steps</p>
            {d.milestones.map((m, i) => (
              <div key={i} className="grid gap-2 rounded-md border p-3">
                <div className="flex flex-wrap gap-2">
                  <Input
                    className="min-w-0 flex-1 basis-48"
                    aria-label="Step"
                    placeholder="Step"
                    value={m.name}
                    onChange={(e) => setStep(i, { name: e.target.value })}
                    required
                  />
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`Remove step ${m.name || i + 1}`}
                    onClick={() => setD({ ...d, milestones: d.milestones.filter((_, j) => j !== i) })}
                  >
                    <Trash2 />
                  </Button>
                </div>
                <div className="grid gap-2 sm:grid-cols-3">
                  <label className="grid gap-1 text-xs">
                    <span className="font-medium">Working days after the start</span>
                    <Input
                      inputMode="numeric"
                      value={m.offset_days}
                      onChange={(e) => setStep(i, { offset_days: Number(e.target.value.replace(/\D/g, '')) || 0 })}
                    />
                  </label>
                  <label className="grid gap-1 text-xs">
                    <span className="font-medium">Who does it</span>
                    <NativeSelect value={m.owner_role} onChange={(e) => setStep(i, { owner_role: e.target.value })}>
                      {ROLES.map(([v]) => (
                        <option key={v} value={v}>
                          {roleLabel(v)}
                        </option>
                      ))}
                    </NativeSelect>
                  </label>
                  <label className="grid gap-1 text-xs">
                    <span className="font-medium">Form to fill in</span>
                    <NativeSelect value={m.form ?? ''} onChange={(e) => setStep(i, { form: e.target.value })}>
                      <option value="">None</option>
                      {(forms.data ?? []).map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.name}
                        </option>
                      ))}
                    </NativeSelect>
                  </label>
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
                  milestones: [
                    ...cur.milestones,
                    { name: '', offset_days: Math.max(0, ...cur.milestones.map((m) => m.offset_days)), owner_role: 'engineer', form: '' },
                  ],
                }))
              }
            >
              <Plus /> Add a step
            </Button>
          </div>
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
          {!d.name.trim() ||
            (d.milestones.length === 0 && (
              <span className="mr-auto text-xs text-muted-foreground">Needs a name and at least one step.</span>
            ))}
          <Button type="submit" form="template-editor" disabled={busy || !d.name.trim() || d.milestones.length === 0}>
            {busy && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
