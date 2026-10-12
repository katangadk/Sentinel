/* No authenticated API or location caching. Offline status must remain honest. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => new Response(
    '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sentinel offline</title><body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:1rem"><h1>Sentinel is unreachable</h1><p>This phone cannot reach the dashboard. The laptop status cannot be verified. Reconnect and reload.</p><button onclick="location.reload()">Try again</button></body></html>',
    { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } }
  )));
});
self.addEventListener('push', event => {
  let data = { title: 'Sentinel security alert', body: 'Open Sentinel to view your alerts.' };
  try { if (event.data) data = event.data.json(); } catch {}
  event.waitUntil(self.registration.showNotification(data.title, { body: data.body, tag: data.tag, icon: '/icon-192.png', badge: '/icon-192.png', data: { url: '/' } }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
    const existing = clients.find(client => new URL(client.url).origin === self.location.origin);
    return existing ? existing.focus() : self.clients.openWindow('/');
  }));
});
