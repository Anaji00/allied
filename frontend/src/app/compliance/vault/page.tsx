import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { QualityView } from '@/components/quality/quality-view';

export const metadata = { title: 'Mill certificates' };

export default function QualityPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <QualityView />
    </Suspense>
  );
}
