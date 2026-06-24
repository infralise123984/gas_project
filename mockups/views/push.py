"""Vistas HTTP — push."""

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse

from mockups.models import PushSubscription
from mockups.push_notifications import test_push_notification
from mockups.utils.permisos import require_roles_api


security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')




# ══════════════════════════════════════════════════════════════
# PUSH NOTIFICATIONS - VISTAS PARA SUSCRIPCIONES WEB PUSH
# ══════════════════════════════════════════════════════════════

@login_required
def push_subscribe(request):
    """
    Guarda o actualiza la suscripción push del usuario actual.
    Solo permite suscripciones a camioneros (por ahora).
    Endpoint: POST /push/subscribe/
    """
    # Log de debug
    audit_logger.info(f"PUSH_SUBSCRIBE_ATTEMPT | User: {request.user.username} | Rol: {request.user.rol} | Method: {request.method}")
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    
    # Solo camioneros pueden suscribirse (por ahora)
    resp = require_roles_api(request, ['camionero'])
    if resp:
        return resp
    
    try:
        import json
        data = json.loads(request.body)
        
        endpoint = data.get('endpoint', '')[:500]  # Truncar a 500 caracteres (límite del modelo)
        keys = data.get('keys', {})
        p256dh = keys.get('p256dh')
        auth = keys.get('auth')
        
        audit_logger.info(f"PUSH_SUBSCRIBE_DATA | Endpoint length: {len(data.get('endpoint', ''))} | Has p256dh: {bool(p256dh)} | Has auth: {bool(auth)}")
        
        if not all([endpoint, p256dh, auth]):
            return JsonResponse({
                'success': False, 
                'error': 'Datos de suscripción incompletos'
            }, status=400)
        
        # Crear o actualizar suscripción
        subscription, created = PushSubscription.objects.update_or_create(
            usuario=request.user,
            endpoint=endpoint,
            defaults={
                'p256dh': p256dh,
                'auth': auth,
                'activa': True,
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500]
            }
        )
        
        action = 'creada' if created else 'actualizada'
        audit_logger.info(f"PUSH_SUBSCRIBE | User: {request.user.username} | Suscripción {action}")
        
        return JsonResponse({
            'success': True,
            'message': f'Suscripción {action} correctamente',
            'subscription_id': subscription.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)
    except Exception as e:
        audit_logger.error(f"PUSH_SUBSCRIBE_ERROR | User: {request.user.username} | Error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Error interno del servidor'}, status=500)



# Usuario: desuscribirse de notificaciones
@login_required
def push_unsubscribe(request):
    """Desactivar suscripción push del usuario. Endpoint: POST /push/unsubscribe/."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        endpoint = data.get('endpoint')
        
        if endpoint:
            # Desactivar suscripción específica
            updated = PushSubscription.objects.filter(
                usuario=request.user,
                endpoint=endpoint
            ).update(activa=False)
        else:
            # Desactivar todas las suscripciones del usuario
            updated = PushSubscription.objects.filter(
                usuario=request.user
            ).update(activa=False)
        
        audit_logger.info(f"PUSH_UNSUBSCRIBE | User: {request.user.username} | {updated} suscripciones desactivadas")
        
        return JsonResponse({
            'success': True,
            'message': 'Notificaciones desactivadas',
            'count': updated
        })
        
    except Exception as e:
        audit_logger.error(f"PUSH_UNSUBSCRIBE_ERROR | User: {request.user.username} | Error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Error interno del servidor'}, status=500)



# Usuario: probar enviar notificación push
@login_required
def push_test(request):
    """Enviar notificación de prueba al usuario actual. Endpoint: POST /push/test/."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

    resp = require_roles_api(request, ['camionero'])
    if resp:
        return resp
    
    success, message = test_push_notification(request.user)
    
    return JsonResponse({
        'success': success,
        'message': message
    })



# Usuario: ver estado de suscripción push
@login_required  
def push_status(request):
    """Estado de suscripciones push del usuario. Endpoint: GET /push/status/."""
    from django.conf import settings
    
    suscripciones = PushSubscription.objects.filter(
        usuario=request.user,
        activa=True
    )
    
    return JsonResponse({
        'success': True,
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', None),
        'has_subscriptions': suscripciones.exists(),
        'subscription_count': suscripciones.count(),
        'subscriptions': [
            {
                'id': s.id,
                'device': 'Móvil' if 'Mobile' in s.user_agent else 'Desktop',
                'created': s.creada_el.isoformat()
            }
            for s in suscripciones
        ]
    })



# PWA: servir service worker para notificaciones offline
def service_worker(request):
    """Sirve el Service Worker desde la raíz para scope '/' y notificaciones push en todo el sitio."""
    import os
    from django.conf import settings as django_settings
    
    # Leer el archivo sw.js desde static
    sw_path = os.path.join(django_settings.BASE_DIR, 'mockups', 'static', 'sw.js')
    
    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            sw_content = f.read()
    except FileNotFoundError:
        return HttpResponse('Service Worker not found', status=404)
    
    response = HttpResponse(sw_content, content_type='application/javascript')
    # Headers importantes para Service Workers
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

