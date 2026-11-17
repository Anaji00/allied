import { Suspense } from 'react';
import { TodayTabs } from '@/components/shell/today-tabs';
import { BallView } from '@/components/ball/ball-view';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Who is it waiting on?' };

export default function Page() {
  return (
    <>
      <TodayTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <BallView />
      </Suspense>
    </>
  );
}
