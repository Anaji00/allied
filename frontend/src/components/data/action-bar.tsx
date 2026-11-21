'use client';

import { useState, type ReactNode } from 'react';
import { toast } from 'sonner';
import { Copy, Download, Mail, MessageSquare, MoreHorizontal, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { authFetch } from '@/lib/api';
import { HandOver } from './ball';
import { EmailDraftDialog } from './email-draft-dialog';
import { FollowButton } from './follow-button';
import { PutOnMonday } from './put-on-monday';
import type { ItemType } from './updates-panel';

export type ActionBarProps = {
  /** A clean plain-text summary of the item: what Copy puts on the clipboard and Email starts with. */
  text: string;
  /** Who it is about, for the email subject. */
  subject: string;
  /** The item on the server (job, po, shipment, order or row): Copy then uses the server's clean text and Email makes a draft with attachments. */
  server?: { kind: string; ref: string };
  /** Opens a prefilled email with the right attachments; without it, Email opens the person's own mail app with the summary. */
  onEmail?: () => void;
  onDownload?: () => void;
  downloadLabel?: string;
  /** The thread this item's ball and comments belong to. Without it there is no Hand off. */
  thread?: { itemType: ItemType; itemRef: string };
  /** Anything else goes under the "…" button. */
  more?: { label: string; onClick: () => void }[];
  children?: ReactNode;
};

/**
 * The same five plain buttons on every job, order, purchase order and shipment: Email, Copy, Download, Hand off, Comment.
 * Copy puts a clean summary on the clipboard; Hand off passes the ball in one step; Comment jumps to the thread.
 */
export function ActionBar({
  text,
  subject,
  server,
  onEmail,
  onDownload,
  downloadLabel = 'Download',
  thread,
  more = [],
  children,
}: ActionBarProps) {
  const [handing, setHanding] = useState(false);
  const [emailing, setEmailing] = useState(false);
  const [putting, setPutting] = useState(false);
  // Anything with a server record can be put on a monday board (a preview first, saved as a change waiting for monday).
  const items = [...more, ...(server ? [{ label: 'Put on a monday board', onClick: () => setPutting(true) }] : [])];

  async function copy() {
    try {
      let out = text;
      if (server) {
        // The server's text hides costs from people who shouldn't see them; use it when it is there.
        const res = await authFetch(
          `/api/v1/actions/${encodeURIComponent(server.kind)}/${encodeURIComponent(server.ref)}/text?audience=internal`,
          { cache: 'no-store' },
        ).catch(() => null);
        const body = res?.ok ? ((await res.json().catch(() => null)) as { text?: string } | null) : null;
        if (body?.text) out = body.text;
      }
      await navigator.clipboard.writeText(out);
      toast.success('Copied', { description: 'Paste it into an email or a message.' });
    } catch {
      toast.error('Couldn’t copy it', { description: 'Your browser blocked the clipboard. Select the text and copy it yourself.' });
    }
  }
  function email() {
    if (onEmail) return onEmail();
    if (server) return setEmailing(true);
    window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(text)}`;
  }
  function comment() {
    const box = document.getElementById('item-thread');
    box?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.setTimeout(() => box?.querySelector<HTMLTextAreaElement>('textarea')?.focus(), 350);
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5" role="toolbar" aria-label="Actions">
      <Button size="sm" variant="outline" onClick={email}>
        <Mail /> Email
      </Button>
      <Button size="sm" variant="outline" onClick={() => void copy()}>
        <Copy /> Copy
      </Button>
      {onDownload && (
        <Button size="sm" variant="outline" onClick={onDownload}>
          <Download /> {downloadLabel}
        </Button>
      )}
      {thread && (
        <Button size="sm" variant="outline" onClick={() => setHanding(true)}>
          <Send /> Hand off
        </Button>
      )}
      {server && <FollowButton kind={server.kind} refId={server.ref} />}
      <Button size="sm" variant="outline" onClick={comment}>
        <MessageSquare /> Comment
      </Button>
      {items.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger
            className="grid size-7 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="More actions"
          >
            <MoreHorizontal className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {items.map((m) => (
              <DropdownMenuItem key={m.label} onClick={m.onClick}>
                {m.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      {children}
      {putting && server && <PutOnMonday kind={server.kind} refId={server.ref} title={subject} onClose={() => setPutting(false)} />}
      {emailing && server && (
        <EmailDraftDialog kind={server.kind} refId={server.ref} subject={subject} onClose={() => setEmailing(false)} />
      )}
      {handing && thread && <HandOver itemType={thread.itemType} itemRef={thread.itemRef} onClose={() => setHanding(false)} />}
    </div>
  );
}
