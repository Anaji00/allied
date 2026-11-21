'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Bell, BellRing } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';

type Following = { items: { item_kind: string; item_ref: string; changed_since_seen?: boolean }[] };
const KINDS = new Set(['job', 'order', 'rfq']);

/** Follow a job, order or quote request: it then shows up on Today when something changes, until you mark it seen. Shown once the server has it. */
export function FollowButton({ kind, refId }: { kind: string; refId: string }) {
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ['following'],
    queryFn: async (): Promise<Following | null> => {
      const res = await authFetch('/api/v1/following', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Following) : null;
    },
    retry: false,
    staleTime: 60_000,
    enabled: KINDS.has(kind),
  });
  const on = q.data?.items.some((i) => i.item_kind === kind && i.item_ref === refId) ?? false;
  const toggle = useMutation({
    mutationFn: async () => {
      const res = await authFetch(on ? '/api/v1/unfollow' : '/api/v1/follow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_kind: kind, item_ref: refId }),
      });
      if (!res.ok) throw new Error(`That didn’t work (${res.status}).`);
    },
    onSuccess: () => {
      toast.success(on ? 'No longer following' : 'Following. It will show on Today when it changes.', { id: 'follow' });
      void client.invalidateQueries({ queryKey: ['following'] });
      void client.invalidateQueries({ queryKey: ['today'] });
    },
    onError: (e) => toast.error('Not changed', { description: e.message }),
  });
  if (!KINDS.has(kind) || !q.data) return null;
  return (
    <Button size="sm" variant="outline" disabled={toggle.isPending} aria-pressed={on} onClick={() => toggle.mutate()}>
      {on ? <BellRing /> : <Bell />} {on ? 'Following' : 'Follow'}
    </Button>
  );
}
