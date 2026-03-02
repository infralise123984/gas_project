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
