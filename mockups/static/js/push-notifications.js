// push-notifications.js
// Módulo para manejar suscripciones push en el cliente
// Debe cargarse en las páginas de camioneros

(function() {
    'use strict';

    // Configuración (la clave pública se inyecta desde Django)
    const VAPID_PUBLIC_KEY = window.VAPID_PUBLIC_KEY || null;
    const PUSH_SUBSCRIBE_URL = '/push/subscribe/';
    const PUSH_UNSUBSCRIBE_URL = '/push/unsubscribe/';

    // ─────────────────────────────────────────────────
    // UTILIDADES
    // ─────────────────────────────────────────────────
    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/\-/g, '+')
            .replace(/_/g, '/');

        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);

        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // ─────────────────────────────────────────────────
    // VERIFICACIÓN DE SOPORTE
    // ─────────────────────────────────────────────────
    function checkPushSupport() {
        if (!('serviceWorker' in navigator)) {
            console.warn('[Push] Service Workers no soportados');
            return false;
        }
        if (!('PushManager' in window)) {
            console.warn('[Push] Push API no soportada');
            return false;
        }
        if (!('Notification' in window)) {
            console.warn('[Push] Notifications API no soportada');
            return false;
        }
        return true;
    }

    // ─────────────────────────────────────────────────
    // SOLICITAR PERMISO
    // ─────────────────────────────────────────────────
    async function requestNotificationPermission() {
        const permission = await Notification.requestPermission();
        console.log('[Push] Permiso de notificaciones:', permission);
        return permission === 'granted';
    }

    // ─────────────────────────────────────────────────
    // SUSCRIBIR A PUSH
    // ─────────────────────────────────────────────────
    async function subscribeToPush() {
        if (!checkPushSupport()) {
            return { success: false, error: 'Push notifications no soportadas en este navegador' };
        }

        if (!VAPID_PUBLIC_KEY) {
            console.error('[Push] VAPID_PUBLIC_KEY no configurada');
            return { success: false, error: 'Configuración de push incompleta' };
        }

        try {
            // Solicitar permiso
            const hasPermission = await requestNotificationPermission();
            if (!hasPermission) {
                return { success: false, error: 'Permiso de notificaciones denegado' };
            }

            // Obtener registro del service worker
            const registration = await navigator.serviceWorker.ready;
            console.log('[Push] Service Worker listo:', registration.scope);

            // Verificar si ya existe una suscripción
            let subscription = await registration.pushManager.getSubscription();
            
            if (!subscription) {
                // Crear nueva suscripción
                const applicationServerKey = urlBase64ToUint8Array(VAPID_PUBLIC_KEY);
                subscription = await registration.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: applicationServerKey
                });
                console.log('[Push] Nueva suscripción creada');
            } else {
                console.log('[Push] Suscripción existente encontrada');
            }

            // Enviar suscripción al servidor
            const response = await fetch(PUSH_SUBSCRIBE_URL, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    endpoint: subscription.endpoint,
                    keys: {
                        p256dh: btoa(String.fromCharCode.apply(null, new Uint8Array(subscription.getKey('p256dh')))),
                        auth: btoa(String.fromCharCode.apply(null, new Uint8Array(subscription.getKey('auth'))))
                    }
                })
            });

            const data = await response.json();
            
            if (response.ok && data.success) {
                console.log('[Push] Suscripción guardada en servidor');
                return { success: true, message: 'Notificaciones activadas correctamente' };
            } else {
                console.error('[Push] Error guardando suscripción:', data.error);
                return { success: false, error: data.error || 'Error al guardar suscripción' };
            }

        } catch (error) {
            console.error('[Push] Error en suscripción:', error);
            return { success: false, error: error.message };
        }
    }

    // ─────────────────────────────────────────────────
    // DESUSCRIBIR DE PUSH
    // ─────────────────────────────────────────────────
    async function unsubscribeFromPush() {
        try {
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();
            
            if (subscription) {
                // Notificar al servidor
                await fetch(PUSH_UNSUBSCRIBE_URL, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    },
                    body: JSON.stringify({
                        endpoint: subscription.endpoint
                    })
                });

                // Cancelar suscripción local
                await subscription.unsubscribe();
                console.log('[Push] Desuscrito correctamente');
                return { success: true, message: 'Notificaciones desactivadas' };
            }
            
            return { success: true, message: 'No había suscripción activa' };

        } catch (error) {
            console.error('[Push] Error al desuscribir:', error);
            return { success: false, error: error.message };
        }
    }

    // ─────────────────────────────────────────────────
    // VERIFICAR ESTADO DE SUSCRIPCIÓN
    // ─────────────────────────────────────────────────
    async function checkSubscriptionStatus() {
        if (!checkPushSupport()) {
            return { supported: false, subscribed: false, permission: 'unsupported' };
        }

        try {
            const permission = Notification.permission;
            const registration = await navigator.serviceWorker.ready;
            const subscription = await registration.pushManager.getSubscription();
            
            return {
                supported: true,
                subscribed: !!subscription,
                permission: permission
            };
        } catch (error) {
            console.error('[Push] Error verificando estado:', error);
            return { supported: true, subscribed: false, permission: 'error' };
        }
    }

    // ─────────────────────────────────────────────────
    // INICIALIZACIÓN AUTOMÁTICA
    // ─────────────────────────────────────────────────
    async function initPushNotifications() {
        if (!checkPushSupport()) {
            console.log('[Push] Notificaciones push no soportadas');
            return;
        }

        // Esperar a que el service worker esté listo
        const registration = await navigator.serviceWorker.ready;
        console.log('[Push] Service Worker activo:', registration.active?.state);

        // Verificar estado actual
        const status = await checkSubscriptionStatus();
        console.log('[Push] Estado actual:', status);

        // Si es camionero y tiene permiso pero no está suscrito, suscribir automáticamente
        if (status.permission === 'granted' && !status.subscribed && window.AUTO_SUBSCRIBE_PUSH) {
            console.log('[Push] Auto-suscribiendo...');
            await subscribeToPush();
        }
    }

    // ─────────────────────────────────────────────────
    // UI HELPERS
    // ─────────────────────────────────────────────────
    function updatePushButton(button, isSubscribed) {
        if (!button) return;
        
        if (isSubscribed) {
            button.classList.remove('btn-primary');
            button.classList.add('btn-success');
            button.innerHTML = '<i class="bi bi-bell-fill me-2"></i>Notificaciones Activas';
            button.dataset.subscribed = 'true';
        } else {
            button.classList.remove('btn-success');
            button.classList.add('btn-primary');
            button.innerHTML = '<i class="bi bi-bell me-2"></i>Activar Notificaciones';
            button.dataset.subscribed = 'false';
        }
    }

    async function handlePushButtonClick(button) {
        const wasSubscribed = button.dataset.subscribed === 'true';
        
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Procesando...';
        
        let result;
        if (wasSubscribed) {
            result = await unsubscribeFromPush();
        } else {
            result = await subscribeToPush();
        }
        
        button.disabled = false;
        
        if (result.success) {
            updatePushButton(button, !wasSubscribed);
            showToast(result.message, 'success');
        } else {
            updatePushButton(button, wasSubscribed);
            showToast(result.error, 'danger');
        }
    }

    function showToast(message, type = 'info') {
        // Usar el sistema de toasts de Bootstrap si existe
        const toastContainer = document.getElementById('toast-container');
        if (toastContainer) {
            const toast = document.createElement('div');
            toast.className = `toast align-items-center text-bg-${type} border-0`;
            toast.setAttribute('role', 'alert');
            toast.innerHTML = `
                <div class="d-flex">
                    <div class="toast-body">${message}</div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            `;
            toastContainer.appendChild(toast);
            const bsToast = new bootstrap.Toast(toast);
            bsToast.show();
            toast.addEventListener('hidden.bs.toast', () => toast.remove());
        } else {
            // Fallback a alert
            alert(message);
        }
    }

    // ─────────────────────────────────────────────────
    // EXPONER API GLOBAL
    // ─────────────────────────────────────────────────
    window.PushNotifications = {
        subscribe: subscribeToPush,
        unsubscribe: unsubscribeFromPush,
        checkStatus: checkSubscriptionStatus,
        init: initPushNotifications,
        updateButton: updatePushButton,
        handleButtonClick: handlePushButtonClick,
        isSupported: checkPushSupport
    };

    // Auto-inicializar cuando el DOM esté listo
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPushNotifications);
    } else {
        initPushNotifications();
    }

})();
