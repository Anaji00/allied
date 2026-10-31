import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { WearView } from '@/components/sales/wear-view';

export const metadata = { title: 'Wear & quotes' };

export default function WearPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <WearView />
    </Suspense>
  );
}
