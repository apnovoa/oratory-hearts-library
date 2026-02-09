// Service Worker for Bibliotheca Oratorii Sacratissimorum Cordium
var CACHE_NAME = "bibliotheca-v1";
var STATIC_ASSETS = [
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/img/logo.png",
  "/static/img/favicon.ico",
  "/static/manifest.json"
];

// Install: pre-cache static assets
self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (cacheNames) {
      return Promise.all(
        cacheNames
          .filter(function (name) {
            return name !== CACHE_NAME;
          })
          .map(function (name) {
            return caches.delete(name);
          })
      );
    })
  );
  self.clients.claim();
});

// Fetch: network-first for dynamic content, cache-first for static assets
self.addEventListener("fetch", function (event) {
  var url = new URL(event.request.url);

  // Cache-first for static assets (CSS, fonts, images)
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then(function (cached) {
        if (cached) {
          return cached;
        }
        return fetch(event.request).then(function (response) {
          if (response.ok) {
            var responseClone = response.clone();
            caches.open(CACHE_NAME).then(function (cache) {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // Network-first for dynamic content
  event.respondWith(
    fetch(event.request)
      .then(function (response) {
        return response;
      })
      .catch(function () {
        return caches.match(event.request).then(function (cached) {
          if (cached) {
            return cached;
          }
          // Offline fallback for navigation requests
          if (event.request.mode === "navigate") {
            return new Response(
              '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">' +
              '<meta name="viewport" content="width=device-width,initial-scale=1.0">' +
              '<title>Offline — Bibliotheca</title>' +
              '<style>body{font-family:"Cormorant Garamond",serif;background:#faf7f2;color:#4a1018;' +
              'display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}' +
              'h1{font-size:2rem;margin-bottom:0.5rem}p{color:#6b1d2a;font-size:1.1rem}</style></head>' +
              '<body><div><h1>Bibliotheca Offline</h1>' +
              '<p>You appear to be offline. Please check your connection and try again.</p>' +
              '</div></body></html>',
              { headers: { "Content-Type": "text/html" } }
            );
          }
        });
      })
  );
});
