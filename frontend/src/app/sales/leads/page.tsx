import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { LeadsView } from '@/components/sales/leads-view';

export const metadata = { title: 'Leads' };

export default function LeadsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <LeadsView />
    </Suspense>
  );
}
