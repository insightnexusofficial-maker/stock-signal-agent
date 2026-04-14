const CACHE_NAME = 'stock-sayo-v2';
const urlsToCache = ['/', '/index.html', '/manifest.json'];

// 캐시 설치
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
    );
    self.skipWaiting();
});

// 새 서비스워커 즉시 활성화
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))
        )
    );
    self.clients.claim();
});

// 네트워크 우선 응답 (실패 시 캐시 fallback)
self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;

    event.respondWith((async () => {
        try {
            const networkResponse = await fetch(event.request);
            const cache = await caches.open(CACHE_NAME);
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
        } catch (e) {
            const cachedResponse = await caches.match(event.request);
            if (cachedResponse) return cachedResponse;
            throw e;
        }
    })());
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