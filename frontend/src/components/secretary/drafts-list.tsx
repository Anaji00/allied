'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Field } from '@/components/data/filters';
import { useInbox, type InboxItem } from '@/components/inbox/use-inbox';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { editDraft } from '@/lib/assistant-api';
import { ago } from '@/lib/format';
import { useUndoQueue } from '@/lib/undo';

/**
 * Every email waiting for a person's OK: who it goes to, what it says, why it was written. Send waits a few seconds so it can be
 * undone; nothing leaves until then. Edit changes the words first.
 */
export function DraftsList() {
  const inbox = useInbox();
  const client = useQueryClient();
  const undo = useUndoQueue();
  const [gone, setGone] = useState<Set<string>>(() => new Set());
  const [armed, setArmed] = useState<string | null>(null);
  const [editing, setEditing] = useState<InboxItem | null>(null);
  const drafts = inbox.items.filter((i) => i.source === 'drafts' && i.draft && !gone.has(i.key));

  if (inbox.isPending) return <RowsSkeleton rows={4} />;
  if (drafts.length === 0)
    return (
      <EmptyState title="No drafts waiting">Nothing is waiting for your OK. Write a reply to a mail, or press “New draft”.</EmptyState>
    );

  function run(item: InboxItem, id: 'send' | 'cancel') {
    const action = item.actions.find((a) => a.id === id);
    if (!action?.commit) return;
    setGone((g) => new Set(g).add(item.key));
    const back = () =>
      setGone((g) => {
        const n = new Set(g);
        n.delete(item.key);
        return n;
      });
    undo(`${action.verb}: ${item.title}`, {
      commit: async (keepalive) => {
        await action.commit!(keepalive);
        void client.invalidateQueries({ queryKey: ['inbox'] });
        void client.invalidateQueries({ queryKey: ['today'] });
      },
      onUndo: back,
      onError: (e) => {
        back();
        toast.error('That didn’t go through', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  return (
    <>
      <ul className="overflow-hidden rounded-lg border bg-card" aria-label="Drafts waiting for your OK">
        {drafts.map((i) => {
          const d = i.draft!;
          const email = d.email;
          const send = i.actions.find((a) => a.id === 'send');
          return (
            <li key={i.key} className="grid gap-1 border-b p-3 last:border-b-0 sm:grid-cols-[1fr_auto] sm:items-start">
              <div className="grid min-w-0 gap-0.5">
                <p className="text-[13px]">
                  <span className="text-muted-foreground">To </span>
                  <span className="font-medium">{email?.to || 'Nobody yet'}</span>
                  {email?.to && /@/.test(email.to) && !/@alliedweldingsystems\.com\s*$/i.test(email.to.trim()) && (
                    <span className="ml-1.5 rounded-full bg-warn-soft px-1.5 py-0.5 text-[11px] font-normal text-warn">outside Allied</span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {' '}
                    · {i.kind}
                    {i.at ? ` · ${ago(i.at)}` : ''}
                  </span>
                </p>
                <p className="truncate text-[13px] font-medium">{email?.subject || i.title}</p>
                {email?.body && <p className="line-clamp-2 text-xs text-pretty text-muted-foreground">{email.body}</p>}
                {d.why[0] && <p className="text-xs text-muted-foreground">Why: {d.why[0]}</p>}
                {(d.attachments ?? []).length > 0 && (
                  <p className="text-xs text-muted-foreground">Attached: {(d.attachments ?? []).map((a) => a.name).join(', ')}</p>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5 sm:justify-end">
                {send && (
                  <Button
                    size="sm"
                    variant={armed === i.key ? 'destructive' : 'default'}
                    onClick={() => {
                      // Anything that goes outside asks once more.
                      if (armed !== i.key) return setArmed(i.key);
                      setArmed(null);
                      run(i, 'send');
                    }}
                    onBlur={() => setArmed((a) => (a === i.key ? null : a))}
                  >
                    {armed === i.key ? 'Click again to send' : send.label}
                  </Button>
                )}
                {email && (
                  <Button size="sm" variant="outline" onClick={() => setEditing(i)}>
                    Edit
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={() => run(i, 'cancel')}>
                  Cancel it
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {editing && <EditDraft item={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function EditDraft({ item, onClose }: { item: InboxItem; onClose: () => void }) {
  const client = useQueryClient();
  const e = item.draft!.email ?? {};
  const [to, setTo] = useState(e.to ?? '');
  const [subject, setSubject] = useState(e.subject ?? '');
  const [body, setBody] = useState(e.body ?? '');
  const [busy, setBusy] = useState(false);
  // Each address is checked before saving: a bad shape stops the save; an address outside Allied only warns.
  const addresses = to
    .split(/[,;]/)
    .map((a) => a.trim())
    .filter(Boolean);
  const badShape = addresses.length === 0 || addresses.some((a) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(a));
  const outside = [
    ...new Set(addresses.filter((a) => /@/.test(a) && !a.toLowerCase().endsWith('@alliedweldingsystems.com')).map((a) => a.split('@')[1])),
  ];
  async function save() {
    setBusy(true);
    try {
      await editDraft(item.draft!.id, { to, subject, body });
      toast.success('Draft changed. It is still waiting for your OK.');
      void client.invalidateQueries({ queryKey: ['inbox'] });
      onClose();
    } catch (err) {
      toast.error('The draft wasn’t changed', { description: err instanceof Error ? err.message : undefined });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit the draft</DialogTitle>
          <DialogDescription>Change the words, then close. It stays in Drafts until you send it.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <Field label="To">
            <Input value={to} onChange={(ev) => setTo(ev.target.value)} />
            {badShape && <span className="text-xs text-bad">This does not look like an email address. Check it.</span>}
            {!badShape && outside.length > 0 && (
              <span className="text-xs text-warn">This address is outside Allied: {outside.join(', ')}. Check it before you send.</span>
            )}
          </Field>
          <Field label="Subject">
            <Input value={subject} onChange={(ev) => setSubject(ev.target.value)} />
          </Field>
          <Field label="Message">
            <Textarea rows={9} value={body} onChange={(ev) => setBody(ev.target.value)} />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={busy || badShape} onClick={() => void save()}>
            Save changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
