'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type Kind = 'job' | 'product' | 'rfq' | 'order' | 'po' | 'shipment' | 'board_row';

// What each "+ New" choice does. Orders, quote requests and shipments already have their own forms: those open on their page.
const KINDS: { kind: Kind; label: string; go?: string; hint: string }[] = [
  { kind: 'job', label: 'Job', hint: 'A piece of work from request to shipment.' },
  { kind: 'rfq', label: 'Quote request', go: '/orders?tab=rfqs&new=true', hint: 'A customer asking for a price.' },
  { kind: 'order', label: 'Sales order', go: '/orders?new=true', hint: 'A customer order.' },
  { kind: 'po', label: 'Purchase order', hint: 'An order to a vendor.' },
  { kind: 'product', label: 'Product or part', hint: 'Something you buy or sell.' },
  { kind: 'shipment', label: 'Shipment', go: '/jobs?section=shipments&new=true', hint: 'Freight with a forwarder.' },
  { kind: 'board_row', label: 'Row on a board', hint: 'A new row, saved here and sent to monday when a manager sends it.' },
];

// The few things each kind needs. Everything else can be filled in afterwards on its own page.
type Field2 = { key: string; label: string; required?: boolean; long?: boolean; hint?: string };
const FIELDS: Record<string, Field2[]> = {
  job: [
    { key: 'customer', label: 'Customer *', required: true },
    { key: 'title', label: 'What is the job?' },
    { key: 'notes', label: 'Notes (optional)', long: true },
  ],
  product: [
    { key: 'part_number', label: 'Part number *', required: true },
    { key: 'description', label: 'Description (optional)' },
    { key: 'base_price', label: 'Selling price (optional)' },
    { key: 'preferred_vendor', label: 'Usual vendor (optional)' },
  ],
  po: [
    { key: 'job', label: 'Job number (for example JOB-2026-0001) *', required: true },
    { key: 'vendor', label: 'Vendor *', required: true },
    {
      key: 'items',
      label: 'What to order *',
      required: true,
      long: true,
      hint: 'One per line: quantity, what, cost each. For example: 2 x flex hose @ 14.50',
    },
  ],
  board_row: [{ key: 'name', label: 'Name of the row *', required: true }],
};

/** Turns "2 x flex hose @ 14.50" lines into the items the server wants. */
function parseItems(text: string) {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = /^(\d+(?:\.\d+)?)\s*(?:x|×)?\s*(.+?)(?:\s*@\s*\$?([\d,.]+))?$/i.exec(l);
      return m
        ? { quantity: Number(m[1]), description: m[2].trim(), unit_cost: m[3] ? Number(m[3].replace(/,/g, '')) : 0 }
        : { quantity: 1, description: l, unit_cost: 0 };
    });
}

/** The "+ New" button: add anything from one place. A dialog for what has no form of its own; the rest open the right page. */
export function NewMenu() {
  const { hasRole } = useAuth();
  if (!hasRole('OPERATOR')) return null;
  const open = (kind: Kind) => window.dispatchEvent(new CustomEvent('allied:new', { detail: { kind } }));
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="inline-flex h-8 items-center gap-1 rounded-lg bg-primary px-2.5 text-sm font-medium text-primary-foreground outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Add something new"
      >
        <Plus className="size-4" /> <span className="max-sm:sr-only">New</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => window.dispatchEvent(new Event('allied:compare'))}>
          <span className="grid">
            <span>Compare two drawings</span>
            <span className="text-xs text-muted-foreground">See what changed between revisions.</span>
          </span>
        </DropdownMenuItem>
        {KINDS.map((k) => (
          <DropdownMenuItem key={k.kind} onClick={() => open(k.kind)}>
            <span className="grid">
              <span>{k.label}</span>
              <span className="text-xs text-muted-foreground">{k.hint}</span>
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Listens for "+ New" and Ctrl+K choices and shows the quick form for that kind. Says so plainly if the server's create route is not there yet. */
export function QuickCreate() {
  const router = useRouter();
  const [kind, setKind] = useState<Kind | null>(null);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [board, setBoard] = useState('');
  const [busy, setBusy] = useState(false);
  const spec = KINDS.find((k) => k.kind === kind);
  const fields = (kind && FIELDS[kind]) || [];

  useEffect(() => {
    const on = (e: Event) => {
      const { kind: k, text } = (e as CustomEvent<{ kind: Kind; text?: string }>).detail;
      const target = KINDS.find((x) => x.kind === k);
      if (target?.go) router.push(target.go);
      else {
        // Words typed in the search box fill the first box of the form.
        const first = FIELDS[k]?.[0]?.key;
        setVals(text?.trim() && first ? { [first]: text.trim() } : {});
        setKind(k);
      }
    };
    window.addEventListener('allied:new', on);
    return () => window.removeEventListener('allied:new', on);
  }, [router]);

  const boards = useQuery({
    queryKey: ['boards', 'for-new'],
    queryFn: async (): Promise<{ board_id: string; name: string }[]> => {
      const res = await authFetch('/api/v1/boards', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as { board_id: string; name: string }[]) : [];
    },
    enabled: Boolean(kind),
    retry: false,
  });

  const close = () => {
    setKind(null);
    setVals({});
    setBoard('');
  };
  const ready = fields.every((f) => !f.required || (vals[f.key] ?? '').trim()) && (kind !== 'board_row' || Boolean(board));

  async function make() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      for (const f of fields) {
        const v = (vals[f.key] ?? '').trim();
        if (v) body[f.key] = f.key === 'items' ? parseItems(v) : v;
      }
      if (kind === 'board_row') body.board_id = board;
      else if (board) body.to_monday = { board_id: board };
      const res = await authFetch(`/api/v1/create/${kind}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const out = (await res.json().catch(() => null)) as { detail?: string; message?: string; link?: string; ref?: string } | null;
      if (res.status === 404 || res.status === 405) throw new Error('Adding this from here isn’t switched on yet.');
      if (!res.ok) throw new Error(typeof out?.detail === 'string' ? out.detail : `That didn’t work (${res.status}).`);
      toast.success(`${spec?.label} added${out?.ref ? `: ${out.ref}` : ''}`, {
        description: board ? 'It is waiting to be sent to monday.' : undefined,
      });
      const link = out?.link;
      close();
      if (link) router.push(link.startsWith('/') ? link : '/');
    } catch (e) {
      toast.error('It wasn’t added', { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={Boolean(kind)} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New {spec?.label.toLowerCase()}</DialogTitle>
          <DialogDescription>{spec?.hint}</DialogDescription>
        </DialogHeader>
        <form
          id="quick-create"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) void make();
          }}
        >
          {fields.map((f, i) => (
            <Field key={f.key} label={f.label} hint={f.hint}>
              {f.long ? (
                <Textarea rows={3} value={vals[f.key] ?? ''} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
              ) : (
                <Input autoFocus={i === 0} value={vals[f.key] ?? ''} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
              )}
            </Field>
          ))}
          {(boards.data ?? []).length > 0 && (
            <Field
              label={kind === 'board_row' ? 'Which board?' : 'Also put it on a monday board (optional)'}
              hint={kind === 'board_row' ? undefined : 'It is saved here first and goes to monday only when a manager sends it.'}
            >
              <NativeSelect value={board} onChange={(e) => setBoard(e.target.value)}>
                <option value="">{kind === 'board_row' ? 'Choose a board…' : 'Not on a board'}</option>
                {(boards.data ?? []).map((b) => (
                  <option key={b.board_id} value={b.board_id}>
                    {b.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          )}
        </form>
        <DialogFooter>
          {!ready && <span className="mr-auto text-xs text-muted-foreground">Fill in the starred parts first.</span>}
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="quick-create" disabled={!ready || busy}>
            {busy && <Loader2 className="animate-spin" />}
            Add it
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
