// Service worker for the My Home PWA.
//
// NETWORK-FIRST: when online, every asset is fetched fresh from the server, so a
// new build is *never* served stale - no cache-version bumping needed. The cache
// is only a fallback for when the device is offline. API calls are never touched.
const CACHE = 'myhome'; // fixed name; it's just the offline fallback store

self.addEventListener('install', () => {
  self.skipWaiting(); // take over as soon as the new SW is byte-different
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Let everything dynamic hit the network untouched: API, the SSE stream, non-GET.
  if (e.request.method !== 'GET' || url.pathname.includes('/api/')) return;

  // Network-first: always try the network (bypassing the HTTP cache so it's
  // genuinely fresh), update the offline copy, and only fall back to cache when
  // the network is unavailable.
  e.respondWith(
    fetch(e.request, { cache: 'no-cache' })
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches
          .match(e.request)
          .then(
            (cached) =>
              cached ||
              (e.request.mode === 'navigate' ? caches.match('./index.html') : Response.error())
          )
      )
  );
});
