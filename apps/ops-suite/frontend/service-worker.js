// EzoEzgi Ops Suite v0 -- asgari cache-first service worker (offline-first
// ilkesi, bkz. docs/MASTER_ROADMAP.md §6). YALNIZCA statik kabuk (shell)
// dosyalarini onbelleklerinde -- API/WS istekleri ASLA onbelleklenmez
// (canli veri, her zaman aga gitmeli).

const CACHE_NAME = "ops-suite-shell-v0";
const SHELL_FILES = [
  "/",
  "/css/style.css",
  "/js/app.js",
  "/js/ws_client.js",
  "/manifest.webmanifest",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(SHELL_FILES);
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (key) { return key !== CACHE_NAME; }).map(function (key) { return caches.delete(key); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function (event) {
  const url = new URL(event.request.url);

  // API/WS trafigi ASLA onbelleklenmez -- her zaman gercek aga gider.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(function (cached) {
      return cached || fetch(event.request);
    })
  );
});
