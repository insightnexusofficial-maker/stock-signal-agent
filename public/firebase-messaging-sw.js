importScripts('https://www.gstatic.com/firebasejs/10.7.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.0/firebase-messaging-compat.js');

firebase.initializeApp({
    apiKey: "AIzaSyALQUtr9qGDdoqj-Jdwrkw3XQpxBuQ7joQ",
    authDomain: "stock-sayo.firebaseapp.com",
    projectId: "stock-sayo",
    storageBucket: "stock-sayo.firebasestorage.app",
    messagingSenderId: "964666304071",
    appId: "1:964666304071:web:0c806002d44229f71e3362"
});

const messaging = firebase.messaging();

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

messaging.onBackgroundMessage((payload) => {
    console.log('백그라운드 메시지:', payload);
    
    const title = payload.notification?.title || '주식 사여?!';
    const options = {
        body: payload.notification?.body || '매수 시그널 발생!',
        icon: '/icon-192.png',
        badge: '/icon-72.png',
        vibrate: [200, 100, 200]
    };
    
    self.registration.showNotification(title, options);
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(clients.openWindow('https://stock-sayo.web.app'));
});
