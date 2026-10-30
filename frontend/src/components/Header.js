'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { fetchHealth, fetchLiveness } from '@/lib/api';
import { pageFor } from '@/lib/nav';
import styles from './Header.module.css';

export default function Header({ onMenu }) {
  const pathname = usePathname();
  const { isAuthenticated, facility } = useAuth();
  // 'unknown' until the first answer, so the badge never claims health it
  // has not checked.
  const [health, setHealth] = useState('unknown');
  const [lastCheck, setLastCheck] = useState('');

  useEffect(() => {
    let alive = true;
    async function check() {
      try {
        const data = await fetchHealth();
        if (alive) setHealth(data.status === 'healthy' ? 'healthy' : 'warning');
      } catch {
        // The full diagnostic needs a sign-in; before that, ask only whether it is up.
        try {
          await fetchLiveness();
          if (alive) setHealth('healthy');
        } catch {
          if (alive) setHealth('down');
        }
      }
      if (alive) setLastCheck(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    }
    check();
    const interval = setInterval(check, 30000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, []);

  const page = pageFor(pathname);
  const healthText = { unknown: 'Checking…', healthy: 'Service up', warning: 'Degraded', down: 'Service unreachable' }[health];

  return (
    <header className={styles.header}>
      <button type="button" className={styles.menu} onClick={onMenu} aria-label="Open menu">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="4" y1="7" x2="20" y2="7" />
          <line x1="4" y1="12" x2="20" y2="12" />
          <line x1="4" y1="17" x2="20" y2="17" />
        </svg>
      </button>
      <div className={styles.left}>
        <h1 className={styles.title}>{page.label}</h1>
        {page.description && <p className={styles.description}>{page.description}</p>}
      </div>
      <div className={styles.right}>
        {isAuthenticated && facility && <span className={styles.chip}>{facility}</span>}
        <span className={styles.health} title={lastCheck ? `Checked ${lastCheck}` : undefined}>
          <span className={`${styles.dot} ${styles[health]}`} />
          {healthText}
        </span>
      </div>
    </header>
  );
}
