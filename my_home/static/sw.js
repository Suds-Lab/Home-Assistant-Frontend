// Service worker for the My Home PWA. Caches the app shell so it loads
// instantly and works offline; never caches API calls (live data).
const CACHE = 'myhome-v22';
const SHELL = [
  './',
  './index.html',
  './styles.css',
  './app.jsx',
  './manifest.webmanifest',
  './vendor/react.production.min.js',
  './vendor/react-dom.production.min.js',
  './vendor/babel.min.js',
  './vendor/uplot.iife.min.js',
  './vendor/uplot.min.css',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
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
  // Let everything dynamic hit the network: API, the SSE stream, non-GET.
  if (e.request.method !== 'GET' || url.pathname.includes('/api/')) return;

  // Stale-while-revalidate: serve the cached shell instantly, but always
  // refetch in the background and update the cache, so an updated add-on
  // (new CSS/JS) reaches users on the next load instead of being stuck.
  e.respondWith(
    (async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(e.request);
      const network = fetch(e.request)
        .then((res) => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        })
        .catch(() => null);
      return (
        cached ||
        (await network) ||
        (e.request.mode === 'navigate' ? cache.match('./index.html') : Response.error())
      );
    })()
  );
});
