'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { RowsSkeleton } from '@/components/data/states';
import { useBoards } from '@/components/boards/use-boards';
import { JobsHome } from './jobs-home';

/**
 * Jobs opens as the first board on the Boards page, next to the monday boards, once the server has a Jobs board.
 * Before that the older Jobs page still works, so nothing disappears while the server is updated.
 */
export function JobsEntry() {
  const router = useRouter();
  const boards = useBoards();
  const hasJobsBoard = boards.data?.some((b) => b.board_id === 'jobs') ?? false;
  useEffect(() => {
    if (hasJobsBoard) router.replace('/boards?board=jobs');
  }, [hasJobsBoard, router]);
  if (boards.isPending || hasJobsBoard) return <RowsSkeleton rows={6} />;
  return <JobsHome />;
}
