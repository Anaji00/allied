import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { InboxView } from '@/components/inbox/inbox-view';

export const metadata = { title: 'Inbox' };

// Everything waiting on the signed-in person that isn't a row on a board.
export default function InboxPage() {
  return (
    <Suspense fallback={<RowsSkeleton />}>
      <InboxView />
    </Suspense>
  );
}
