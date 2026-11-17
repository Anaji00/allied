import { Suspense } from 'react';
import { CarriersPage } from '@/components/admin/carriers-page';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Carrier prices' };

export default function Page() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <CarriersPage />
    </Suspense>
  );
}
