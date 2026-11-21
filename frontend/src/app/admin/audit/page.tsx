import { Suspense } from 'react';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { AuditView } from '@/components/admin/audit-view';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Audit trail' };

export default function AuditPage() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={8} />}>
        <AuditView />
      </Suspense>
    </>
  );
}
