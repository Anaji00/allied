'use client';

import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from 'next-themes';
import { NuqsAdapter } from 'nuqs/adapters/next/app';
import { ServiceWorker } from '@/components/service-worker';
import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AuthProvider } from '@/context/AuthContext';
import { ApiError } from '@/lib/api/client';

let browserQueryClient: QueryClient | undefined;

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Several parts of the screen ask for the same thing (health, status);
        // within this window they share one request instead of each polling.
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        // A 4xx will not fix itself on retry; a dropped connection might.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  });
}

function getQueryClient() {
  // Keep server renders isolated and the browser cache across renders.
  if (typeof window === 'undefined') return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={getQueryClient()}>
        <NuqsAdapter>
          <AuthProvider>
            <TooltipProvider>
              {children}
              {/* On phones toasts span the width; lift them clear of the tab bar and assistant button. */}
              <Toaster position="bottom-center" mobileOffset={{ bottom: 'calc(144px + env(safe-area-inset-bottom))' }} />
              <ServiceWorker />
            </TooltipProvider>
          </AuthProvider>
        </NuqsAdapter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
