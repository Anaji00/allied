'use client';

import { useEffect } from 'react';
import { useLanguage } from '@/lib/language';

const TAG = { en: 'en', zh: 'zh-Hans', vi: 'vi' } as const;

/** Keeps the page's language tag in step with the person's language, so screen readers and Chinese fonts pick correctly. */
export function LangSync() {
  const { language } = useLanguage();
  useEffect(() => {
    document.documentElement.lang = TAG[language] ?? 'en';
  }, [language]);
  return null;
}
