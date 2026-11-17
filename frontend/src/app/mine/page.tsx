import { Suspense } from 'react';
import { TodayTabs } from '@/components/shell/today-tabs';
import { RowsSkeleton } from '@/components/data/states';
import { MineView } from '@/components/mine/mine-view';

export const metadata = { title: 'My day' };

export default function Page() {
  return (
    <>
      <TodayTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <MineView />
      </Suspense>
    </>
  );
}
