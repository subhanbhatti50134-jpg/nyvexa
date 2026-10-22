/* Nyvexa service worker — app shell cache, network-first for API. */

const CACHE = "nyvexa-v4";
const SHELL = [
  "/",
  "/static/css/theme.css",
  "/static/css/layout.css",
  "/static/css/chat.css",
  "/static/css/sidebar.css",
  "/static/js/main.js",
  "/static/js/chat.js",
  "/static/js/streaming.js",
  "/static/js/markdown.js",
  "/static/js/ui.js",
  "/static/js/api.js",
  "/static/js/history.js",
  "/static/js/voice.js",
  "/static/assets/favicon.svg",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Only manage our own assets — intercepting cross-origin font/CDN requests
  // re-fetches them without the page's CORS context and breaks font loading.
  if (url.origin !== self.location.origin) return;
  // Never cache API/SSE
  if (url.pathname.startsWith("/api/")) return;

  if (event.request.method !== "GET") return;

  // Network-first for navigation, cache-first for static
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put("/", copy));
          return resp;
        })
        .catch(() => caches.match("/"))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((resp) => {
          if (resp.ok && url.origin === self.location.origin) {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(event.request, copy));
          }
          return resp;
        })
    )
  );
});
