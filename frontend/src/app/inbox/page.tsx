import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { TodayView } from '@/components/today/today-view';

export const metadata = { title: 'Today' };

// Everything waiting on the signed-in person, in one list with one next step per line.
export default function InboxPage() {
  return (
    <>
      <SecretaryTabs />
      <Suspense fallback={<RowsSkeleton />}>
        <TodayView />
      </Suspense>
    </>
  );
}
