'use client';

import Link from 'next/link';
import { Button, buttonVariants } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ToneBadge } from '@/components/data/tone-badge';

export type Draft = {
  source: 'rule' | 'ai' | 'template';
  rule?: string | null;
  to: string;
  subject: string;
  body: string;
  action_id?: string | number | null;
  note?: string | null;
};

const SOURCE: Record<Draft['source'], string> = {
  rule: 'Written from a rule',
  ai: 'Written by the AI helper',
  template: 'Plain template',
};

/** Shows the reply the app wrote. It is already waiting in Approvals; nothing has been sent. */
export function ReplyDialog({ draft, onClose }: { draft: Draft | null; onClose: () => void }) {
  return (
    <Dialog open={Boolean(draft)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        {draft && (
          <>
            <DialogHeader>
              <DialogTitle>Reply waiting for your OK</DialogTitle>
              <DialogDescription>Nothing has been sent. Open it in Approvals to edit it and send it.</DialogDescription>
            </DialogHeader>
            <div className="grid gap-2 text-[13px]">
              <div className="flex flex-wrap items-center gap-2">
                <ToneBadge tone={draft.source === 'ai' ? 'info' : 'neutral'}>{SOURCE[draft.source]}</ToneBadge>
                {draft.rule && <span className="text-xs text-muted-foreground">Rule: {draft.rule}</span>}
              </div>
              {draft.note && <p className="text-xs text-muted-foreground">{draft.note}</p>}
              <p>
                <span className="text-muted-foreground">To </span>
                {draft.to || 'Nobody yet'}
              </p>
              <p className="font-medium">{draft.subject}</p>
              <pre className="max-h-64 overflow-auto rounded-md bg-muted/50 p-3 font-sans text-[13px] whitespace-pre-wrap">
                {draft.body || 'No text yet. Open it in Drafts to write it.'}
              </pre>
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
              <Link href="/secretary/mail?filter=drafts" className={buttonVariants()}>
                See it in Drafts
              </Link>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
