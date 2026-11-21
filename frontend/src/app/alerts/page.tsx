import { Suspense } from 'react';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { PeopleAlerts } from '@/components/admin/people-alerts';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'People & alerts' };

export default function PeopleAlertsPage() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <PeopleAlerts />
      </Suspense>
    </>
  );
}
