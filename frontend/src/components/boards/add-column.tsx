'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';

const TYPES: [string, string][] = [
  ['text', 'Text'],
  ['long text', 'Long text'],
  ['number', 'Number'],
  ['status', 'Status (pick from a list)'],
  ['dropdown', 'Dropdown'],
  ['date', 'Date'],
  ['people', 'People'],
  ['checkbox', 'Tick box'],
  ['link', 'Link'],
  ['email', 'Email'],
  ['timeline', 'Timeline'],
  ['rating', 'Rating'],
  ['hour', 'Time of day'],
];

/** The "+" at the end of the column headings: name it, pick what it holds, and it is saved as a change waiting for monday. */
export function AddColumn({ boardId }: { boardId: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [type, setType] = useState('text');
  const [labels, setLabels] = useState('');
  const [busy, setBusy] = useState(false);
  const needsLabels = type === 'status' || type === 'dropdown';
  const list = labels
    .split(/[\n,]/)
    .map((l) => l.trim())
    .filter(Boolean);
  const ready = title.trim() && (!needsLabels || list.length > 0);

  async function save() {
    setBusy(true);
    try {
      const res = await authFetch(`/api/v1/board-edit/boards/${encodeURIComponent(boardId)}/columns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim(), type, ...(needsLabels ? { labels: list } : {}) }),
      });
      const body = (await res.json().catch(() => null)) as { message?: string; detail?: string } | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      toast.success('Column saved, waiting to go to monday', { description: body?.message });
      void client.invalidateQueries({ queryKey: ['monday-push'] });
      setOpen(false);
      setTitle('');
      setLabels('');
      setType('text');
    } catch (e) {
      toast.error('The column wasn’t added', { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        aria-label="Add a column"
        title="Add a column"
        onClick={() => setOpen(true)}
        className="grid size-7 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Plus className="size-4" />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Add a column</DialogTitle>
            <DialogDescription>It is saved here first and goes to monday only when a manager sends the changes.</DialogDescription>
          </DialogHeader>
          <form
            id="add-column"
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (ready) void save();
            }}
          >
            <Field label="Name">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
            </Field>
            <Field label="What does it hold?">
              <NativeSelect value={type} onChange={(e) => setType(e.target.value)}>
                {TYPES.map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            {needsLabels && (
              <Field label="The choices" hint="One per line, or separated by commas.">
                <Textarea rows={3} value={labels} onChange={(e) => setLabels(e.target.value)} />
              </Field>
            )}
          </form>
          <DialogFooter>
            {!ready && (
              <span className="mr-auto text-xs text-muted-foreground">
                {needsLabels ? 'Needs a name and at least one choice.' : 'Give it a name.'}
              </span>
            )}
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="add-column" disabled={!ready || busy}>
              {busy && <Loader2 className="animate-spin" />}
              Add it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
