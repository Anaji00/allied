import { Suspense } from 'react';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { SystemView } from '@/components/admin/system-view';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'System' };

export default function SystemPage() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <SystemView />
      </Suspense>
    </>
  );
}
