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
