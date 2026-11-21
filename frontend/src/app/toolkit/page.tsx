import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { AssistantView } from '@/components/tools/assistant-view';

export const metadata = { title: 'Toolkit' };

export default function Page() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <AssistantView />
    </Suspense>
  );
}
