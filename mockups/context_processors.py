"""
mockups/context_processors.py

Procesadores de contexto para agregar variables globales a todos los templates.
"""
from django.conf import settings


def vapid_public_key(request):
    """
    Agrega la clave pública VAPID al contexto SOLO para camioneros.
    Los demás roles no necesitan exponer esta clave en cada página.
    """
    if request.user.is_authenticated and request.user.rol == 'camionero':
        return {
            'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        }
    return {'vapid_public_key': ''}


def bodega_context(request):
    """
    Agrega bodega_activa y bodegas_disponibles al contexto de todos los templates.
    Necesario para el navbar switcher de bodegas (admin/jefe).
    """
    if not request.user.is_authenticated:
        return {}

    from mockups.models import Bodega
    from mockups.utils.permisos import get_bodega_actual

    context = {}
    bodega = get_bodega_actual(request)
    if bodega:
        context['bodega_activa'] = bodega

    if request.user.rol in ('jefe', 'admin'):
        bodegas_qs = Bodega.objects.filter(activo=True)
        if request.user.bodega:
            bodegas_qs = bodegas_qs | Bodega.objects.filter(id=request.user.bodega_id)
        context['bodegas_disponibles'] = bodegas_qs.distinct()

    return context
