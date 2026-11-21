'use client';

import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Copy, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { authFetch } from '@/lib/api';
import { dateTime, money } from '@/lib/format';

type Prep = {
  title: string;
  next_steps?: { text: string }[];
  emails?: { at: string; from: string; subject: string; summary?: string }[];
  pos?: { po_number: string; vendor: string; status: string; total?: number | string; drawing_mismatch_lines?: number[] }[];
  shipments?: { reference: string; status: string; eta?: string | null; forwarder?: string | null }[];
  comments?: { author: string; at: string; body: string }[];
  text?: string;
};

function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid gap-1">
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      <ul className="grid gap-1">{children}</ul>
    </section>
  );
}

/** Everything to know before a call or meeting about a job: last emails, open purchase orders, dates, drawing revisions. For you only. */
export function PrepDialog({ kind, refId, onClose }: { kind: 'job' | 'customer' | 'vendor'; refId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['prep', kind, refId],
    queryFn: async (): Promise<Prep | null> => {
      const res = await authFetch(`/api/v1/prep/${kind}/${encodeURIComponent(refId)}`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Prep) : null;
    },
    retry: false,
  });
  const p = q.data;
  async function copy() {
    try {
      await navigator.clipboard.writeText(p?.text ?? '');
      toast.success('Copied');
    } catch {
      toast.error('Couldn’t copy it');
    }
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Before the meeting{p?.title ? `: ${p.title}` : ''}</DialogTitle>
          <DialogDescription>For you only. It has our prices and customer details, so it is not for forwarding.</DialogDescription>
        </DialogHeader>
        {q.isPending ? (
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        ) : !p ? (
          <p className="text-sm text-muted-foreground">This isn’t available yet.</p>
        ) : (
          <div className="grid gap-4 text-[13px]">
            {(p.next_steps ?? []).length > 0 && (
              <Part title="What is next">
                {p.next_steps!.map((s, i) => (
                  <li key={i}>{s.text}</li>
                ))}
              </Part>
            )}
            {(p.pos ?? []).length > 0 && (
              <Part title="Purchase orders">
                {p.pos!.map((o) => (
                  <li key={o.po_number}>
                    PO {o.po_number} to {o.vendor}: {o.status}
                    {o.total != null ? `, ${money(o.total)}` : ''}
                    {o.drawing_mismatch_lines?.length ? (
                      <span className="text-warn">
                        {' '}
                        · the vendor confirmed a different drawing revision on line {o.drawing_mismatch_lines.join(', ')}
                      </span>
                    ) : null}
                  </li>
                ))}
              </Part>
            )}
            {(p.shipments ?? []).length > 0 && (
              <Part title="Shipments">
                {p.shipments!.map((s) => (
                  <li key={s.reference}>
                    {s.reference}: {s.status}
                    {s.eta ? `, arrives ${s.eta}` : ''}
                    {s.forwarder ? ` (${s.forwarder})` : ''}
                  </li>
                ))}
              </Part>
            )}
            {(p.emails ?? []).length > 0 && (
              <Part title="Last emails">
                {p.emails!.map((m, i) => (
                  <li key={i}>
                    <span className="font-medium">{m.subject}</span>{' '}
                    <span className="text-muted-foreground">
                      ({m.from}, {dateTime(m.at)})
                    </span>
                    {m.summary ? <span className="block text-muted-foreground">{m.summary}</span> : null}
                  </li>
                ))}
              </Part>
            )}
            {(p.comments ?? []).length > 0 && (
              <Part title="Latest comments">
                {p.comments!.map((c, i) => (
                  <li key={i}>
                    <span className="font-medium">{c.author}:</span> {c.body}
                  </li>
                ))}
              </Part>
            )}
            {p.text && (
              <Button variant="outline" size="sm" className="justify-self-start" onClick={() => void copy()}>
                <Copy /> Copy all of this
              </Button>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
