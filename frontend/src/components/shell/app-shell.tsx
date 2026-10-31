'use client';

import { ViewTransition, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import AssistantWidget from '@/components/assistant/AssistantWidget';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';
import { useAuth } from '@/context/AuthContext';
import { AppSidebar } from './app-sidebar';
import { CommandMenu } from './command-menu';
import { PhoneTabBar } from './phone-tab-bar';
import { SignIn } from './sign-in';
import { TopBar } from './top-bar';

const noop = () => () => {};

/** False during the server render and hydration, true once in the browser. */
function useIsClient() {
  return useSyncExternalStore(noop, () => true, () => false);
}

export function AppShell({ children }: { children: ReactNode }) {
  const isClient = useIsClient();
  const { isAuthenticated } = useAuth();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    if (!isAuthenticated) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isAuthenticated]);

  // The session lives in localStorage, which the server cannot read. Deciding
  // only in the browser avoids flashing the sign-in screen at a signed-in user.
  if (!isClient) return <div className="min-h-svh bg-background" aria-busy="true" />;
  if (!isAuthenticated) return <SignIn />;

  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  // The sidebar remembers being collapsed in a cookie it writes itself.
  const sidebarOpen = !document.cookie.split('; ').includes('sidebar_state=false');

  return (
    <SidebarProvider defaultOpen={sidebarOpen}>
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-background px-3 py-2 text-sm font-medium shadow focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <TopBar onOpenPalette={() => setPaletteOpen(true)} shortcut={mac ? '⌘K' : 'Ctrl K'} />
        {/* On phones the bottom padding keeps the last row clear of the tab bar and assistant button. */}
        <main
          id="main"
          tabIndex={-1}
          className="mx-auto w-full max-w-[1440px] min-w-0 flex-1 px-4 pt-6 pb-[calc(9rem+env(safe-area-inset-bottom))] outline-none md:px-8 md:pb-6"
        >
          {/* A quick crossfade when moving between areas; filters and rows change in place. */}
          <ViewTransition key={pathname} enter="page-in" exit="page-out" default="none">
            <div>{children}</div>
          </ViewTransition>
        </main>
      </SidebarInset>
      <PhoneTabBar />
      <CommandMenu open={paletteOpen} onOpenChange={setPaletteOpen} />
      <AssistantWidget />
    </SidebarProvider>
  );
}
