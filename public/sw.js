'use strict';
const CACHE = 'ariel-pwa-v1';
const ASSETS = ['/offline.html','/manifest.webmanifest','/icons/ariel-192.png','/icons/ariel-512.png','/icons/ariel-maskable-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('ariel-pwa-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Conversaciones, claves, archivos privados y respuestas de API nunca se cachean.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/uploads/') || url.pathname.startsWith('/voices/') || url.pathname.startsWith('/reels/')) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).then(response => response.status >= 500 ? caches.match('/offline.html') : response).catch(() => caches.match('/offline.html')));
    return;
  }
  if (ASSETS.includes(url.pathname)) event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});
