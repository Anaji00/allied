'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { Check, Copy, Loader2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { editDraft } from '@/lib/assistant-api';
import { authFetch } from '@/lib/api';

export type BoardRow = { item_name: string; customer: string; status: string; notes: string };

async function decide(id: string, confirm: boolean) {
  const res = await authFetch(`/api/v1/assistant/actions/${encodeURIComponent(id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm }),
  });
  const body = (await res.json().catch(() => null)) as {
    status?: string;
    result?: { done?: string; error?: string; change_id?: number };
    detail?: string;
  } | null;
  if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
  if (body?.status === 'FAILED') throw new Error(body.result?.error ?? 'It didn’t go through.');
  return body;
}

/**
 * A suggested row for the Open RFQs board. Edit it, then "Add to monday". While monday is read-only
 * nothing is added: the row comes back as text to copy and add by hand, and this says so plainly.
 */
export function RowDraft({ id, row, onDone }: { id: string; row: BoardRow; onDone: () => void }) {
  const [form, setForm] = useState<BoardRow>(row);
  const [busy, setBusy] = useState<'add' | 'skip' | null>(null);
  const [result, setResult] = useState<string | null>(null);
  // Set when the row was staged on the monday changes list instead of sent.
  const [changeId, setChangeId] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const set = (k: keyof BoardRow) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const changed = (Object.keys(form) as (keyof BoardRow)[]).some((k) => form[k] !== row[k]);

  async function add() {
    setBusy('add');
    try {
      if (changed) await editDraft(id, form);
      const res = await decide(id, true);
      setChangeId(res?.result?.change_id ?? null);
      setResult(res?.result?.done || 'Done.');
    } catch (e) {
      toast.error('The row wasn’t added', { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(null);
    }
  }
  async function skip() {
    setBusy('skip');
    try {
      await decide(id, false);
      toast('Skipped');
      onDone();
    } catch (e) {
      toast.error('That didn’t go through', { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(null);
    }
  }

  // The read-only message ends with the row itself, as plain text: that part is what gets copied.
  const staged = changeId !== null || (result ? /staged/i.test(result) : false);
  const readOnly = !staged && result ? /read-only|isn.t connected/i.test(result) : false;
  const rowText = result
    ? result.includes(':') && readOnly
      ? result.slice(result.lastIndexOf('ready to add by hand:') + 'ready to add by hand:'.length).trim() || result
      : result
    : '';

  return (
    <div className="grid gap-3 rounded-lg border bg-muted/30 p-3">
      <p className="text-sm font-medium">Add to the Open RFQs board</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Item name">
          <Input value={form.item_name} onChange={set('item_name')} />
        </Field>
        <Field label="Customer">
          <Input value={form.customer} onChange={set('customer')} />
        </Field>
        <Field label="Status">
          <Input value={form.status} onChange={set('status')} />
        </Field>
      </div>
      <Field label="Notes">
        <Textarea rows={3} value={form.notes} onChange={set('notes')} />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={busy !== null || !form.item_name.trim()} onClick={() => void add()}>
          {busy === 'add' && <Loader2 className="animate-spin" />}
          Add to monday
        </Button>
        <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void skip()}>
          {busy === 'skip' && <Loader2 className="animate-spin" />}
          Skip
        </Button>
      </div>

      <Dialog
        open={result !== null}
        onOpenChange={(o) => {
          if (!o) {
            setResult(null);
            onDone();
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {staged ? 'Waiting to go to monday (not sent yet)' : readOnly ? 'Nothing was added to monday' : 'Added to monday'}
            </DialogTitle>
            <DialogDescription>
              {staged
                ? `The row${changeId !== null ? ` (change #${changeId})` : ''} is waiting on the monday changes list. A manager sends it from System, monday changes.`
                : readOnly
                  ? 'monday isn’t taking changes from the app, so it can’t add the row itself. Here it is, ready to add by hand.'
                  : 'The row is on the board.'}
            </DialogDescription>
          </DialogHeader>
          <pre className="max-h-60 overflow-auto rounded-md bg-muted p-3 font-sans text-[13px] break-words whitespace-pre-wrap">
            {rowText}
          </pre>
          <DialogFooter>
            {readOnly && (
              <Button
                variant="outline"
                onClick={() => {
                  void navigator.clipboard?.writeText(rowText).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  });
                }}
              >
                {copied ? <Check /> : <Copy />} {copied ? 'Copied' : 'Copy row'}
              </Button>
            )}
            <Button
              onClick={() => {
                setResult(null);
                onDone();
              }}
            >
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
