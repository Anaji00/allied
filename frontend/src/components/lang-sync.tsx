'use client';

import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { setFxRates } from '@/lib/format';
import { useLanguage } from '@/lib/language';

const TAG = { en: 'en', zh: 'zh-Hans', vi: 'vi' } as const;

/** Keeps the page's language tag in step with the person's language, so screen readers and Chinese fonts pick correctly. */
export function LangSync() {
  const { language } = useLanguage();
  const { isAuthenticated } = useAuth();
  const client = useQueryClient();
  useEffect(() => {
    document.documentElement.lang = TAG[language] ?? 'en';
  }, [language]);
  // Today's exchange rates, so every amount can be shown in US dollars. Without them amounts stay in their own currency.
  const fx = useQuery({
    queryKey: ['fx'],
    enabled: isAuthenticated,
    staleTime: 6 * 3_600_000,
    retry: false,
    queryFn: async () => {
      const res = await authFetch('/api/v1/fx', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as { to_usd?: Record<string, number>; as_of?: string | null }) : null;
    },
  });
  useEffect(() => {
    // The first time rates are known, screens already drawn are drawn again so their amounts show in dollars.
    if (setFxRates(fx.data?.to_usd, fx.data?.as_of)) void client.invalidateQueries();
  }, [fx.data, client]);
  return null;
}
