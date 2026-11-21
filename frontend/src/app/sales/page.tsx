import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { SalesHome } from '@/components/sales/sales-home';

export const metadata = { title: 'Sales' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <SalesHome />
      </Suspense>
    </>
  );
}
