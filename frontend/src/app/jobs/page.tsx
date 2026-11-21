import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { JobsEntry } from '@/components/jobs/jobs-entry';

export const metadata = { title: 'Jobs' };

export default function Page() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <JobsEntry />
    </Suspense>
  );
}
