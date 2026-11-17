'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { VendorInput } from './vendor-pick';
import { jobsCall, type Job } from './job-types';

/**
 * "The vendor's price is in": records the vendor's quotation on the job, which makes that vendor's purchase order
 * a draft and tells the managers. Only what is filled in is kept; a blank stays blank.
 */
export function VendorQuoteButton({ job }: { job: Job }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    vendor: '',
    quote_order_number: '',
    date: '',
    total: '',
    lead_time: '',
    valid_until: '',
    currency: '',
  });
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { vendor: form.vendor.trim() };
      if (form.quote_order_number.trim()) body.quote_order_number = form.quote_order_number.trim();
      if (form.date) body.date = form.date;
      if (form.total !== '') body.total = Number(form.total);
      if (form.lead_time.trim()) body.lead_time = form.lead_time.trim();
      if (form.valid_until) body.valid_until = form.valid_until;
      if (form.currency.trim()) body.currency = form.currency.trim().toUpperCase();
      return jobsCall(`/${encodeURIComponent(job.ref)}/vendor-quotes`, 'POST', body);
    },
    onSuccess: (r) => {
      const warnings = (r as { warnings?: string[] } | null)?.warnings ?? [];
      toast.success('Vendor price recorded', { description: 'That vendor’s purchase order is now a draft, and the managers were told.' });
      warnings.forEach((w) => toast.warning(w));
      void client.invalidateQueries({ queryKey: ['jobs'] });
      setOpen(false);
      setForm({ vendor: '', quote_order_number: '', date: '', total: '', lead_time: '', valid_until: '', currency: '' });
    },
    onError: (e) => toast.error('It wasn’t recorded', { description: e.message }),
  });
  if (!hasRole('OPERATOR')) return null;
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Vendor price is in
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>The vendor’s price</DialogTitle>
            <DialogDescription>Fill in what is on their quotation. Leave the rest blank.</DialogDescription>
          </DialogHeader>
          <form
            id="vendor-quote"
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (form.vendor.trim()) save.mutate();
            }}
          >
            <Field label="Vendor (required)">
              <VendorInput value={form.vendor} onChange={set('vendor')} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Their quote number">
                <Input value={form.quote_order_number} onChange={(e) => set('quote_order_number')(e.target.value)} />
              </Field>
              <Field label="Date">
                <Input type="date" value={form.date} onChange={(e) => set('date')(e.target.value)} />
              </Field>
              <Field label="Total">
                <Input inputMode="decimal" value={form.total} onChange={(e) => set('total')(e.target.value.replace(/[^\d.]/g, ''))} />
              </Field>
              <Field label="Price good until">
                <Input type="date" value={form.valid_until} onChange={(e) => set('valid_until')(e.target.value)} />
              </Field>
              <Field label="Currency (if not USD)">
                <Input value={form.currency} maxLength={3} onChange={(e) => set('currency')(e.target.value)} placeholder="e.g. CNY" />
              </Field>
              <Field label="Lead time">
                <Input value={form.lead_time} onChange={(e) => set('lead_time')(e.target.value)} placeholder="e.g. 4 weeks" />
              </Field>
            </div>
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="vendor-quote" disabled={!form.vendor.trim() || save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />}
              Record it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
