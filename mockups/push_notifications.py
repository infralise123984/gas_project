"""
mockups/push_notifications.py

Módulo para gestión de notificaciones push con Web Push API.
Permite enviar notificaciones a camioneros sobre nuevos pedidos y recordatorios.
"""
import json
import logging
from django.conf import settings

try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_AVAILABLE = True
except ImportError:
    WEBPUSH_AVAILABLE = False
    webpush = None
    WebPushException = Exception

audit_logger = logging.getLogger('audit')


def get_vapid_keys():
    """
    Obtiene las claves VAPID desde settings.
    Retorna None si no están configuradas.
    """
    return {
        'public_key': getattr(settings, 'VAPID_PUBLIC_KEY', None),
        'private_key': getattr(settings, 'VAPID_PRIVATE_KEY', None),
        'admin_email': getattr(settings, 'VAPID_ADMIN_EMAIL', 'mailto:admin@example.com'),
    }


def send_push_notification(subscription_info, title, body, url='/', tag='gasfacil', extra_data=None):
    """
    Envía una notificación push a una suscripción específica.
    
    Args:
        subscription_info: Dict con endpoint, keys.p256dh y keys.auth
        title: Título de la notificación
        body: Cuerpo del mensaje
        url: URL a abrir al hacer click
        tag: Identificador único de la notificación
        extra_data: Datos adicionales para incluir
    
    Returns:
        Tuple (success: bool, error_message: str or None)
    """
    if not WEBPUSH_AVAILABLE:
        return False, 'pywebpush no está instalado'
    
    vapid = get_vapid_keys()
    if not vapid['public_key'] or not vapid['private_key']:
        return False, 'Claves VAPID no configuradas'
    
    # Construir payload de la notificación
    payload = {
        'title': title,
        'body': body,
        'icon': '/static/img/web-app-manifest-192x192.png',
        'badge': '/static/img/favicon-96x96.png',
        'tag': tag,
        'renotify': True,  # Vuelve a notificar aunque tenga el mismo tag
        'requireInteraction': True,  # Mantiene la notificación visible hasta que el usuario interactúe
        'vibrate': [200, 100, 200, 100, 200],  # Patrón de vibración
        'data': {
            'url': url,
            **(extra_data or {})
        },
        'actions': [
            {'action': 'ver', 'title': 'Ver detalle'},
            {'action': 'cerrar', 'title': 'Cerrar'}
        ]
    }
    
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid['private_key'],
            vapid_claims={
                'sub': vapid['admin_email']
            },
            headers={
                'Urgency': 'high',  # Prioridad alta para heads-up notification
                'TTL': '60'  # Tiempo de vida: 60 segundos
            }
        )
        # Log sin emojis para evitar error de encoding en Windows
        title_log = title.encode('ascii', 'ignore').decode('ascii') or 'Notificacion'
        audit_logger.info(f"PUSH_SENT | Title: {title_log} | To: {subscription_info.get('endpoint', '')[:50]}...")
        return True, None
        
    except WebPushException as e:
        error_msg = str(e)
        title_log = title.encode('ascii', 'ignore').decode('ascii') or 'Notificacion'
        audit_logger.warning(f"PUSH_FAILED | Title: {title_log} | Error: {error_msg}")
        
        # Si la suscripción expiró o es inválida, retornar código específico
        if e.response and e.response.status_code in [404, 410]:
            return False, 'subscription_expired'
        
        return False, error_msg
    except Exception as e:
        audit_logger.error(f"PUSH_ERROR | Title: {title} | Error: {str(e)}")
        return False, str(e)


def notificar_nuevo_pedido(pedido):
    """
    Notifica a todos los camioneros activos sobre un nuevo pedido pendiente.
    Se usa cuando un telefonista crea un pedido de teléfono/domicilio.
    
    Args:
        pedido: Instancia del modelo Pedido
    """
    from .models import PushSubscription, Usuario
    
    # Solo notificar pedidos pendientes de domicilio
    if pedido.estado != 'pendiente' or pedido.origen not in ['telefono', 'tarreo']:
        return 0
    
    # Obtener suscripciones activas de camioneros
    suscripciones = PushSubscription.objects.filter(
        usuario__rol='camionero',
        usuario__is_active=True,
        activa=True
    ).select_related('usuario')
    
    if not suscripciones.exists():
        audit_logger.info(f"PUSH_NO_SUBS | Pedido #{pedido.id} - No hay camioneros suscritos")
        return 0
    
    # Construir mensaje
    title = '🚚 ¡Nuevo Pedido!'
    
    # Resumen del pedido
    detalles = pedido.detalles.all()
    if detalles:
        items = ', '.join([f"{d.cantidad}x {d.balon.nombre}" for d in detalles[:3]])
        if detalles.count() > 3:
            items += f" (+{detalles.count() - 3} más)"
    else:
        items = "Ver detalles"
    
    body = f"📍 {pedido.sector}\n{items}"
    url = "/entregas/"  # Vista de entregas del camionero
    # Tag único con timestamp para forzar heads-up en Android
    import time
    tag = f"pedido-{pedido.id}-{int(time.time())}"
    
    enviados = 0
    errores_suscripcion = []
    
    for sub in suscripciones:
        subscription_info = {
            'endpoint': sub.endpoint,
            'keys': {
                'p256dh': sub.p256dh,
                'auth': sub.auth
            }
        }
        
        success, error = send_push_notification(
            subscription_info=subscription_info,
            title=title,
            body=body,
            url=url,
            tag=tag,
            extra_data={'pedido_id': pedido.id}
        )
        
        if success:
            enviados += 1
        elif error == 'subscription_expired':
            errores_suscripcion.append(sub.id)
    
    # Desactivar suscripciones expiradas
    if errores_suscripcion:
        PushSubscription.objects.filter(id__in=errores_suscripcion).update(activa=False)
        audit_logger.info(f"PUSH_CLEANUP | Desactivadas {len(errores_suscripcion)} suscripciones expiradas")
    
    audit_logger.info(f"PUSH_PEDIDO | #{pedido.id} -> {enviados}/{suscripciones.count()} camioneros notificados")
    return enviados


def notificar_recordatorio_pendientes():
    """
    Envía recordatorios a camioneros sobre pedidos pendientes.
    Diseñado para ejecutarse periódicamente (ej: cada 30 minutos) via cron o Celery.
    
    Returns:
        Dict con estadísticas de envío
    """
    from .models import Pedido, PushSubscription
    from django.utils import timezone
    from datetime import timedelta
    
    # Obtener pedidos pendientes de más de 5 minutos
    limite = timezone.now() - timedelta(minutes=5)
    pedidos_pendientes = Pedido.objects.filter(
        estado='pendiente',
        origen__in=['telefono', 'tarreo'],
        fecha__lt=limite
    ).order_by('fecha')
    
    if not pedidos_pendientes.exists():
        return {'enviados': 0, 'pedidos': 0}
    
    # Obtener suscripciones activas
    suscripciones = PushSubscription.objects.filter(
        usuario__rol='camionero',
        usuario__is_active=True,
        activa=True
    )
    
    if not suscripciones.exists():
        return {'enviados': 0, 'pedidos': pedidos_pendientes.count()}
    
    # Mensaje de recordatorio
    count = pedidos_pendientes.count()
    title = f'⏰ {count} pedido{"s" if count > 1 else ""} pendiente{"s" if count > 1 else ""}'
    
    # Listar sectores únicos
    sectores = list(pedidos_pendientes.values_list('sector', flat=True).distinct()[:3])
    body = f"Sectores: {', '.join(sectores)}"
    if len(sectores) < pedidos_pendientes.values_list('sector', flat=True).distinct().count():
        body += " y más..."
    
    enviados = 0
    errores = []
    
    for sub in suscripciones:
        subscription_info = {
            'endpoint': sub.endpoint,
            'keys': {
                'p256dh': sub.p256dh,
                'auth': sub.auth
            }
        }
        
        success, error = send_push_notification(
            subscription_info=subscription_info,
            title=title,
            body=body,
            url='/entregas/',
            tag='recordatorio-pendientes',
            extra_data={'tipo': 'recordatorio', 'count': count}
        )
        
        if success:
            enviados += 1
        elif error == 'subscription_expired':
            errores.append(sub.id)
    
    # Limpiar suscripciones expiradas
    if errores:
        PushSubscription.objects.filter(id__in=errores).update(activa=False)
    
    audit_logger.info(f"PUSH_RECORDATORIO | {count} pedidos -> {enviados} notificaciones enviadas")
    
    return {
        'enviados': enviados,
        'pedidos': count,
        'suscripciones_activas': suscripciones.count()
    }


def test_push_notification(user):
    """
    Envía una notificación de prueba al usuario especificado.
    Útil para verificar que las notificaciones funcionan.
    
    Args:
        user: Usuario al que enviar la prueba
    
    Returns:
        Tuple (success: bool, message: str)
    """
    from .models import PushSubscription
    
    suscripcion = PushSubscription.objects.filter(
        usuario=user,
        activa=True
    ).first()
    
    if not suscripcion:
        return False, 'No tienes una suscripción activa'
    
    subscription_info = {
        'endpoint': suscripcion.endpoint,
        'keys': {
            'p256dh': suscripcion.p256dh,
            'auth': suscripcion.auth
        }
    }
    
    success, error = send_push_notification(
        subscription_info=subscription_info,
        title='🔔 Prueba de notificación',
        body='¡Las notificaciones funcionan correctamente!',
        url='/',
        tag='test-notification'
    )
    
    if success:
        return True, 'Notificación de prueba enviada'
    else:
        return False, f'Error: {error}'
