'use client';

import { useState, type FormEvent } from 'react';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { BrandMark } from './brand-mark';

/**
 * Every route is staff-only. The backend checks the password and issues the
 * token every later call carries; nothing here grants access by itself.
 */
export function SignIn() {
  const { loginEmployee } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const result = await loginEmployee(email.trim(), password);
      if (!result.ok) setError(result.error || 'Sign-in failed. Check your email and password.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-svh place-items-center bg-background px-4 py-10">
      <div className="grid w-full max-w-sm gap-6">
        <div className="grid justify-items-center gap-3 text-center">
          <BrandMark className="size-10" />
          <div className="grid gap-1">
            <h1 className="text-xl font-semibold tracking-tight text-balance">Sign in to Allied Operations</h1>
            <p className="text-sm text-muted-foreground">Use your work email and password.</p>
          </div>
        </div>

        <form onSubmit={submit} className="grid gap-4 rounded-xl border bg-card p-6 shadow-sm">
          <div className="grid gap-1.5">
            <label htmlFor="allied-email" className="text-sm font-medium">
              Work email
            </label>
            <Input
              id="allied-email"
              type="email"
              autoComplete="username"
              placeholder="you@alliedweldingsystems.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="grid gap-1.5">
            <label htmlFor="allied-password" className="text-sm font-medium">
              Password
            </label>
            <Input
              id="allied-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && (
            <p role="alert" className="rounded-md bg-bad-soft px-3 py-2 text-sm text-bad">
              {error}
            </p>
          )}
          <Button type="submit" disabled={busy} className="w-full">
            {busy && <Loader2 className="animate-spin" />}
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </div>
    </main>
  );
}
