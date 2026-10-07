"""Sobre único de respuesta y catálogo de errores del API v1 (docs/API_MOVIL.md §5).

Única razón para cambiar: que cambie el contrato del sobre o el catálogo de
códigos. Ningún endpoint debe construir su propio JSON a mano.
"""

from django.http import JsonResponse

from mockups.utils.fechas import now_chile

# código estable -> (status HTTP por defecto, mensaje por defecto para el usuario).
# El mensaje es corto, en español y SIN detalles internos: el cliente decide con
# el código, nunca leyendo el texto (docs/API_MOVIL.md §5.2).
CATALOGO_ERRORES = {
    'validacion': (400, 'Revisa los datos enviados.'),
    'no_autenticado': (401, 'Debes iniciar sesión.'),
    'sin_permiso': (403, 'No tienes permiso para esta acción.'),
    'no_encontrado': (404, 'El recurso no existe.'),
    'metodo_no_permitido': (405, 'Método no permitido para esta ruta.'),
    'no_disponible': (409, 'El pedido ya no está disponible o ya fue tomado.'),
    'conflicto_estado': (409, 'El pedido cambió de estado y la acción ya no aplica.'),
    'bloqueado_login': (429, 'Demasiados intentos fallidos. Vuelve a intentarlo más tarde.'),
    'demasiadas_peticiones': (429, 'Demasiadas peticiones seguidas. Espera un momento.'),
    'error_interno': (500, 'Ocurrió un error inesperado.'),
}


def _respuesta(payload, status, headers):
    response = JsonResponse(
        payload,
        status=status,
        json_dumps_params={'ensure_ascii': False},
    )
    # Nada del API se cachea: la app depende de `servidor_ahora` en cada respuesta.
    response['Cache-Control'] = 'no-store'
    for nombre, valor in (headers or {}).items():
        response[nombre] = valor
    return response


def ok(data, *, status=200, headers=None):
    """Sobre de éxito (§5.1)."""
    return _respuesta(
        {'servidor_ahora': now_chile().isoformat(), 'data': data},
        status,
        headers,
    )


def error(codigo, *, mensaje=None, status=None, headers=None):
    """Sobre de error (§5.2).

    `codigo` debe existir en CATALOGO_ERRORES; un código desconocido es un bug de
    programación y se levanta en desarrollo en vez de salir al cliente.
    """
    if codigo not in CATALOGO_ERRORES:
        raise ValueError(f'Código de error desconocido: {codigo}')

    status_por_defecto, mensaje_por_defecto = CATALOGO_ERRORES[codigo]
    return _respuesta(
        {
            'servidor_ahora': now_chile().isoformat(),
            'error': {
                'codigo': codigo,
                'mensaje': mensaje or mensaje_por_defecto,
            },
        },
        status or status_por_defecto,
        headers,
    )
