// swing_end(React CRM) 쪽 서비스 워커와 동일한 Firebase 프로젝트(athlepa) 설정.
// 이 데모 페이지 자체 오리진(athlepa-demo.netlify.app)에서 알림을 구독하려면
// 이 사이트 루트에도 똑같은 서비스 워커가 따로 있어야 한다 (서비스 워커는
// 오리진마다 별도로 등록해야 함).
importScripts("https://www.gstatic.com/firebasejs/12.18.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/12.18.0/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyCPuVPbLsrUPDYxnUhdvRtxI3ByrnUU5dw",
  authDomain: "athlepa.firebaseapp.com",
  projectId: "athlepa",
  storageBucket: "athlepa.firebasestorage.app",
  messagingSenderId: "332733557227",
  appId: "1:332733557227:web:0f3be7f3c0fcde3e0111ae",
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  const { title, body, image } = payload.notification || {};
  self.registration.showNotification(title || "ATHLEPA 알림", { body, icon: image });
});
