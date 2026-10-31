// Allied Operations service worker.
//
// Its one job is a friendly "you're offline" page when a phone loses signal.
// It never caches pages or API answers: orders, stock and dates must always
// come fresh from the server, so a stale copy is worse than none.

const CACHE = 'allied-offline-v1';
const OFFLINE_URL = '/offline.html';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' }))));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  // Only page loads; scripts, images and /api calls go straight to the network untouched.
  if (event.request.mode !== 'navigate') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(OFFLINE_URL).then((page) => page || Response.error())),
  );
});
