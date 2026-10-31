import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { ProjectsView } from '@/components/projects/projects-view';

export const metadata = { title: 'Projects' };

export default function ProjectsPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <ProjectsView />
    </Suspense>
  );
}
