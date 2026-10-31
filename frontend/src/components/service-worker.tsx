'use client';

import { useEffect } from 'react';

/** Registers /sw.js, which only shows an offline page; it caches no data. */
export function ServiceWorker() {
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return;
    // Browsers allow service workers on HTTPS and localhost only; elsewhere this rejects quietly.
    navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).catch(() => {});
  }, []);
  return null;
}
