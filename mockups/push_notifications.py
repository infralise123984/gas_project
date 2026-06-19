"""
mockups/push_notifications.py

Módulo para gestión de notificaciones push con Web Push API.
Permite enviar notificaciones a camioneros sobre nuevos pedidos y recordatorios.
"""
import json
import logging
import time
from django.conf import settings

try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_AVAILABLE = True
except ImportError:
    WEBPUSH_AVAILABLE = False
    webpush = None
    WebPushException = Exception

audit_logger = logging.getLogger('audit')


def _is_expired_subscription_error(error, error_msg=''):
    """Detecta suscripciones push expiradas aunque pywebpush no entregue siempre response.status_code."""
    response = getattr(error, 'response', None)
    status_code = getattr(response, 'status_code', None)
    if status_code in [404, 410]:
        return True

    texto = f"{error_msg} {error}".lower()
    patrones = [
        'subscription_expired',
        '410 gone',
        '404 not found',
        'no such subscription',
        'error":"gone"',
        'errno":106',
    ]
    return any(patron in texto for patron in patrones)


def _ubicacion_pedido(pedido):
    """
    Retorna la ubicación legible para notificaciones.
    Prioriza dirección de entrega y usa sector como fallback.
    """
    direccion = (pedido.direccion_entrega or '').strip()
    if direccion:
        return direccion

    sector = (pedido.sector or '').strip()
    if sector:
        return sector

    return 'Ubicación por confirmar'


def _resumen_ubicaciones_pedidos(pedidos_qs, max_items=3, max_len=38):
    """
    Genera un resumen corto de direcciones para notificaciones agregadas.
    """
    ubicaciones = []
    for pedido in pedidos_qs[:max_items]:
        texto = _ubicacion_pedido(pedido)
        if len(texto) > max_len:
            texto = f"{texto[:max_len - 1].rstrip()}..."
        ubicaciones.append(texto)

    if not ubicaciones:
        return 'Direcciones: sin detalle'

    return f"Direcciones: {', '.join(ubicaciones)}"


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
    # Tag único con timestamp para forzar heads-up en Android
    unique_tag = f"{tag}-{int(time.time() * 1000)}"
    
    payload = {
        'title': title,
        'body': body,
        'icon': '/static/img/web-app-manifest-192x192.png',
        'badge': '/static/img/favicon-96x96.png',
        'tag': unique_tag,
        'renotify': True,  # Vuelve a notificar aunque tenga el mismo tag
        'requireInteraction': True,  # Mantiene la notificación visible hasta que el usuario interactúe
        'timestamp': int(time.time() * 1000),  # Timestamp para prioridad
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
                'TTL': '86400'  # 24 h: tolera teléfonos dormidos o sin señal en ruta
            }
        )
        # Log sin emojis para evitar error de encoding en Windows
        title_log = title.encode('ascii', 'ignore').decode('ascii') or 'Notificacion'
        endpoint_hint = subscription_info.get('endpoint', '')[:50]
        audit_logger.info(
            f"PUSH_SENT | Title: {title_log} | TTL: 86400 | Endpoint: {endpoint_hint}..."
        )
        return True, None
        
    except WebPushException as e:
        error_msg = str(e)
        title_log = title.encode('ascii', 'ignore').decode('ascii') or 'Notificacion'
        endpoint_hint = subscription_info.get('endpoint', '')[:50]
        audit_logger.warning(
            f"PUSH_FAILED | Title: {title_log} | Endpoint: {endpoint_hint}... | Error: {error_msg}"
        )
        
        # Si la suscripción expiró o es inválida, retornar código específico
        if _is_expired_subscription_error(e, error_msg):
            return False, 'subscription_expired'
        
        return False, error_msg
    except Exception as e:
        error_msg = str(e)
        audit_logger.error(f"PUSH_ERROR | Title: {title} | Error: {error_msg}")
        if _is_expired_subscription_error(e, error_msg):
            return False, 'subscription_expired'
        return False, error_msg


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
    
    body = f"📍 {_ubicacion_pedido(pedido)}\n{items}"
    url = "/entregas/"  # Vista de entregas del camionero
    tag = f"pedido-{pedido.id}-{int(time.time())}"
    
    enviados = 0
    errores_suscripcion = []
    errores_detalle = []
    
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
            ua_resumido = (sub.user_agent or 'sin_user_agent')[:80]
            errores_detalle.append(f"{sub.usuario.username}|{ua_resumido}")
        elif error:
            ua_resumido = (sub.user_agent or 'sin_user_agent')[:80]
            audit_logger.warning(
                f"PUSH_PEDIDO_FAIL | #{pedido.id} | User: {sub.usuario.username} | "
                f"UA: {ua_resumido} | Error: {error}"
            )
    
    # Desactivar suscripciones expiradas
    if errores_suscripcion:
        PushSubscription.objects.filter(id__in=errores_suscripcion).update(activa=False)
        afectados = '; '.join(errores_detalle[:10])
        audit_logger.info(
            f"PUSH_CLEANUP | Desactivadas {len(errores_suscripcion)} suscripciones expiradas | Afectados: {afectados}"
        )
    
    audit_logger.info(
        f"PUSH_PEDIDO | #{pedido.id} | Origen: {pedido.origen} | "
        f"{enviados}/{suscripciones.count()} camioneros notificados"
    )
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
    ).select_related('usuario')
    
    if not suscripciones.exists():
        return {'enviados': 0, 'pedidos': pedidos_pendientes.count()}
    
    # Mensaje de recordatorio
    count = pedidos_pendientes.count()
    title = f'⏰ {count} pedido{"s" if count > 1 else ""} pendiente{"s" if count > 1 else ""}'
    
    body = _resumen_ubicaciones_pedidos(pedidos_pendientes)
    if count > 3:
        body += " y más..."
    
    enviados = 0
    errores = []
    errores_detalle = []
    
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
            ua_resumido = (sub.user_agent or 'sin_user_agent')[:80]
            errores_detalle.append(f"{sub.usuario.username}|{ua_resumido}")
    
    # Limpiar suscripciones expiradas
    if errores:
        PushSubscription.objects.filter(id__in=errores).update(activa=False)
        afectados = '; '.join(errores_detalle[:10])
        audit_logger.info(
            f"PUSH_CLEANUP_RECORDATORIO | Desactivadas {len(errores)} suscripciones expiradas | Afectados: {afectados}"
        )
    
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
