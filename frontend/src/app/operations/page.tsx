import { Suspense } from 'react';
import { SageImports } from '@/components/admin/sage-imports';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Sage & imports' };

export default function SageImportsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <SageImports />
    </Suspense>
  );
}
