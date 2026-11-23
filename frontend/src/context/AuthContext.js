'use client';

import { createContext, useCallback, useContext, useEffect, useSyncExternalStore } from 'react';
import { toast } from 'sonner';

import { logRefresh } from '@/lib/authlog';
import { authFetch, loginUser, refreshAccessToken, setAuthToken, setUnauthorizedHandler } from '@/lib/api';

const STORAGE_KEY = 'allied_staff_auth';

// Role ranking, most privileged first. Lets a page ask for a minimum level
// rather than enumerating every role that satisfies it.
const ROLE_RANK = { ADMIN: 0, MANAGER: 1, OPERATOR: 2, VIEWER: 3, GUEST: 99 };

// ---------------------------------------------------------------------------
// Session store
//
// localStorage is an external store, so the session is read through
// useSyncExternalStore rather than copied into state inside an effect. That
// keeps the server and client renders consistent and avoids the cascading
// re-render that setState-in-effect causes.
// ---------------------------------------------------------------------------

const EMPTY_SESSION = null;

let _session = EMPTY_SESSION;
let _hydrated = false;
const _listeners = new Set();

let _storageOk = true;

function readStoredSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : EMPTY_SESSION;
  } catch {
    // Private mode / blocked storage: start signed out rather than throwing.
    _storageOk = false;
    return EMPTY_SESSION;
  }
}

function getSnapshot() {
  // Hydrate once, on the client, the first time a snapshot is requested.
  // getSnapshot must return a cached value: returning a fresh object each call
  // would make React loop.
  if (!_hydrated) {
    _hydrated = true;
    _session = readStoredSession();
    if (_session?.access_token) {
      setAuthToken(_session.access_token);
    }
  }
  return _session;
}

function getServerSnapshot() {
  // No storage on the server; every SSR pass renders signed out.
  return EMPTY_SESSION;
}

function subscribe(listener) {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

function setSession(next) {
  _session = next;
  setAuthToken(next?.access_token || null);
  try {
    if (!next?.access_token) {
      localStorage.removeItem('allied.tomonday.board');
      localStorage.removeItem('allied.tomonday.group');
    }
    if (next) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Non-fatal: the session just will not survive a reload.
    _storageOk = false;
  }
  _listeners.forEach((l) => l());
}

// ---------------------------------------------------------------------------

/**
 * What useAuth() returns. Typed here so the TypeScript shell sees real
 * signatures rather than the placeholders below.
 *
 * @typedef {object} AuthValue
 * @property {boolean} isAuthenticated
 * @property {string} userRole ADMIN, MANAGER, OPERATOR, VIEWER, or GUEST when signed out
 * @property {string} employeeName
 * @property {string} employeeEmail
 * @property {string} facility
 * @property {boolean} isInitialized
 * @property {(email: string, password: string) => Promise<{ ok: boolean, error?: string }>} loginEmployee
 * @property {() => void} logoutEmployee
 * @property {(accessToken: string, refreshToken: string) => void} replaceTokens Keeps this device signed in after a password change
 * @property {(minimumRole: string) => boolean} hasRole True at or above the given role
 */

const AuthContext = createContext(/** @type {AuthValue} */ ({
  isAuthenticated: false,
  userRole: 'GUEST',
  employeeName: '',
  employeeEmail: '',
  facility: '',
  isInitialized: false,
  loginEmployee: async () => ({ ok: false, error: 'not ready' }),
  logoutEmployee: () => {},
  replaceTokens: () => {},
  hasRole: () => false,
}));

export function AuthProvider({ children }) {
  const session = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const user = session?.user;
  const userRole = user?.role || 'GUEST';

  const logoutEmployee = useCallback(() => setSession(EMPTY_SESSION), []);

  // Give api.js a way to refresh the access token when the API rejects it.
  // This only registers a callback on an external module, so it belongs in an
  // effect and performs no synchronous state update.
  useEffect(() => {
    const syncProfile = async () => {
      if (!_session?.access_token) return;
      try {
        const res = await authFetch('/api/v1/auth/profile', { cache: 'no-store' });
        if (res.ok && _session) {
          const profile = await res.json();
          setSession({ ..._session, user: profile });
        }
      } catch {
        // Non-fatal: keep existing cached profile.
      }
    };
    void syncProfile();

    // ------------------------------------------------------------------ renewal
    // A renewal runs only when (a) a request that carried a token is answered "Token has expired", or (b) the quiet timer says
    // 75% of the token's life is gone. Never on a page load, never because a request went out with no token yet. Everything it
    // does is written to the renewal log (shown to admins on the System page).
    const decodeExp = (token) => {
      try {
        const body = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
        return { exp: body.exp ? body.exp * 1000 : null, iat: body.iat ? body.iat * 1000 : null };
      } catch {
        return { exp: null, iat: null };
      }
    };
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    let refreshing = null;

    // One renewal at a time across every tab, using the browser's own lock. Inside the lock the stored tokens are read afresh: if another
    // tab already renewed, its tokens are used and no request is made; otherwise the renewal uses the token read inside the lock, and
    // is read again before every retry, so a rotated token is never sent twice.
    const renew = (trigger) => {
      if (refreshing) return refreshing;
      const startedWith = _session?.refresh_token;
      const run = async () => {
        const newer = () => {
          const stored = readStoredSession();
          return stored?.refresh_token && stored.refresh_token !== startedWith && stored.access_token ? stored : null;
        };
        let other = newer();
        if (other) {
          setSession(other);
          logRefresh({ trigger, status: 'other tab', did: 'used the tokens another tab stored' });
          return { token: other.access_token };
        }
        for (let attempt = 0; attempt < 4; attempt += 1) {
          // With working storage, nothing stored means signed out. With blocked storage this tab's own copy is all there is.
          const current = _storageOk ? readStoredSession() : _session;
          const refresh = current?.refresh_token;
          if (!refresh) {
            logRefresh({ trigger, status: 'none', did: 'no renewal token to use' });
            return { rejected: true, reason: 'no renewal token' };
          }
          try {
            const out = await refreshAccessToken(refresh);
            setSession({
              ...current,
              access_token: out.access_token,
              refresh_token: out.refresh_token || refresh,
              tokenAt: new Date().toISOString(),
            });
            logRefresh({ trigger, status: 200, did: 'renewed' });
            void syncProfile();
            return { token: out.access_token };
          } catch (err) {
            other = newer();
            if (other) {
              setSession(other);
              logRefresh({ trigger, status: err?.status ?? 'error', did: 'another tab renewed meanwhile; used its tokens' });
              return { token: other.access_token };
            }
            if (err?.transient) {
              logRefresh({ trigger, status: err.status ?? 'no answer', did: 'server busy, will retry' });
              if (attempt >= 3) return { transient: true };
              toast('The server is restarting, trying again…', { id: 'server-restarting' });
              await sleep(2000 * (attempt + 1));
            } else {
              // The server refused. A refused renewal token is never sent a second time: the server treats a repeat as reuse and
              // ends every session. The check for another tab's newer tokens above has already run, so a refusal ends this session once.
              logRefresh({ trigger, status: err.status, did: 'refused, signing out', why: err.message });
              return { rejected: true, reason: 'the server refused the renewal', why: err.message };
            }
          }
        }
        return { transient: true };
      };
      refreshing = (async () => {
        try {
          return typeof navigator !== 'undefined' && navigator.locks ? await navigator.locks.request('allied-renew', run) : await run();
        } finally {
          refreshing = null;
        }
      })();
      return refreshing;
    };

    const endSession = (reason, why) => {
      logRefresh({ trigger: 'sign-out', did: reason });
      setSession(EMPTY_SESSION);
      const text = why && /password was changed/i.test(why) ? why : `Your session ended: ${reason}. Sign in again.`;
      try {
        sessionStorage.setItem('allied.signin.notice', text);
      } catch {
        // Private windows refuse storage; the toast below still tells them.
      }
      toast.error(text, { id: 'session-ended' });
    };

    setUnauthorizedHandler(async ({ url, sentToken, detail } = {}) => {
      // Signed out (here or on another tab): nothing to renew.
      if (!_session) return null;
      const current = _session?.access_token;
      // The request went out with no token (before sign-in finished loading) or an older one: retry with the current token.
      if (!sentToken || (current && sentToken !== current)) {
        logRefresh({ trigger: `401 on ${url}`, status: 'none', did: sentToken ? 'retried with the newer token' : 'request had no token yet; retried without renewing' });
        return current || null;
      }
      if (/password was changed|no longer exists/i.test(detail || '')) {
        endSession('the account changed', detail);
        return null;
      }
      const out = await renew(`401 on ${url}`);
      if (out?.token) return out.token;
      if (out?.transient) return null;
      endSession(out?.reason || 'the server refused the renewal', out?.why);
      return null;
    });

    // Signing out (or in) in another tab arrives here as a storage event: follow it, so a signed-out tab never renews and writes the session back.
    const onStorage = (e) => {
      if (e.key !== STORAGE_KEY) return;
      let next = EMPTY_SESSION;
      try {
        next = e.newValue ? JSON.parse(e.newValue) : EMPTY_SESSION;
      } catch {
        next = EMPTY_SESSION;
      }
      _session = next;
      setAuthToken(next?.access_token || null);
      if (!next) logRefresh({ trigger: 'storage', did: 'signed out on another tab' });
      _listeners.forEach((l) => l());
    };
    window.addEventListener('storage', onStorage);

    // The quiet timer: renew at 75% of the token's life, with a little jitter so tabs do not renew together.
    const quiet = window.setInterval(() => {
      const tok = _session?.access_token;
      if (!tok || !_session?.refresh_token) return;
      const { exp, iat } = decodeExp(tok);
      const start = iat ?? Date.parse(_session.tokenAt || _session.loginTime || '');
      if (!exp || !Number.isFinite(start)) return;
      const jitter = Math.random() * 120_000;
      if (Date.now() > start + (exp - start) * 0.75 + jitter) void renew('quiet timer');
    }, 60_000);
    return () => {
      window.clearInterval(quiet);
      window.removeEventListener('storage', onStorage);
      setUnauthorizedHandler(null);
    };
  }, []);

  /** Authenticates against the backend. Returns {ok} or {ok:false, error}. */
  const loginEmployee = useCallback(async (email, password) => {
    try {
      const data = await loginUser(email, password);
      setSession({
        access_token: data.access_token,
        refresh_token: data.refresh_token,
        user: data.user || {},
        loginTime: new Date().toISOString(),
      });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err?.message || 'Login failed' };
    }
  }, []);

  /** After a password change the server ends every older session and sends new tokens, so this device stays signed in. */
  const replaceTokens = useCallback((accessToken, refreshToken) => {
    if (!_session || !accessToken) return;
    setSession({ ..._session, access_token: accessToken, refresh_token: refreshToken || _session.refresh_token });
  }, []);

  /** True when the signed-in user is at or above the given role. */
  const hasRole = useCallback(
    (minimumRole) =>
      (ROLE_RANK[userRole] ?? ROLE_RANK.GUEST) <= (ROLE_RANK[minimumRole] ?? ROLE_RANK.GUEST),
    [userRole]
  );

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: Boolean(session?.access_token),
        userRole,
        employeeName: user?.full_name || user?.email || '',
        employeeEmail: user?.email || '',
        facility: user?.facility || '',
        // useSyncExternalStore resolves on the first client render, so by the
        // time this runs in the browser the session is already known.
        isInitialized: true,
        loginEmployee,
        logoutEmployee,
        replaceTokens,
        hasRole,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
