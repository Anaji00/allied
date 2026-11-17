'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Link2, Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import { jobsCall, type Job } from './job-types';

type Duplicate = { vendor: string; order_po: string; job_po: string };
type Linked = Job & { duplicates?: Duplicate[] };

/**
 * Ties an order that was made somewhere else (an emailed customer PO, the New order screen) to this job. Purchase orders
 * on it that have not gone out take the job's number; any that already went out are listed, so the vendor can be told.
 */
export function LinkOrder({ job }: { job: Job }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [so, setSo] = useState('');
  const [dupes, setDupes] = useState<Duplicate[] | null>(null);
  const orders = useQuery({
    queryKey: ['orders', 'link-pick', job.customer],
    queryFn: async (): Promise<{ sales_order_number: string; customer_name?: string | null; customer_po_number?: string | null }[]> => {
      // This customer's orders first; when there are none (the name is written differently), the latest orders.
      const find = async (q: string) => {
        const res = await authFetch(`/api/v1/orders?q=${encodeURIComponent(q)}&view=all&limit=30&offset=0`, { cache: 'no-store' });
        return res.ok ? (((await res.json()) as { items?: never[] }).items ?? []) : [];
      };
      const mine = await find(job.customer ?? '');
      return mine.length ? mine : find('');
    },
    enabled: open,
    retry: false,
  });
  const link = useMutation({
    mutationFn: () => jobsCall<Linked>(`/${encodeURIComponent(job.ref)}/link-order`, 'POST', { sales_order_number: so.trim() }),
    onSuccess: (r) => {
      void client.invalidateQueries({ queryKey: ['jobs'] });
      void client.invalidateQueries({ queryKey: ['orders'] });
      setOpen(false);
      if (r?.duplicates?.length) setDupes(r.duplicates);
      else toast.success('Order linked to this job');
    },
    onError: (e) => toast.error('The order wasn’t linked', { description: e.message }),
  });

  return (
    <>
      <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setOpen(true)}>
        <Link2 /> Link an order
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Link an order to this job</DialogTitle>
            <DialogDescription>
              For an order made elsewhere. Its purchase orders that haven’t gone out take this job’s numbers.
            </DialogDescription>
          </DialogHeader>
          <form
            id="link-order"
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (so.trim()) link.mutate();
            }}
          >
            <Field label="Which order">
              {(orders.data?.length ?? 0) > 0 ? (
                <NativeSelect value={so} onChange={(e) => setSo(e.target.value)} required>
                  <option value="">Choose an order</option>
                  {orders.data!.map((o) => (
                    <option key={o.sales_order_number} value={o.sales_order_number}>
                      {o.sales_order_number} · {o.customer_po_number || 'no reference'} · {o.customer_name}
                    </option>
                  ))}
                </NativeSelect>
              ) : (
                <Input value={so} onChange={(e) => setSo(e.target.value)} placeholder="Sales order number" required />
              )}
            </Field>
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="link-order" disabled={!so.trim() || link.isPending}>
              {link.isPending && <Loader2 className="animate-spin" />}
              Link it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={dupes !== null} onOpenChange={(o) => !o && setDupes(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Order linked, with a duplicate to sort out</DialogTitle>
            <DialogDescription>
              These vendors already received a second purchase order, so they now have two for the same job.
            </DialogDescription>
          </DialogHeader>
          <ul className="grid gap-1.5 text-sm">
            {dupes?.map((d) => (
              <li key={`${d.vendor}-${d.order_po}`}>
                <span className="font-medium">{d.vendor}</span>: PO {d.order_po} on the order, PO {d.job_po} on the job. Tell them which one
                to use.
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button onClick={() => setDupes(null)}>Got it</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
