'use client';

import { useCallback, useState } from 'react';
import { AuthProvider } from '@/context/AuthContext';
import Sidebar from './Sidebar';
import Header from './Header';
import InternalPortalGate from './InternalPortalGate';

export default function AppLayout({ children }) {
  // Only matters below the tablet breakpoint, where the sidebar slides in.
  const [navOpen, setNavOpen] = useState(false);
  const close = useCallback(() => setNavOpen(false), []);

  return (
    <AuthProvider>
      <div className="appLayout">
        <Sidebar open={navOpen} onNavigate={close} />
        {navOpen && <div className="navScrim" onClick={close} aria-hidden="true" />}
        <div className="mainContent">
          <Header onMenu={() => setNavOpen(true)} />
          <main className="pageContent">
            <InternalPortalGate>{children}</InternalPortalGate>
          </main>
        </div>
      </div>
    </AuthProvider>
  );
}
