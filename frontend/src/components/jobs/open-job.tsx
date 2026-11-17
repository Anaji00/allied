'use client';

import { useRouter } from 'next/navigation';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Route } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { jobsCall, type Job } from './job-types';

/** On a quote request: go to its job (made the first time), where the whole path to the shipment is. Hidden until jobs are switched on. */
export function OpenJob({ rfqId }: { rfqId: number }) {
  const router = useRouter();
  const { hasRole } = useAuth();
  // Jobs being switched on is learned from the list route, so nothing is shown (or created) before then.
  const available = useQuery({
    queryKey: ['jobs', 'available'],
    queryFn: async () => (await jobsCall<unknown>('?limit=1')) !== null,
    retry: false,
    staleTime: 5 * 60_000,
  });
  const open = useMutation({
    mutationFn: () => jobsCall<Job>(`/from-rfq/${rfqId}`, 'POST'),
    onSuccess: (job) => job && router.push(`/jobs/${encodeURIComponent(job.ref)}`),
    onError: (e) => toast.error('The job couldn’t be opened', { description: e.message }),
  });
  if (!available.data || !hasRole('OPERATOR')) return null;
  return (
    <Button variant="outline" size="sm" className="justify-self-start" disabled={open.isPending} onClick={() => open.mutate()}>
      {open.isPending ? <Loader2 className="animate-spin" /> : <Route />} Open the job
    </Button>
  );
}
