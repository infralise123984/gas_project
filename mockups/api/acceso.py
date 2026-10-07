"""Guardas de acceso y validación de entrada del API v1.

Por qué existe este módulo y no se usan los decoradores de la web:

* ``@login_required`` responde 302 hacia ``/auth/login/`` (``settings.LOGIN_URL``).
  Una app nativa no debe seguir redirecciones a HTML: el contrato exige
  ``401 no_autenticado`` con el sobre del §5.2.
* ``require_roles_api`` devuelve ``{"error": "No autorizado"}`` sin sobre. Se sigue
  usando —porque audita ``PERM_DENIED``— pero su salida se reformatea aquí.
"""

import json
import logging
from functools import wraps

from mockups.api import respuestas
from mockups.utils.permisos import require_roles_api

security_logger = logging.getLogger('security')

# Cuerpo máximo aceptado antes de parsear nada. Los tres únicos payloads del API
# (login, tarreo, nada más) son diminutos: cualquier cosa mayor es un abuso.
LIMITE_CUERPO_BYTES = 4096

CONTENT_TYPES_ACEPTADOS = (
    'application/json',
    'application/x-www-form-urlencoded',
)


def acceso_api(roles=None):
    """Exige sesión y, opcionalmente, rol.

    ``roles=None`` significa "cualquier usuario autenticado" (arranque de
    identidad: perfil, logout). Con roles, delega en ``require_roles_api`` para
    conservar la auditoría de ``PERM_DENIED``.

    Se comprueba la sesión ANTES del rol: ``require_roles_api`` lee
    ``request.user.rol``, que no existe en ``AnonymousUser``.
    """

    def decorador(vista):
        @wraps(vista)
        def envoltorio(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return respuestas.error('no_autenticado')
            if roles:
                denegado = require_roles_api(request, roles)
                if denegado is not None:
                    return respuestas.error('sin_permiso')
            return vista(request, *args, **kwargs)

        return envoltorio

    return decorador


def solo_post(vista):
    """405 en el sobre del §5.2, en lugar del 405 vacío de ``@require_POST``."""

    @wraps(vista)
    def envoltorio(request, *args, **kwargs):
        if request.method != 'POST':
            return respuestas.error('metodo_no_permitido')
        return vista(request, *args, **kwargs)

    return envoltorio


def cuerpo_json(request, *, permitidos):
    """Lee el cuerpo UNA vez y valida forma, tamaño y campos.

    Devuelve ``(datos, None)`` o ``(None, respuesta_de_error)``. Se rechaza:

    * cuerpo mayor que ``LIMITE_CUERPO_BYTES`` (antes de parsear);
    * ``Content-Type`` no soportado;
    * JSON inválido o que no sea un objeto;
    * cualquier campo que no esté en ``permitidos`` (un ``role`` colado en el
      payload de login no se ignora en silencio: se rechaza).
    """

    largo = request.META.get('CONTENT_LENGTH') or '0'
    try:
        if int(largo) > LIMITE_CUERPO_BYTES:
            return None, respuestas.error(
                'validacion', mensaje='El cuerpo de la petición es demasiado grande.'
            )
    except ValueError:
        return None, respuestas.error('validacion')

    if request.content_type not in CONTENT_TYPES_ACEPTADOS:
        return None, respuestas.error(
            'validacion', mensaje='Content-Type no soportado.'
        )

    if request.content_type == 'application/json':
        try:
            datos = json.loads(request.body or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, respuestas.error(
                'validacion', mensaje='El cuerpo no es JSON válido.'
            )
        if not isinstance(datos, dict):
            return None, respuestas.error('validacion')
    else:
        datos = {clave: request.POST[clave] for clave in request.POST}

    inesperados = sorted(set(datos) - set(permitidos))
    if inesperados:
        security_logger.warning(
            f'API_CAMPOS_INESPERADOS | Path: {request.path} | Campos: {inesperados[:5]}'
        )
        return None, respuestas.error(
            'validacion', mensaje='La petición incluye campos no permitidos.'
        )

    return datos, None
