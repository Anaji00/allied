'use client';

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { authFetch } from '@/lib/api';

type Doc = {
  id: number;
  kind?: string;
  label?: string;
  pages?: string;
  fields?: Record<string, unknown>;
  warnings?: string[];
  confirmed?: boolean;
};
type Read = { packet: string; already_read?: boolean; documents: Doc[] };

const fieldWord = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/**
 * Upload a scanned packet (one PDF with several papers in it). The app splits it into the purchase order, drawings and the rest,
 * reads the main details off each, and files them on this job. The details are shown here to check.
 */
export function PacketRead({ jobRef, onClose }: { jobRef: string; onClose: () => void }) {
  const client = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [out, setOut] = useState<Read | null>(null);
  const read = useMutation({
    mutationFn: async (pdf: File): Promise<Read> => {
      const body = new FormData();
      body.append('file', pdf, pdf.name);
      const res = await authFetch(`/api/v1/jobs/${encodeURIComponent(jobRef)}/packets`, { method: 'POST', body });
      const json = (await res.json().catch(() => null)) as (Read & { detail?: string }) | null;
      if (!res.ok || !json) throw new Error(typeof json?.detail === 'string' ? json.detail : `That didn’t work (${res.status}).`);
      return json;
    },
    onSuccess: (r) => {
      setOut(r);
      void client.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: (e) => toast.error('The packet wasn’t read', { description: e.message }),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Read a scanned packet</DialogTitle>
          <DialogDescription>
            Choose one PDF with the papers scanned together. The app sorts them and reads the details for you to check.
          </DialogDescription>
        </DialogHeader>
        <input
          ref={file}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) read.mutate(f);
            e.target.value = '';
          }}
        />
        {!out ? (
          <Button variant="outline" className="justify-self-start" disabled={read.isPending} onClick={() => file.current?.click()}>
            {read.isPending ? <Loader2 className="animate-spin" /> : <Upload />} Choose the PDF
          </Button>
        ) : (
          <div className="grid gap-3 text-[13px]">
            {out.already_read && <p className="text-muted-foreground">This packet was read before; nothing new was added.</p>}
            {out.documents.length === 0 && <p>No papers were found in it.</p>}
            {out.documents.map((d) => (
              <div key={d.id} className="grid gap-1 rounded-md border p-3">
                <p className="font-medium">
                  {d.label ?? d.kind}
                  {d.pages ? <span className="font-normal text-muted-foreground"> · pages {d.pages}</span> : null}
                </p>
                {Object.entries(d.fields ?? {}).length > 0 && (
                  <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3">
                    {Object.entries(d.fields ?? {}).map(([k, v]) => (
                      <div key={k} className="contents">
                        <dt className="text-muted-foreground">{fieldWord(k)}</dt>
                        <dd>{String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                {(d.warnings ?? []).map((w, i) => (
                  <p key={i} className="rounded bg-warn-soft px-2 py-1 text-warn">
                    {w}
                  </p>
                ))}
              </div>
            ))}
            <Button className="justify-self-end" onClick={onClose}>
              Done
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
