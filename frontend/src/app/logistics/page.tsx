import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { LogisticsHome } from '@/components/logistics/logistics-home';

export const metadata = { title: 'Logistics' };

export default function Page() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <LogisticsHome />
      </Suspense>
    </>
  );
}
