'use client';

import { useQuery } from '@tanstack/react-query';
import { useInbox } from '@/components/inbox/use-inbox';
import { authFetch } from '@/lib/api';

/**
 * How many things really need this person: the same number Today shows. Until the server sends it, the old
 * count of waiting items is used so the badge never disappears.
 */
export function useNeedAction() {
  const waiting = useInbox().items;
  const day = useQuery({
    queryKey: ['today', 'my-day'],
    queryFn: async (): Promise<{ need_action?: number } | null> => {
      const res = await authFetch('/api/v1/my-day', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as { need_action?: number }) : null;
    },
    refetchInterval: 30_000,
    retry: false,
  });
  const critical = waiting.some((i) => i.severity === 'critical');
  return { count: day.data?.need_action ?? waiting.length, critical };
}
