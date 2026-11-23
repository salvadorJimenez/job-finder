// Lets job_finder be installed as an app. It only keeps the "not running" page and the
// files that page needs, so data is never stale: pages, forms and API answers always
// come from the server. Bump CACHE when the precached files change.
const CACHE = "job-finder-v3";
const OFFLINE_URL = "/offline";
const PRECACHE = [
  OFFLINE_URL,
  "/static/style.css",
  "/static/fonts/GeistPixel-Square.woff2",
  "/static/fonts/GeistMono-Variable.woff2",
  "/static/icons/favicon-32.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) { return cache.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.filter(function (name) { return name !== CACHE; })
        .map(function (name) { return caches.delete(name); }));
    }).then(function () { return self.clients.claim(); })
  );
});

// Only page loads (GET navigations) are handled: network first, and the offline page
// when the server cannot be reached. Everything else goes to the network untouched.
self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET" || request.mode !== "navigate") return;
  event.respondWith(
    fetch(request).catch(function () { return caches.match(OFFLINE_URL); })
  );
});
