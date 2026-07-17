const CACHE_NAME = 'stock-sayo-v10';
const urlsToCache = ['/manifest.json'];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
    );
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys
                    .filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;
    
    const url = new URL(event.request.url);
    const isNavigation = event.request.mode === 'navigate';
    const isHtml = url.pathname === '/' || url.pathname.endsWith('.html');
    
    if (isNavigation || isHtml) {
        event.respondWith(
            fetch(event.request, { cache: 'no-store' })
                .catch(() => caches.match('/index.html'))
        );
        return;
    }
    
    event.respondWith(
        caches.match(event.request).then(response => {
            if (response) return response;
            return fetch(event.request).then(networkResponse => {
                if (!networkResponse || networkResponse.status !== 200) {
                    return networkResponse;
                }
                const responseToCache = networkResponse.clone();
                caches.open(CACHE_NAME).then(cache => cache.put(event.request, responseToCache));
                return networkResponse;
            });
        })
    );
});

// 푸시 알림 수신
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || '주식 사여?!';
    const options = {
        body: data.body || '매수 시그널 발생!',
        icon: '/icon-192.png',
        badge: '/icon-192.png',
        vibrate: [200, 100, 200],
        data: data.url || '/'
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

// 알림 클릭
self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data || '/')
    );
});
