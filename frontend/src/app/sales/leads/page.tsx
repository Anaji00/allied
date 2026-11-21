import { Suspense } from 'react';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { RowsSkeleton } from '@/components/data/states';
import { LeadsView } from '@/components/sales/leads-view';

export const metadata = { title: 'Leads' };

export default function LeadsPage() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <LeadsView />
      </Suspense>
    </>
  );
}
