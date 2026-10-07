"""Control de abuso del API v1: ventana fija sobre la caché de Django.

Alcance honesto: esto **no** es protección DDoS. Frena bucles de cliente y
martilleo de un usuario autenticado; la saturación de red se resuelve en la capa
de infraestructura.

Decisiones deliberadas:

* La clave se deriva del **usuario autenticado**, nunca de ``X-Forwarded-For``:
  esa cabecera la controla el cliente y usarla como clave permite evadir el
  límite rotándola. El límite se aplica SIEMPRE después de ``acceso_api``, así
  que en la práctica nunca hay clave anónima.
* ``cache.add`` fija el TTL en la MISMA operación que crea la clave. Un
  ``INCR`` seguido de un ``EXPIRE`` (patrón habitual con Redis) deja la clave
  viva para siempre si el proceso muere entre ambas llamadas.
* Si la caché falla, se **falla abierto** (con aviso en el logger ``security``):
  se prioriza que el camionero pueda ver sus pedidos por sobre el límite. Es una
  decisión explícita, no un descuido.

No hay store compartido configurado (``settings.CACHES`` no está definido →
LocMemCache por proceso). Con más de una instancia, el límite pasa a ser por
instancia. Ver docs/API_MOVIL.md §10.
"""

import logging
import math
import time
from functools import wraps

from django.core.cache import cache

from mockups.api import respuestas

security_logger = logging.getLogger('security')


def _claves(alcance, identificador):
    raiz = f'api.v1.limite.{alcance}.{identificador}'
    return raiz, f'{raiz}.inicio'


def consumir(alcance, identificador, peticiones, ventana_segundos):
    """Cuenta una petición y decide si se permite.

    Devuelve ``(permitida, segundos_para_reintentar)``. Los segundos son los que
    realmente quedan de la ventana, no la ventana completa.
    """
    clave, clave_inicio = _claves(alcance, identificador)
    ahora = time.time()

    if cache.add(clave, 1, ventana_segundos):
        cache.add(clave_inicio, ahora, ventana_segundos)
        return True, 0

    try:
        usadas = cache.incr(clave)
    except ValueError:
        # La clave expiró entre el add y el incr (o alguien la borró): ventana nueva.
        cache.add(clave, 1, ventana_segundos)
        cache.add(clave_inicio, ahora, ventana_segundos)
        return True, 0

    if usadas <= peticiones:
        return True, 0

    inicio = cache.get(clave_inicio, ahora)
    espera = max(1, math.ceil(ventana_segundos - (ahora - inicio)))
    return False, espera


def limitar(alcance, peticiones, ventana_segundos):
    """Aplica ``consumir`` por usuario autenticado y responde 429 si se excede."""

    def decorador(vista):
        @wraps(vista)
        def envoltorio(request, *args, **kwargs):
            identificador = request.user.pk or 'anonimo'
            try:
                permitida, espera = consumir(
                    alcance, identificador, peticiones, ventana_segundos
                )
            except Exception:
                # Caché caída (store compartido, reinicio, backend mal
                # configurado). Se falla abierto y se registra el hueco sin
                # incluir datos del usuario más allá del alcance.
                security_logger.warning(f'API_LIMITE_SIN_CACHE | Alcance: {alcance}')
                return vista(request, *args, **kwargs)

            if not permitida:
                security_logger.warning(
                    f'API_LIMITE_EXCEDIDO | Alcance: {alcance} | '
                    f'User: {request.user.username} | Espera: {espera}s'
                )
                return respuestas.error(
                    'demasiadas_peticiones',
                    headers={'Retry-After': str(espera)},
                )

            return vista(request, *args, **kwargs)

        return envoltorio

    return decorador
