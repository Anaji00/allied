import { Suspense } from 'react';
import { AdminTabs } from '@/components/shell/admin-tabs';
import { SageImports } from '@/components/admin/sage-imports';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Sage & imports' };

export default function SageImportsPage() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <SageImports />
      </Suspense>
    </>
  );
}
