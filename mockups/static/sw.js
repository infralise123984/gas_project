// Service Worker para GasFácil - Notificaciones Push
// Este archivo debe estar en la raíz del scope de la PWA
// sw.js - Versión 1.2

const CACHE_NAME = 'gasfacil-v3';
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
self.addEventListener('pushsubscriptionchange', (event) => {
    console.warn('[SW] Suscripción push cambió o expiró');

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            clientList.forEach((client) => {
                client.postMessage({
                    type: 'PUSH_RESUBSCRIBE_REQUIRED'
                });
            });
        })
    );
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
});

console.log('[SW] Service Worker cargado');
