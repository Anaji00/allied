'use client';

import { useQuery } from '@tanstack/react-query';
import { authFetch } from '@/lib/api';
import { MailSettings } from './mail-settings';

type Status = { connected: boolean; reason?: string | null; polling?: { running?: boolean; every_seconds?: number; folders?: string[] } };

function useMailStatus() {
  return useQuery({
    queryKey: ['mail', 'status'],
    queryFn: async (): Promise<Status | null> => {
      const res = await authFetch('/api/v1/mail/status', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Status) : null;
    },
    refetchInterval: 120_000,
    retry: false,
  });
}

/** Says plainly whether the shared mailbox is connected. When it is not, the page says why instead of looking empty. */
export function MailStatusBanner() {
  const s = useMailStatus().data;
  if (!s || s.connected) return null;
  return (
    <div role="status" className="rounded-lg border border-warn bg-warn-soft p-3 text-sm">
      <p className="font-medium text-warn">The shared mailbox is not connected yet.</p>
      {s.reason && <p className="mt-0.5 text-[13px] text-muted-foreground">{s.reason}</p>}
      <p className="mt-0.5 text-xs text-muted-foreground">
        Until it is, no new mail appears here. You can still write a draft with New draft.
      </p>
    </div>
  );
}

/** The reply helper makes no sense without a mailbox, so it is hidden until one is connected. */
export function MailSettingsWhenConnected() {
  const q = useMailStatus();
  if (q.data && !q.data.connected) return null;
  return <MailSettings />;
}
