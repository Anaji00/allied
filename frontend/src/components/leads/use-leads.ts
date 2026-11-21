'use client';

import { useQuery } from '@tanstack/react-query';
import { leads, type LeadList, type LeadSettings, type Suggestion, type Summary } from './api';

/** null means the server has no leads routes yet, so a screen can say so plainly instead of failing. */
async function soft<T>(path: string): Promise<T | null> {
  try {
    return await leads<T>(path);
  } catch {
    return null;
  }
}

export const useLeadSettings = () => useQuery({ queryKey: ['leads', 'settings'], queryFn: () => soft<LeadSettings>('/settings'), retry: false });
export const useSummary = () => useQuery({ queryKey: ['leads', 'summary'], queryFn: () => soft<Summary>('/summary'), retry: false });
export const useSuggestions = () => useQuery({ queryKey: ['leads', 'suggestions'], queryFn: () => soft<Suggestion[]>('/suggestions'), retry: false });
export const useLeadList = (status: string, due: boolean) =>
  useQuery({
    queryKey: ['leads', 'list', status, due],
    queryFn: () => soft<LeadList>(`?limit=200${status ? `&status=${encodeURIComponent(status)}` : ''}${due ? '&due=true' : ''}`),
    retry: false,
    refetchInterval: 60_000,
  });
