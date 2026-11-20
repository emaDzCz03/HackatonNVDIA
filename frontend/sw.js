const CACHE_VERSION = "v5";
const CACHE_NAME = `anaqueles-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  "./",
  "index.html",
  "styles/styles.css",
  "scripts/main.js",
  "scripts/config.js",
  "manifest.webmanifest",
  "assets/icons/icon-192.png",
  "assets/icons/icon-512.png",
  "assets/icons/icon-512-maskable.png",
].map((path) => new URL(path, self.registration.scope).href);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }
  if (url.pathname === "/auditar" || url.pathname === "/ws/auditar") {
    return;
  }
  if (!PRECACHE_URLS.includes(url.href)) {
    return;
  }

  event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
});
