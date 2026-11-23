import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { LogisticsHome } from '@/components/logistics/logistics-home';

export const metadata = { title: 'Freight and shipping' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <LogisticsHome />
      </Suspense>
    </>
  );
}
