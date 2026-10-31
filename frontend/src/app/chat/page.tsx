import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { AssistantView } from '@/components/tools/assistant-view';

export const metadata = { title: 'Assistant' };

export default function AssistantPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={6} />}>
      <AssistantView />
    </Suspense>
  );
}
