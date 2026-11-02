'use client';

import { useMutation } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { decideAssistantAction } from '@/lib/opsApi';

/** What /chat/execute-action returns for a step that changes something: nothing has run yet. */
export type Proposal = { id: string; summary: string };
export type Outcome = { status: 'DONE' | 'FAILED' | 'CANCELLED'; text: string; data?: unknown };

type Decision = { status: Outcome['status']; result?: { done?: string; error?: string; data?: unknown } };

/** The server's proposal response, or null when the step ran straight away (a lookup). */
export function proposalOf(res: { status?: string; action?: { id?: string; summary?: string } }): Proposal | null {
  return res.status === 'needs_confirmation' && res.action?.id ? { id: res.action.id, summary: res.action.summary ?? '' } : null;
}

/** Cancels a proposal nobody confirmed, so it doesn't linger in the assistant's pending list. */
export const withdraw = (p: Proposal) => decideAssistantAction(p.id, false).catch(() => {});

/**
 * The server's own account of what a step will do, with Run it and Cancel.
 * Nothing changes until Run it.
 */
export function ProposalCard({ proposal, onOutcome }: { proposal: Proposal; onOutcome: (o: Outcome) => void }) {
  const decide = useMutation({
    mutationFn: async (confirm: boolean) => (await decideAssistantAction(proposal.id, confirm)) as Decision,
    onSuccess: (d) =>
      onOutcome({
        status: d.status,
        text: d.status === 'DONE' ? (d.result?.done ?? 'Done.') : d.status === 'FAILED' ? (d.result?.error ?? 'It didn’t work.') : 'Cancelled. Nothing was changed.',
        data: d.result?.data,
      }),
    onError: (e) => onOutcome({ status: 'FAILED', text: e.message }),
  });

  return (
    <div className="grid gap-3">
      <pre className="max-h-72 overflow-auto rounded-md border bg-muted/40 p-3 font-sans text-[13px] whitespace-pre-wrap">{proposal.summary}</pre>
      <p className="text-xs text-muted-foreground">Nothing has changed yet. Run it to go ahead.</p>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
          Cancel
        </Button>
        <Button disabled={decide.isPending} onClick={() => decide.mutate(true)}>
          {decide.isPending && decide.variables && <Loader2 className="animate-spin" />}
          Run it
        </Button>
      </div>
    </div>
  );
}
