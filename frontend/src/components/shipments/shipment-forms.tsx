'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { api, ok } from '@/lib/api/client';
import { day } from '@/lib/format';
import { identifier, statusLabel, statusOptions, type Shipment } from './use-shipments';

const EMPTY = {
  forwarder: '',
  mode: 'SEA',
  reference: '',
  container_no: '',
  awb: '',
  carrier: '',
  origin: '',
  destination: '',
  etd: '',
  eta: '',
  free_time_ends: '',
  status: 'BOOKED',
  owner_email: '',
};

export function AddShipmentDialog({
  open,
  onOpenChange,
  statuses,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  statuses: string[];
  onAdded: (id: number) => void;
}) {
  const { employeeEmail } = useAuth();
  const client = useQueryClient();
  const [form, setForm] = useState({ ...EMPTY, owner_email: employeeEmail ?? '' });
  const set = (key: keyof typeof EMPTY) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const numbered = Boolean(form.container_no.trim() || form.awb.trim() || form.reference.trim());

  const add = useMutation({
    mutationFn: async () => {
      const body = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() === '' ? null : v.trim()]));
      return (await ok(api.POST('/api/v1/shipments', { body: body as never }))) as Shipment;
    },
    onSuccess: (created) => {
      toast.success(`Added ${identifier(created)}`);
      void client.invalidateQueries({ queryKey: ['shipments'] });
      setForm({ ...EMPTY, owner_email: employeeEmail ?? '' });
      onOpenChange(false);
      onAdded(created.id);
    },
    onError: (e) => toast.error('The shipment wasn’t added', { description: e.message }),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a shipment</DialogTitle>
          <DialogDescription>For goods booked with a forwarder. A container, air waybill or booking number is enough; dates can come later.</DialogDescription>
        </DialogHeader>
        <form
          id="add-shipment"
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (numbered) add.mutate();
          }}
        >
          <Field label="Forwarder">
            <Input value={form.forwarder} onChange={set('forwarder')} />
          </Field>
          <Field label="How it travels">
            <NativeSelect value={form.mode} onChange={set('mode')}>
              <option value="SEA">Sea</option>
              <option value="AIR">Air</option>
              <option value="TRUCK">Truck</option>
            </NativeSelect>
          </Field>
          {form.mode === 'AIR' ? (
            <Field label="Air waybill">
              <Input value={form.awb} onChange={set('awb')} placeholder="176-12345675" />
            </Field>
          ) : (
            <Field label="Container number">
              <Input value={form.container_no} onChange={set('container_no')} placeholder="MSCU1234567" />
            </Field>
          )}
          <Field label="Booking reference">
            <Input value={form.reference} onChange={set('reference')} />
          </Field>
          <Field label="From">
            <Input value={form.origin} onChange={set('origin')} placeholder="Shanghai" />
          </Field>
          <Field label="To">
            <Input value={form.destination} onChange={set('destination')} placeholder="Vancouver" />
          </Field>
          <Field label="Leaves">
            <Input type="date" value={form.etd} onChange={set('etd')} />
          </Field>
          <Field label="Arrives">
            <Input type="date" value={form.eta} onChange={set('eta')} />
          </Field>
          <Field label="Port free time ends">
            <Input type="date" value={form.free_time_ends} onChange={set('free_time_ends')} />
          </Field>
          <Field label="Status">
            <NativeSelect value={form.status} onChange={set('status')}>
              {statusOptions(statuses.length ? statuses : ['BOOKED']).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Carrier">
            <Input value={form.carrier} onChange={set('carrier')} placeholder="Shipping line or airline" />
          </Field>
          <Field label="Owner" hint="Gets the alerts for this shipment.">
            <Input type="email" value={form.owner_email} onChange={set('owner_email')} />
          </Field>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="add-shipment" disabled={!numbered || add.isPending}>
            {add.isPending && <Loader2 className="animate-spin" />}
            Add shipment
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type Reading = {
  summary?: string;
  status?: string;
  references?: string[];
  etd?: string | null;
  eta?: string | null;
  last_free_day?: string | null;
  confidence?: number | null;
};
type EmailResult = { applied: boolean; reason?: string; shipment?: Shipment; reading?: Reading | null };

export function ForwarderEmailDialog({
  open,
  onOpenChange,
  onApplied,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApplied: (id: number) => void;
}) {
  const client = useQueryClient();
  const [mail, setMail] = useState({ sender: '', subject: '', body: '' });

  const read = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/shipments/forwarder-email', { body: mail }))) as EmailResult,
    onSuccess: (res) => {
      void client.invalidateQueries({ queryKey: ['shipments'] });
      if (res.applied && res.shipment) {
        toast.success(`Updated ${identifier(res.shipment)}`, {
          description: `Now “${statusLabel(res.shipment.status, res.shipment.mode)}”, arriving ${day(res.shipment.eta_current)}.`,
        });
        setMail({ sender: '', subject: '', body: '' });
        read.reset();
        onOpenChange(false);
        onApplied(res.shipment.id);
      }
    },
    onError: (e) => toast.error('Couldn’t read the email', { description: e.message }),
  });
  const result = read.data && !read.data.applied ? read.data : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Read a forwarder email</DialogTitle>
          <DialogDescription>
            Paste the email. The AI reads the status, dates and reference numbers and updates the matching shipment. It can take
            up to a minute.
          </DialogDescription>
        </DialogHeader>
        <form
          id="forwarder-email"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (mail.body.trim()) read.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="From">
              <Input value={mail.sender} onChange={(e) => setMail({ ...mail, sender: e.target.value })} placeholder="ops@forwarder.com" />
            </Field>
            <Field label="Subject">
              <Input value={mail.subject} onChange={(e) => setMail({ ...mail, subject: e.target.value })} />
            </Field>
          </div>
          <Field label="Message">
            <Textarea rows={7} value={mail.body} onChange={(e) => setMail({ ...mail, body: e.target.value })} required />
          </Field>
        </form>

        {result && (
          <div role="status" className="grid gap-2 rounded-md bg-warn-soft p-3 text-sm">
            <p className="font-medium text-warn">Nothing was changed</p>
            <p>
              {result.reason === 'no matching shipment'
                ? 'No shipment matched the numbers in this email. Add the shipment first, or check the reference.'
                : `The email wasn’t clear enough to apply on its own (${result.reason}).`}{' '}
              Leadership has been alerted to look at it.
            </p>
            {result.reading && (
              <Facts
                items={[
                  ['Reads as', result.reading.summary || '–'],
                  ['Status', statusLabel(result.reading.status)],
                  ['References', result.reading.references?.join(', ') || '–'],
                  ['Arrives', day(result.reading.eta)],
                  ['How sure', result.reading.confidence != null ? `${Math.round(result.reading.confidence * 100)}%` : '–'],
                ]}
              />
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button type="submit" form="forwarder-email" disabled={!mail.body.trim() || read.isPending}>
            {read.isPending && <Loader2 className="animate-spin" />}
            {read.isPending ? 'Reading…' : 'Read email'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
