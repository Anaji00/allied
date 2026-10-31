import { Suspense } from 'react';
import { PeopleAlerts } from '@/components/admin/people-alerts';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'People & alerts' };

export default function PeopleAlertsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <PeopleAlerts />
    </Suspense>
  );
}
