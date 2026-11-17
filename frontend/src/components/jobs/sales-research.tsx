'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, Globe, Loader2, Plus, Trash2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { since } from '@/lib/format';
import { LinkOrder } from './link-order';
import { downloadBlob, jobsCall, type Job } from './job-types';

type SoLine = { description: string; quantity: string; unit_price: string };

/** Allied's own copy of the order: made from the customer's reference, shown to staff with its sheet; customers only ever see their reference. */
export function SalesOrderCard({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const so = job.sales_order;
  const sheet = useMutation({
    mutationFn: () =>
      downloadBlob(
        `/api/v1/jobs/${encodeURIComponent(job.ref)}/sales-order/sheet.pdf`,
        `sales-order-${job.sales_order?.customer_reference || job.ref}.pdf`,
      ),
    onError: (e) => toast.error('Couldn’t download the sheet', { description: e.message }),
  });

  return (
    <section className="grid content-start gap-2 rounded-lg border bg-card p-4">
      <h3 className="text-xs font-medium text-muted-foreground">Sales order (Allied’s own copy)</h3>
      {so ? (
        <>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-[13px]">
            {so.number && (
              <>
                <dt className="text-muted-foreground">Our number</dt>
                <dd className="font-mono text-xs">{so.number}</dd>
              </>
            )}
            <dt className="text-muted-foreground">Customer’s reference</dt>
            <dd className="font-mono text-xs">{so.customer_reference || '–'}</dd>
          </dl>
          {hasRole('OPERATOR') && (
            <Button size="xs" variant="outline" className="justify-self-start" disabled={sheet.isPending} onClick={() => sheet.mutate()}>
              {sheet.isPending ? <Loader2 className="animate-spin" /> : <Download />} Information sheet (PDF)
            </Button>
          )}
        </>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Made once the customer’s order is in.</p>
          {hasRole('MANAGER') && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
                Make the sales order
              </Button>
              <LinkOrder job={job} />
            </div>
          )}
        </>
      )}
      {open && (
        <MakeSalesOrder
          job={job}
          onClose={() => setOpen(false)}
          onMade={() => {
            void client.invalidateQueries({ queryKey: ['jobs'] });
            setOpen(false);
          }}
        />
      )}
    </section>
  );
}

/** A part name that offers the catalog's closest matches as you type; the order only accepts parts that are in it. */
function PartInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [focused, setFocused] = useState(false);
  const [later, setLater] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setLater(value), 250);
    return () => clearTimeout(t);
  }, [value]);
  const found = useQuery({
    queryKey: ['catalog', later],
    queryFn: async (): Promise<{ item_id: string; description: string }[]> => {
      const res = await authFetch(`/api/v1/catalog/items?q=${encodeURIComponent(later)}&limit=6`, { cache: 'no-store' });
      return res.ok ? ((await res.json()) as { item_id: string; description: string }[]) : [];
    },
    enabled: focused && later.trim().length >= 2,
    staleTime: 60_000,
    retry: false,
  });
  const options = (found.data ?? []).filter((o) => o.item_id !== value);
  return (
    <div className="relative">
      <Input
        aria-label="What"
        placeholder="Part number or name"
        value={value}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 150)}
      />
      {focused && options.length > 0 && (
        <ul
          role="listbox"
          aria-label="Matching parts"
          className="absolute z-10 mt-1 max-h-56 w-full min-w-64 overflow-auto rounded-md border bg-popover p-1 text-xs shadow-md"
        >
          {options.map((o) => (
            <li key={o.item_id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                className="grid w-full gap-0.5 rounded px-2 py-1.5 text-left hover:bg-accent"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(o.item_id);
                  setFocused(false);
                }}
              >
                <span className="font-mono">{o.item_id}</span>
                <span className="truncate text-muted-foreground">{o.description}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MakeSalesOrder({ job, onClose, onMade }: { job: Job; onClose: () => void; onMade: () => void }) {
  const [reference, setReference] = useState(job.customer_reference ?? '');
  const [address, setAddress] = useState('');
  const [lines, setLines] = useState<SoLine[]>([{ description: '', quantity: '1', unit_price: '' }]);
  const setLine = (i: number, patch: Partial<SoLine>) => setLines((cur) => cur.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const make = useMutation({
    mutationFn: () =>
      jobsCall(`/${encodeURIComponent(job.ref)}/sales-order`, 'POST', {
        customer_reference: reference.trim(),
        shipping_address: address.trim(),
        lines: lines
          .filter((l) => l.description.trim())
          .map((l) => ({ description: l.description.trim(), quantity: Number(l.quantity) || 0, unit_price: Number(l.unit_price) || 0 })),
      }),
    onSuccess: () => {
      toast.success('Sales order made');
      onMade();
    },
    onError: (e) => toast.error('The sales order wasn’t made', { description: e.message }),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Make the sales order</DialogTitle>
          <DialogDescription>
            This is Allied’s own copy. The customer only ever sees their own reference, never our number.
          </DialogDescription>
        </DialogHeader>
        <form
          id="so-make"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            make.mutate();
          }}
        >
          <Field label="Customer’s PO or reference">
            <Input required value={reference} onChange={(e) => setReference(e.target.value)} />
          </Field>
          <Field label="Ship to">
            <Textarea required rows={3} value={address} onChange={(e) => setAddress(e.target.value)} />
          </Field>
          <div className="grid gap-2">
            <p className="text-sm font-medium">What the customer ordered</p>
            {lines.map((l, i) => (
              <div key={i} className="grid grid-cols-[4.5rem_minmax(0,1fr)_6.5rem_auto] items-center gap-2">
                <Input
                  aria-label="Quantity"
                  inputMode="decimal"
                  value={l.quantity}
                  onChange={(e) => setLine(i, { quantity: e.target.value.replace(/[^\d.]/g, '') })}
                />
                <PartInput value={l.description} onChange={(v) => setLine(i, { description: v })} />
                <Input
                  aria-label="Price each"
                  inputMode="decimal"
                  placeholder="Price each"
                  value={l.unit_price}
                  onChange={(e) => setLine(i, { unit_price: e.target.value.replace(/[^\d.]/g, '') })}
                />
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label="Remove this line"
                  onClick={() => setLines((cur) => cur.filter((_, j) => j !== i))}
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="justify-self-start"
              onClick={() => setLines((cur) => [...cur, { description: '', quantity: '1', unit_price: '' }])}
            >
              <Plus /> Add a line
            </Button>
          </div>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="so-make"
            disabled={make.isPending || !reference.trim() || !address.trim() || !lines.some((l) => l.description.trim())}
          >
            {make.isPending && <Loader2 className="animate-spin" />}
            Make it
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** What the team found out about the part, from sites or by hand. Search is free and saves the top few results as notes. */
export function ResearchCard({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [query, setQuery] = useState('');
  const [note, setNote] = useState({ title: '', url: '', note: '' });
  const [adding, setAdding] = useState(false);
  const items = job.research ?? [];
  const done = () => void client.invalidateQueries({ queryKey: ['jobs'] });
  const search = useMutation({
    mutationFn: () => jobsCall<Job>(`/${encodeURIComponent(job.ref)}/research/search`, 'POST', { query: query.trim() }),
    onSuccess: (updated) => {
      // The server answers OK even when the search finds nothing, so count what was actually saved.
      const saved = (updated?.research?.length ?? items.length) - items.length;
      if (saved > 0) {
        toast.success(`Saved ${saved} ${saved === 1 ? 'result' : 'results'} as notes`);
        setQuery('');
      } else {
        toast.warning('The search came back empty', { description: 'Nothing was saved. Try different words, or add a note by hand.' });
      }
      done();
    },
    onError: (e) => toast.error('The search didn’t run', { description: e.message }),
  });
  const addNote = useMutation({
    mutationFn: () =>
      jobsCall<unknown>(`/${encodeURIComponent(job.ref)}/research`, 'POST', {
        title: note.title.trim(),
        url: note.url.trim(),
        note: note.note.trim(),
      }),
    onSuccess: () => {
      setNote({ title: '', url: '', note: '' });
      setAdding(false);
      done();
    },
    onError: (e) => toast.error('The note wasn’t saved', { description: e.message }),
  });
  const canWrite = hasRole('OPERATOR');

  return (
    <section className="grid content-start gap-2 rounded-lg border bg-card p-4">
      <h3 className="text-xs font-medium text-muted-foreground">Research</h3>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing saved yet.{canWrite && ' Search the web, or add a note by hand.'}</p>
      ) : (
        <ul className="grid gap-1.5 text-[13px]">
          {items.map((r) => (
            <li key={r.id}>
              {r.url ? (
                <a href={r.url} target="_blank" rel="noreferrer noopener" className="font-medium underline">
                  {r.title}
                </a>
              ) : (
                <span className="font-medium">{r.title}</span>
              )}
              {r.note && <span className="text-muted-foreground"> · {r.note}</span>}
              <span className="text-xs text-muted-foreground">
                {' '}
                · {(r as { source?: string }).source === 'search' ? 'from a search' : 'added by hand'}
                {r.at ? ` ${since(r.at)}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
      {canWrite && (
        <>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (query.trim()) search.mutate();
            }}
          >
            <label className="grid gap-1 text-xs">
              <span className="font-medium">Search the web</span>
              <Input
                className="w-64"
                placeholder="For example: AR400 chute liner supplier"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <Button type="submit" size="sm" variant="outline" disabled={!query.trim() || search.isPending}>
              {search.isPending ? <Loader2 className="animate-spin" /> : <Globe />} Search
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setAdding((a) => !a)}>
              {adding ? 'Cancel' : 'Add a note'}
            </Button>
          </form>
          {adding && (
            <form
              className="grid gap-2 rounded-md border p-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (note.title.trim()) addNote.mutate();
              }}
            >
              <Input
                aria-label="Title"
                placeholder="Title"
                value={note.title}
                onChange={(e) => setNote({ ...note, title: e.target.value })}
              />
              <Input
                aria-label="Link"
                placeholder="Link (optional)"
                value={note.url}
                onChange={(e) => setNote({ ...note, url: e.target.value })}
              />
              <Textarea
                aria-label="Note"
                rows={2}
                placeholder="What you found"
                value={note.note}
                onChange={(e) => setNote({ ...note, note: e.target.value })}
              />
              <Button type="submit" size="sm" className="justify-self-start" disabled={!note.title.trim() || addNote.isPending}>
                {addNote.isPending && <Loader2 className="animate-spin" />}
                Save note
              </Button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
