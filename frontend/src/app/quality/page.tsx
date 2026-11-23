import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { QualityView } from '@/components/quality/quality-view';
import { AdminTabs } from '@/components/shell/admin-tabs';

export const metadata = { title: 'Material test papers' };

export default function Page() {
  return (
    <>
      <AdminTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <QualityView />
      </Suspense>
    </>
  );
}
