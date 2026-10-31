import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { VendorsView } from '@/components/vendors/vendors-view';

export const metadata = { title: 'Vendors' };

export default function VendorsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <VendorsView />
    </Suspense>
  );
}
