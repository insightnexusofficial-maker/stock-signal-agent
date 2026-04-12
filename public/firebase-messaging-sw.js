importScripts('https://www.gstatic.com/firebasejs/10.7.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.0/firebase-messaging-compat.js');

firebase.initializeApp({
    apiKey: "AIzaSyALQUtr9qGDdoqj-Jdwrkw3XQpxBuQ7joQ",
    authDomain: "stock-sayo.firebaseapp.com",
    projectId: "stock-sayo"
});

const messaging = firebase.messaging();

// 백그라운드 푸시 알림 수신
messaging.onBackgroundMessage((payload) => {
    console.log('백그라운드 메시지:', payload);
    
    const title = payload.notification?.title || '주식 사여?!';
    const options = {
        body: payload.notification?.body || '매수 시그널 발생!',
        icon: '/icon-192.png',
        badge: '/icon-72.png',
        vibrate: [200, 100, 200],
        data: payload.data
    };
    
    self.registration.showNotification(title, options);
});

// 알림 클릭
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow('https://stock-sayo.web.app')
    );
});