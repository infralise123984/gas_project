// Service Worker para GasFácil - Notificaciones Push
// Este archivo debe estar en la raíz del scope de la PWA
// sw.js - Versión 1.3

const CACHE_NAME = 'gasfacil-v5';

let cachedPushConfig = null;
const OFFLINE_URL = '/';

// Archivos a cachear para funcionamiento offline
const STATIC_CACHE_URLS = [
    '/',
    '/static/manifest.json',
    '/static/img/web-app-manifest-192x192.png',
    '/static/img/web-app-manifest-512x512.png',
];

// ─────────────────────────────────────────────────
// INSTALACIÓN DEL SERVICE WORKER
// ─────────────────────────────────────────────────
self.addEventListener('install', (event) => {
    console.log('[SW] Instalando Service Worker...');
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW] Cache abierto, precacheando...');
            return cache.addAll(STATIC_CACHE_URLS);
        })
    );
    // Activar inmediatamente sin esperar
    self.skipWaiting();
});

// ─────────────────────────────────────────────────
// ACTIVACIÓN DEL SERVICE WORKER
// ─────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
    console.log('[SW] Activando Service Worker...');
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('[SW] Limpiando cache antiguo:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    // Tomar control inmediato de todas las páginas
    self.clients.claim();
});

// ─────────────────────────────────────────────────
// PUSH NOTIFICATIONS - Recepción del mensaje push
// ─────────────────────────────────────────────────
self.addEventListener('push', (event) => {
    console.log('[SW] Push recibido:', event);

    let data = {
        title: 'GasFácil - Kim Gas',
        body: 'Tienes una nueva notificación',
        icon: '/static/img/web-app-manifest-192x192.png',
        badge: '/static/img/favicon-96x96.png',
        tag: 'gasfacil-notification',
        data: {
            url: '/',
        }
    };

    // Intentar parsear los datos del push
    if (event.data) {
        try {
            const pushData = event.data.json();
            data = {
                ...data,
                ...pushData,
            };
        } catch (e) {
            console.error('[SW] Error parseando push data:', e);
            data.body = event.data.text();
        }
    }

    const options = {
        body: data.body,
        icon: data.icon || '/static/img/web-app-manifest-192x192.png',
        badge: data.badge || '/static/img/favicon-96x96.png',
        tag: data.tag || `gasfacil-${Date.now()}`,
        renotify: true,
        timestamp: data.timestamp || Date.now(),
        data: data.data || { url: '/' },
        vibrate: [200, 100, 200],
        requireInteraction: true,
        silent: false,
        actions: [
            { action: 'ver', title: 'Ver detalle' },
            { action: 'cerrar', title: 'Cerrar' }
        ]
    };

    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

// ─────────────────────────────────────────────────
// CLICK EN LA NOTIFICACIÓN
// ─────────────────────────────────────────────────
self.addEventListener('notificationclick', (event) => {
    console.log('[SW] Notificación clickeada:', event);

    event.notification.close();

    const urlToOpen = event.notification.data?.url || '/';

    // Manejar acciones específicas
    if (event.action === 'cerrar') {
        return; // Solo cerrar, no abrir nada
    }

    // Abrir o enfocar la ventana de la app
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            // Buscar si ya hay una ventana abierta
            for (const client of clientList) {
                if (client.url.includes(self.registration.scope) && 'focus' in client) {
                    client.navigate(urlToOpen);
                    return client.focus();
                }
            }
            // Si no hay ventana abierta, abrir una nueva
            if (clients.openWindow) {
                return clients.openWindow(urlToOpen);
            }
        })
    );
});

// ─────────────────────────────────────────────────
// CIERRE DE NOTIFICACIÓN
// ─────────────────────────────────────────────────
self.addEventListener('notificationclose', (event) => {
    console.log('[SW] Notificación cerrada:', event.notification.tag);
});

// ─────────────────────────────────────────────────
// RENOVACIÓN DE SUSCRIPCIÓN PUSH
// ─────────────────────────────────────────────────
function urlBase64ToUint8Array(base64String) {
    if (!base64String) {
        return null;
    }
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

function subscriptionKeysToBase64(subscription) {
    const p256dh = subscription.getKey('p256dh');
    const auth = subscription.getKey('auth');
    return {
        p256dh: btoa(String.fromCharCode.apply(null, new Uint8Array(p256dh))),
        auth: btoa(String.fromCharCode.apply(null, new Uint8Array(auth))),
    };
}

async function resubscribePushInBackground() {
    if (!cachedPushConfig?.vapidPublicKey) {
        console.warn('[SW] Sin config push cacheada para re-suscripción en background');
        return false;
    }

    const applicationServerKey = urlBase64ToUint8Array(cachedPushConfig.vapidPublicKey);
    if (!applicationServerKey) {
        return false;
    }

    const registration = self.registration;
    const oldSubscription = await registration.pushManager.getSubscription();
    if (oldSubscription) {
        try {
            await oldSubscription.unsubscribe();
        } catch (error) {
            console.warn('[SW] No se pudo cancelar suscripción anterior:', error);
        }
    }

    const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
    });

    const response = await fetch('/push/subscribe/', {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': cachedPushConfig.csrfToken || '',
        },
        body: JSON.stringify({
            endpoint: subscription.endpoint,
            keys: subscriptionKeysToBase64(subscription),
        }),
    });

    if (!response.ok) {
        console.warn('[SW] Re-suscripción en background rechazada por servidor:', response.status);
        return false;
    }

    const data = await response.json();
    if (!data.success) {
        console.warn('[SW] Re-suscripción en background falló:', data.error || 'error desconocido');
        return false;
    }

    console.log('[SW] Re-suscripción en background completada');
    return true;
}

async function handlePushSubscriptionChange() {
    const clientList = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    clientList.forEach((client) => {
        client.postMessage({ type: 'PUSH_RESUBSCRIBE_REQUIRED' });
    });

    if (clientList.length > 0) {
        return;
    }

    try {
        await resubscribePushInBackground();
    } catch (error) {
        console.error('[SW] Error en re-suscripción background:', error);
    }
}

self.addEventListener('pushsubscriptionchange', (event) => {
    console.warn('[SW] Suscripción push cambió o expiró');
    event.waitUntil(handlePushSubscriptionChange());
});

// ─────────────────────────────────────────────────
// FETCH - Estrategia Network First con fallback a Cache
// ─────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
    // Solo cachear GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    // Ignorar requests a APIs externas
    if (!event.request.url.startsWith(self.location.origin)) {
        return;
    }

    const url = new URL(event.request.url);

    // No cachear rutas dinámicas de la app (solo estáticos y página offline)
    const isStaticAsset = url.pathname.startsWith('/static/');
    const isOfflineRoot = event.request.mode === 'navigate' && url.pathname === '/';
    if (!isStaticAsset && !isOfflineRoot) {
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Clonar la respuesta para guardar en cache
                if (response.status === 200) {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            })
            .catch(() => {
                // Fallback a cache si la red falla
                return caches.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    // Si no está en cache, mostrar página offline
                    if (event.request.mode === 'navigate') {
                        return caches.match(OFFLINE_URL);
                    }
                    return new Response('Offline', { status: 503 });
                });
            })
    );
});

// ─────────────────────────────────────────────────
// MENSAJE DESDE LA PÁGINA
// ─────────────────────────────────────────────────
self.addEventListener('message', (event) => {
    console.log('[SW] Mensaje recibido:', event.data);

    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }

    if (event.data && event.data.type === 'PUSH_CONFIG') {
        cachedPushConfig = {
            vapidPublicKey: event.data.vapidPublicKey || null,
            csrfToken: event.data.csrfToken || '',
            updatedAt: Date.now(),
        };
    }
});

console.log('[SW] Service Worker cargado');
