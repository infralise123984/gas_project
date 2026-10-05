"""Vistas HTTP — notificaciones in-app del telefonista."""

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from mockups.services.notificaciones import VENTANA_MAXIMA, eventos_recientes_telefonista
from mockups.utils.permisos import require_roles_api


audit_logger = logging.getLogger('audit')


# Telefonista: avisos efimeros de sus propios pedidos
@login_required
@never_cache
def notificaciones_telefonista_api(request):
    """Avisos in-app del telefonista sobre los pedidos que registro.

    GET /notificaciones/api/?desde=<iso>

    `desde` es el timestamp de "ultima vez visto" que envia el cliente (localStorage).
    Solo se devuelven eventos posteriores a ese instante. Sin `desde` se responde vacio
    (linea base): asi, al abrir la app no se inunda con avisos viejos; solo aparecen los
    eventos nuevos desde la ultima consulta.
    """
    resp = require_roles_api(request, ['telefonista'])
    if resp:
        return resp

    ahora = timezone.now()
    desde_raw = (request.GET.get('desde') or '').strip()
    if not desde_raw:
        # Linea base: el cliente toma `server_time` para fijar su marca de agua sin
        # depender del reloj del navegador.
        return JsonResponse({'count': 0, 'items': [], 'server_time': ahora.isoformat()})

    desde = parse_datetime(desde_raw)
    if desde is None:
        return JsonResponse({'error': 'Parametro desde invalido'}, status=400)
    if timezone.is_naive(desde):
        desde = timezone.make_aware(desde)

    # Acota la ventana para no escanear historial demasiado antiguo.
    if desde < ahora - VENTANA_MAXIMA:
        desde = ahora - VENTANA_MAXIMA

    items = eventos_recientes_telefonista(request.user, desde=desde)
    return JsonResponse({'count': len(items), 'items': items, 'server_time': ahora.isoformat()})
