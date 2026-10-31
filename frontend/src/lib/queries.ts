'use client';

import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/context/AuthContext';
import { api, ok } from '@/lib/api/client';

// These routes return plain dicts, so OpenAPI types them as open objects.
// The fields the shell reads are named here.
export type Health = {
  status?: string;
  simulation_mode?: boolean;
  environment?: string;
  timestamp?: string;
};

export type AutomationLink = { key: string; label?: string; live: boolean; detail?: string };
export type AutomationStatus = { links?: AutomationLink[] };

/** Service health, shared by everything that shows it; refreshed every 30s. */
export function useHealth() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ['health'],
    queryFn: async () => (await ok(api.GET('/api/v1/health'))) as Health,
    enabled: isAuthenticated,
    refetchInterval: 30_000,
  });
}

/** Which links run by themselves (Sage writes, monday, mailbox...). */
export function useAutomationStatus() {
  const { isAuthenticated } = useAuth();
  return useQuery({
    queryKey: ['automation', 'status'],
    queryFn: async () => (await ok(api.GET('/api/v1/automation/status'))) as AutomationStatus,
    enabled: isAuthenticated,
    refetchInterval: 60_000,
  });
}
