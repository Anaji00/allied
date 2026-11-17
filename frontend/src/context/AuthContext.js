'use client';

import { createContext, useCallback, useContext, useEffect, useSyncExternalStore } from 'react';
import { toast } from 'sonner';

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

function readStoredSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : EMPTY_SESSION;
  } catch {
    // Private mode / blocked storage: start signed out rather than throwing.
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
    if (next) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Non-fatal: the session just will not survive a reload.
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

    setUnauthorizedHandler(async () => {
      const refresh = _session?.refresh_token;
      if (!refresh) {
        setSession(EMPTY_SESSION);
        toast.error('Your session expired — please sign in again.');
        return null;
      }
      try {
        const { access_token: accessToken } = await refreshAccessToken(refresh);
        setSession({ ..._session, access_token: accessToken });
        void syncProfile();
        return accessToken;
      } catch (err) {
        // Refresh token expired, the account was disabled or deleted, or the password was changed elsewhere.
        setSession(EMPTY_SESSION);
        const why = 'Your session ended. Sign in again.';
        if (why) {
          try {
            sessionStorage.setItem('allied.signin.notice', why);
          } catch {
            // Private windows refuse storage; the toast below still tells them.
          }
        }
        toast.error(why || 'Your session expired — please sign in again.');
        return null;
      }
    });
    return () => setUnauthorizedHandler(null);
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
