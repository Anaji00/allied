'use client';

import { useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { Button, Field, Input, Notice, Spinner } from '@/components/ui';
import styles from './InternalPortalGate.module.css';

export default function InternalPortalGate({ children }) {
  const { isAuthenticated, isInitialized, loginEmployee } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // Still reading the saved session from localStorage.
  if (!isInitialized) {
    return (
      <div className={styles.loading}>
        <Spinner />
        <span>Loading…</span>
      </div>
    );
  }

  // Every route is staff-only. The customer-facing portal was removed, so
  // there is no public route left to exempt.
  if (isAuthenticated) {
    return <>{children}</>;
  }

  // Credentials are verified by the backend, which issues the JWT that every
  // subsequent API call carries. The previous gate accepted any three
  // characters and offered a one-click bypass, so it granted the UI without
  // establishing any identity at all.
  async function handleUnlock(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const result = await loginEmployee(email.trim(), password);
      if (!result.ok) {
        setError(result.error || 'Sign-in failed. Check your email and password.');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.head}>
          <span className={styles.mark} aria-hidden="true">A</span>
          <h1 className={styles.title}>Sign in to Allied Operations</h1>
          <p className={styles.sub}>Use your work email and password.</p>
        </div>

        <form onSubmit={handleUnlock} className={styles.form}>
          <Field label="Work email">
            <Input
              id="allied-email"
              type="email"
              autoComplete="username"
              placeholder="you@alliedweldingsystems.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </Field>

          <Field label="Password">
            <Input
              id="allied-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </Field>

          {error && <Notice tone="danger">{error}</Notice>}

          <Button type="submit" variant="primary" loading={busy} className={styles.submit}>
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </div>
    </div>
  );
}
