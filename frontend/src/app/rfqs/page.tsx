import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { RfqsView } from '@/components/rfqs/rfqs-view';

export const metadata = { title: 'RFQs' };

export default function RfqsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <RfqsView />
    </Suspense>
  );
}
