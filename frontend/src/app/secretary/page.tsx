import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { SecretaryHome } from '@/components/secretary/secretary-home';
import { SecretaryTabs } from '@/components/shell/secretary-tabs';

export const metadata = { title: 'Secretary' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <SecretaryHome />
      </Suspense>
    </>
  );
}
