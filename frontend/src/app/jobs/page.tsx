import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { JobsView } from '@/components/jobs/jobs-view';

export const metadata = { title: 'Jobs' };

export default function Page() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <JobsView />
    </Suspense>
  );
}
