import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { AutomationsPage } from '@/components/admin/automations-page';
import { AdminTabs } from '@/components/shell/admin-tabs';

export const metadata = { title: 'Automations' };

export default function Page() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <AutomationsPage />
      </Suspense>
    </>
  );
}
