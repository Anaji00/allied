import { Suspense } from 'react';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { RowsSkeleton } from '@/components/data/states';
import { WearView } from '@/components/sales/wear-view';

export const metadata = { title: 'Wear & quotes' };

export default function WearPage() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <WearView />
      </Suspense>
    </>
  );
}
