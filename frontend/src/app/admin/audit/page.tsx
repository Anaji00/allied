import { Suspense } from 'react';
import { AuditView } from '@/components/admin/audit-view';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Audit trail' };

export default function AuditPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <AuditView />
    </Suspense>
  );
}
