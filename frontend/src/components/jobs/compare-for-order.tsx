'use client';

import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { openCompare } from '@/components/tools/compare-dialog';
import { CompareJobFiles } from './compare-job-files';
import { jobsCall, type Job, type JobRow } from './job-types';

/**
 * "Compare drawings" from a quote request, sales order or purchase order: finds the job this order belongs to, then opens the same
 * comparison as on the job page with its two newest drawings picked. With no job found, it offers files from the computer.
 */
export function CompareForOrder({ orderRef, jobRef, onClose }: { orderRef: string; jobRef?: string | null; onClose: () => void }) {
  const job = useQuery({
    queryKey: ['jobs', 'for-order', orderRef, jobRef ?? ''],
    queryFn: async (): Promise<Job | null> => {
      if (jobRef) return await jobsCall<Job>(`/${encodeURIComponent(jobRef)}`).catch(() => null);
      const list = await jobsCall<JobRow[] | { items?: JobRow[]; jobs?: JobRow[] }>(`?q=${encodeURIComponent(orderRef)}&limit=5`).catch(
        () => null,
      );
      const rows = Array.isArray(list) ? list : (list?.items ?? list?.jobs ?? []);
      const hit = rows.find((r) => r.ref === orderRef) ?? rows[0];
      return hit ? await jobsCall<Job>(`/${encodeURIComponent(hit.ref)}`).catch(() => null) : null;
    },
    retry: false,
    staleTime: 60_000,
  });
  if (job.data) return <CompareJobFiles job={job.data} onClose={onClose} />;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Compare drawings</DialogTitle>
          <DialogDescription>
            {job.isPending ? 'Looking for the job this order belongs to…' : `No job with drawings was found for ${orderRef}.`}
          </DialogDescription>
        </DialogHeader>
        {job.isPending ? (
          <Loader2 className="animate-spin text-muted-foreground" />
        ) : (
          <Button variant="outline" size="sm" className="justify-self-start" onClick={() => (onClose(), openCompare())}>
            Compare files from my computer
          </Button>
        )}
      </DialogContent>
    </Dialog>
  );
}
