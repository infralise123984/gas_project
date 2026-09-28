"""
mockups/context_processors.py

Procesadores de contexto para agregar variables globales a todos los templates.
"""
from django.conf import settings


def vapid_public_key(request):
    """
    Agrega la clave pública VAPID al contexto de todos los templates.
    Necesario para que el JavaScript de push notifications funcione.
    """
    return {
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
    }


def secciones_activas(request):
    """Expone los feature flags de secciones para que los templates oculten la UI.

    Conteo de balones: la sección existe pero no se muestra ni se enlaza hasta
    liberarla (``CONTEO_BALONES_HABILITADO``).
    """
    return {
        'conteo_balones_habilitado': getattr(settings, 'CONTEO_BALONES_HABILITADO', False),
    }
