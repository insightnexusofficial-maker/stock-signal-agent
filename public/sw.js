const CACHE_NAME = 'stock-sayo-v1';
const urlsToCache = ['/', '/index.html', '/manifest.json'];

// 캐시 설치
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
    );
});

// 캐시 응답
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request).then(response => response || fetch(event.request))
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