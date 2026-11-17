'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Truck } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import type { Job } from './job-types';

/**
 * Adds a shipment that belongs to this job. The shipment is tied to the job by its reference, "PO 9262",
 * so it shows up on the job's page and the purchase order is the one it belongs to.
 */
export function AddJobShipment({ job }: { job: Job }) {
  const client = useQueryClient();
  const numbered = (job.purchase_orders ?? []).filter((p) => p.po_number);
  const [open, setOpen] = useState(false);
  const [po, setPo] = useState('');
  const [mode, setMode] = useState('SEA');
  const [forwarder, setForwarder] = useState('');
  const [container, setContainer] = useState('');
  const [eta, setEta] = useState('');
  const chosen = po || numbered[0]?.po_number || '';

  const add = useMutation({
    mutationFn: async () => {
      const body = {
        mode,
        reference: `PO ${chosen}`,
        forwarder: forwarder.trim() || null,
        container_no: mode === 'SEA' ? container.trim() || null : null,
        awb: mode === 'AIR' ? container.trim() || null : null,
        eta: eta || null,
        status: 'BOOKED',
      };
      const res = await authFetch('/api/v1/shipments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res
          .json()
          .then((j) => j?.detail)
          .catch(() => null);
        throw new Error(typeof detail === 'string' ? detail : `The shipment wasn’t added (${res.status}).`);
      }
    },
    onSuccess: () => {
      toast.success('Shipment added to this job');
      void client.invalidateQueries({ queryKey: ['jobs'] });
      void client.invalidateQueries({ queryKey: ['shipments'] });
      setOpen(false);
    },
    onError: (e) => toast.error('The shipment wasn’t added', { description: e.message }),
  });

  if (numbered.length === 0)
    return <p className="text-xs text-muted-foreground">A shipment can be added once a purchase order has its number.</p>;

  return (
    <>
      <Button size="xs" variant="outline" className="justify-self-start" onClick={() => setOpen(true)}>
        <Truck /> Add a shipment
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add a shipment for this job</DialogTitle>
            <DialogDescription>It is tied to the purchase order you pick, so it shows on this job.</DialogDescription>
          </DialogHeader>
          <form
            id="job-shipment"
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              add.mutate();
            }}
          >
            <Field label="Which purchase order">
              <NativeSelect value={chosen} onChange={(e) => setPo(e.target.value)}>
                {numbered.map((p) => (
                  <option key={p.id} value={p.po_number}>
                    PO {p.po_number} · {p.vendor}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="How it travels">
              <NativeSelect value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="SEA">Sea</option>
                <option value="AIR">Air</option>
                <option value="TRUCK">Truck</option>
              </NativeSelect>
            </Field>
            <Field label="Forwarder (optional)">
              <Input value={forwarder} onChange={(e) => setForwarder(e.target.value)} />
            </Field>
            {mode !== 'TRUCK' && (
              <Field label={mode === 'SEA' ? 'Container number (optional)' : 'Air waybill (optional)'}>
                <Input value={container} onChange={(e) => setContainer(e.target.value)} />
              </Field>
            )}
            <Field label="Expected to arrive (optional)">
              <Input type="date" value={eta} onChange={(e) => setEta(e.target.value)} />
            </Field>
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="job-shipment" disabled={add.isPending || !chosen}>
              {add.isPending && <Loader2 className="animate-spin" />}
              Add it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
