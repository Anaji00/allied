'use client';

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Field = {
  value: unknown;
  confidence?: number;
  source?: { page?: number; text?: string } | null;
  method?: 'rule' | 'ai' | 'master data';
};
export type Extraction = {
  extraction_id?: string;
  fields: Record<string, Field>;
  warnings?: string[];
  needs_check?: string[];
  ai_used?: boolean;
};

/** Plain chips, not numbers: how sure the app is, and where the value came from. Blank stays blank. */
function chip(f: Field) {
  if (f.method === 'ai' || (f.confidence ?? 1) < 0.6) return { word: 'Best guess', cls: 'bg-warn-soft text-warn' };
  return { word: 'Found', cls: 'bg-ok-soft text-ok' };
}

/**
 * Paste an email or some text; the app reads it and fills the form with what it finds. Each field says whether it was found or is a
 * best guess, shows the line it came from, and can be fixed. Money, dates and numbers are always flagged to check. Nothing is saved
 * here: the person presses "Use these" and then saves the form as usual.
 */
export function PasteToFill({
  kind,
  labels,
  onUse,
  onClose,
}: {
  /** The extract route: rfq-from-email, order-from-email, vendor-quote, call-to-job-note. */
  kind: 'rfq-from-email' | 'order-from-email' | 'vendor-quote' | 'call-to-job-note';
  /** Plain names for the fields this form has. Fields not listed here are ignored. */
  labels: Record<string, string>;
  onUse: (values: Record<string, string>, extractionId?: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const [out, setOut] = useState<Extraction | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});

  const read = useMutation({
    mutationFn: async () => {
      // The vendor-quote route takes a form (it can also take a PDF); the others take JSON.
      const form = kind === 'vendor-quote';
      const data = new FormData();
      data.append('text', text);
      const res = await authFetch(`/api/v1/extract/${kind}`, {
        method: 'POST',
        ...(form ? { body: data } : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) }),
      });
      const body = (await res.json().catch(() => null)) as (Extraction & { detail?: string }) | null;
      if (res.status === 404) throw new Error('This isn’t switched on yet. Tell your administrator.');
      if (res.status === 429) throw new Error('The AI is busy. Try again in a minute.');
      if (!res.ok || !body) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
      return body;
    },
    onSuccess: (b) => {
      setOut(b);
      setEdits({});
    },
    onError: (e) => toast.error('It could not be read', { description: e.message }),
  });

  const names = Object.keys(labels);
  // Lines come back as a list of {quantity, description, unit_price}; they are shown, and used, as one line each ("2 x hose @ 14.50").
  const show = (v: unknown): string =>
    Array.isArray(v)
      ? v
          .map((x) => {
            const o = x as { quantity?: unknown; description?: unknown; unit_price?: unknown };
            return o && typeof o === 'object'
              ? `${o.quantity ?? ''} x ${o.description ?? ''}${o.unit_price != null ? ` @ ${o.unit_price}` : ''}`.trim()
              : String(x);
          })
          .join('\n')
      : v == null
        ? ''
        : String(v);
  const value = (k: string) => edits[k] ?? show(out?.fields[k]?.value);
  // An address the app picked out of pasted text can be the wrong one (our own, or a signature's), so it is always to be checked.
  const check = new Set([...(out?.needs_check ?? []), ...(out?.fields.contact_email ? ['contact_email'] : [])]);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Fill this in from text</DialogTitle>
          <DialogDescription>Paste the email or text. You check what it found before anything is saved.</DialogDescription>
        </DialogHeader>
        {!out ? (
          <div className="grid gap-3">
            <Textarea
              rows={8}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Paste the email or text here"
              autoFocus
            />
          </div>
        ) : (
          <div className="grid gap-3 text-[13px]">
            {out.ai_used && <p className="text-xs text-muted-foreground">The AI helped read this. Check it.</p>}
            {(out.warnings ?? []).map((w, i) => (
              <p key={i} className="rounded-md bg-warn-soft px-3 py-2 text-warn">
                {w}
              </p>
            ))}
            <ul className="grid gap-2">
              {names.map((k) => {
                const f = out.fields[k];
                return (
                  <li key={k} className="grid gap-0.5">
                    <label className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      {labels[k]}
                      {f ? (
                        <span className={cn('rounded-full px-1.5 py-0.5 text-[11px]', chip(f).cls)} title={f.source?.text ?? undefined}>
                          {chip(f).word}
                        </span>
                      ) : (
                        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[11px]">Not found</span>
                      )}
                      {check.has(k) && <span className="text-warn">Check this one</span>}
                    </label>
                    {value(k).includes('\n') ? (
                      <Textarea
                        rows={Math.min(6, value(k).split('\n').length + 1)}
                        value={value(k)}
                        onChange={(e) => setEdits({ ...edits, [k]: e.target.value })}
                      />
                    ) : (
                      <Input value={value(k)} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} className="h-8" />
                    )}
                    {f?.source?.text && <span className="line-clamp-1 text-[11px] text-muted-foreground">From: “{f.source.text}”</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          {!out ? (
            <Button disabled={!text.trim() || read.isPending} onClick={() => read.mutate()}>
              {read.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Read it
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => setOut(null)}>
                Paste again
              </Button>
              <Button
                onClick={() => {
                  onUse(Object.fromEntries(names.map((k) => [k, value(k)]).filter(([, v]) => v !== '')), out.extraction_id);
                  onClose();
                }}
              >
                Use these
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
