import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { InboxView } from '@/components/inbox/inbox-view';

// Home is the Inbox: everything waiting on the signed-in person.
export default function InboxPage() {
  return (
    <Suspense fallback={<RowsSkeleton />}>
      <InboxView />
    </Suspense>
  );
}
