import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { ChaseView } from '@/components/today/chase-view';

export const metadata = { title: 'Who owes us' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <ChaseView />
      </Suspense>
    </>
  );
}
