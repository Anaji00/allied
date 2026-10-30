'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { fetchAutomationStatus } from '@/lib/api';
import { NAV, isActive } from '@/lib/nav';
import styles from './Sidebar.module.css';

export default function Sidebar({ open = false, onNavigate }) {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthenticated, logoutEmployee } = useAuth();

  // What is actually wired up. Null until the system answers, so the footer
  // never claims a connection it has not confirmed.
  const [fetched, setFetched] = useState(null);
  const asideRef = useRef(null);

  // Refetch whenever the sign-in state changes: before sign-in the API
  // rejects the call, so a one-off fetch on mount would stay empty.
  useEffect(() => {
    if (!isAuthenticated) return undefined;
    let alive = true;
    fetchAutomationStatus()
      .then((d) => alive && setFetched(d.links || []))
      .catch(() => alive && setFetched([]));
    return () => {
      alive = false;
    };
  }, [isAuthenticated]);

  // Signed out, nothing is known, so nothing is claimed.
  const links = isAuthenticated ? fetched : null;

  // Mobile drawer: Escape closes it, and opening it moves focus inside.
  useEffect(() => {
    if (!open) return undefined;
    asideRef.current?.querySelector('a, button')?.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') onNavigate?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onNavigate]);

  const sageLive = Boolean(links?.find((l) => l.key === 'sage_write')?.live);
  const mondayLive = Boolean(links?.find((l) => l.key === 'monday')?.live);

  return (
    <aside ref={asideRef} className={`${styles.sidebar} ${open ? styles.open : ''}`} aria-label="Main navigation">
      <div className={styles.brand}>
        <span className={styles.mark} aria-hidden="true">A</span>
        <span className={styles.brandText}>
          <strong>Allied Welding</strong>
          <small>Operations</small>
        </span>
      </div>

      <nav className={styles.nav}>
        {NAV.map((group) => (
          <div key={group.section} className={styles.group}>
            <div className={styles.section}>{group.section}</div>
            {group.items.map((item) => {
              const active = isActive(item.href, pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={active ? 'page' : undefined}
                  className={`${styles.link} ${active ? styles.active : ''}`}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Reported, not asserted: this footer is on every screen. */}
      <div className={styles.footer}>
        <Link href="/integrations" onClick={onNavigate} className={styles.status}>
          <StatusRow label="monday.com" live={mondayLive} known={links !== null} on="Live" off="Not connected" />
          <StatusRow label="Sage 50" live={sageLive} known={links !== null} on="Writing" off="Import files" />
        </Link>
        {isAuthenticated ? (
          <button type="button" className={styles.signBtn} onClick={logoutEmployee}>
            Sign out
          </button>
        ) : (
          <button type="button" className={styles.signBtn} onClick={() => router.push('/')}>
            Sign in
          </button>
        )}
      </div>
    </aside>
  );
}

function StatusRow({ label, live, known, on, off }) {
  return (
    <span className={styles.statusRow}>
      <span className={`${styles.dot} ${!known ? '' : live ? styles.dotOn : styles.dotOff}`} />
      <span className={styles.statusLabel}>{label}</span>
      <span className={styles.statusValue}>{!known ? '…' : live ? on : off}</span>
    </span>
  );
}
