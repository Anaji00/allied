import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { ShipmentsView } from '@/components/shipments/shipments-view';

export const metadata = { title: 'Shipments' };

export default function ShipmentsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <ShipmentsView />
    </Suspense>
  );
}
