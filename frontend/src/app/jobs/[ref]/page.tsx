import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { JobPage } from '@/components/jobs/job-page';

export const metadata = { title: 'Job' };

export default function Page() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <JobPage />
    </Suspense>
  );
}
