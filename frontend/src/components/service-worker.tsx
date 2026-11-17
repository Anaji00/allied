'use client';

import { useEffect } from 'react';

/** Registers /sw.js, which only shows an offline page; it caches no data. */
export function ServiceWorker() {
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    // Only the real app uses it. On a dev server it just gets in the way (a stale offline page), so any old one is removed.
    if (process.env.NODE_ENV !== 'production') {
      void navigator.serviceWorker.getRegistrations().then((all) => all.forEach((r) => void r.unregister()));
      void caches?.keys().then((keys) => keys.filter((k) => k.startsWith('allied-offline')).forEach((k) => void caches.delete(k)));
      return;
    }
    // Browsers allow service workers on HTTPS and localhost only; elsewhere this rejects quietly.
    navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).catch(() => {});
  }, []);
  return null;
}
