'use client';

import { useQuery } from '@tanstack/react-query';
import { authFetch } from '@/lib/api';

export type MailSettings = {
  ai_opt_in: boolean;
  auto_draft: boolean;
  style_note: string;
  exclude_senders: string[];
  exclude_words: string[];
};

export const EMPTY_SETTINGS: MailSettings = { ai_opt_in: false, auto_draft: false, style_note: '', exclude_senders: [], exclude_words: [] };

/** Whether the AI helper can work right now, and a plain reason when it cannot. Reads status only: it never calls an AI route. */
export function useAiHealth() {
  return useQuery({
    queryKey: ['ai-health'],
    queryFn: async (): Promise<{ ready: boolean; reason: string | null }> => {
      const res = await authFetch('/api/v1/automation/status', { cache: 'no-store' }).catch(() => null);
      if (!res?.ok) return { ready: false, reason: 'The AI helper could not be checked.' };
      const j = await res.json().catch(() => null);
      const ai = j?.ai;
      return { ready: Boolean(ai?.ready), reason: ai?.ready ? null : (ai?.reason ?? 'The AI helper is not set up.') };
    },
    staleTime: 60_000,
    retry: false,
  });
}

/** This person's reply-helper choices. Null means the server has no such setting yet. */
export function useMailSettings() {
  return useQuery({
    queryKey: ['mail-settings'],
    queryFn: async (): Promise<MailSettings | null> => {
      const res = await authFetch('/api/v1/me/mail-settings', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ({ ...EMPTY_SETTINGS, ...(await res.json()) } as MailSettings) : null;
    },
    retry: false,
  });
}
