import { Suspense } from 'react';
import { BoardsView } from '@/components/boards/boards-view';
import { RowsSkeleton } from '@/components/data/states';

// Home is the boards: monday's boards as monday shows them, with the app's automations on each row.
export default function BoardsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <BoardsView />
    </Suspense>
  );
}
