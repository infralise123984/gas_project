"""Borde de errores del API v1: el 500 en el sobre del §5.2 (docs/API_MOVIL.md §10 #13).

Un fallo inesperado en una vista de la web lo resuelve Django con su página HTML.
En ``/api/v1/`` eso rompe el contrato: la app recibe algo que no puede parsear y
se queda sin ``servidor_ahora``. Acá el 500 del API sale como JSON.

Por qué ``handler500`` y no un ``try/except`` por vista:

* un solo lugar cubre toda ruta presente y **futura** (si mañana se agrega un
  endpoint y nadie se acuerda de envolverlo, igual queda cubierto);
* la vista no gana bloques que atrapen de más: la excepción sigue subiendo con
  su traza, que es lo que quieren el log y el depurador;
* los errores de negocio (cuerpo mal formado, ``DoesNotExist``) se siguen
  manejando donde están, junto a la validación que los explica.

Alcance honesto: no atrapa fallos anteriores a la vista (middleware, resolución
de URL) ni convierte en sobre los estados que Django devuelve sin excepción
(``400`` por ``SuspiciousOperation``, ``413``). Con ``DEBUG=True`` el handler no
corre: en desarrollo se ve la página técnica de Django, que es lo útil.
"""

import logging
import sys

from django.http import JsonResponse
from django.views.defaults import server_error

from mockups.api import respuestas

security_logger = logging.getLogger('security')

PREFIJO_API = '/api/v1/'

# Respuesta de último recurso: si ni el sobre se puede armar (por ejemplo, zona
# horaria del servidor mal configurada), el handler no puede volver a fallar.
SOBRE_MINIMO = {'error': {'codigo': 'error_interno', 'mensaje': 'Ocurrió un error inesperado.'}}


def _registrar(request):
    """Rastro interno acotado: evento, ruta, usuario y clase de excepción.

    Nunca se registra el cuerpo del request (puede traer credenciales) ni el
    mensaje de la excepción: la traza completa ya la deja Django en el logger
    ``django.request``, que va a la salida del servidor.
    """
    usuario = getattr(request, 'user', None)
    tipo = getattr(sys.exc_info()[0], '__name__', 'desconocida')
    security_logger.error(
        f'API_ERROR_INTERNO | Path: {request.path} | '
        f'User: {getattr(usuario, "pk", None)} | Excepcion: {tipo}'
    )


def handler500(request):
    """500 del API con el sobre del §5.2; la web conserva su página de Django."""
    if not request.path.startswith(PREFIJO_API):
        return server_error(request)

    try:
        _registrar(request)
    except Exception:
        # El log no puede tumbar la respuesta de error.
        pass

    try:
        return respuestas.error('error_interno')
    except Exception:
        return JsonResponse(SOBRE_MINIMO, status=500)
