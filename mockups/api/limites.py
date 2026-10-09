"""Política de límites del API v1 y su contador (docs/API_MOVIL.md §6.4).

Qué promete la política publicada, y qué no:

* **Algoritmo: ventana fija** por usuario. Es el rung más barato y el único que
  es correcto con la caché del proceso (LocMemCache). Se acepta sabiendo sus dos
  defectos, documentados en el contrato: hasta **2× el cupo en el borde** de la
  ventana (30 al final + 30 al principio) y *stampede*, porque los clientes
  bloqueados reintentan todos juntos en el reinicio.
* **Dos números, no uno**: el cupo sostenido (por minuto) y la ráfaga real, que
  con ventana fija es *el cupo completo en el primer instante*. No hay ráfaga
  adicional deliberada: el único pico legítimo (vaciar la cola offline cuando
  vuelve la señal) se resuelve en el cliente honrando `Retry-After` y con
  *backoff*; su duplicación de escrituras es el pendiente de idempotencia (§6.2).
* **Cabeceras en toda respuesta limitada**: trío legacy `X-RateLimit-*` más
  `X-RateLimit-Scope` (cuál de los cubos se contó). `X-RateLimit-Reset` son
  **segundos restantes**, nunca epoch. En el 429, `Retry-After` manda sobre
  `Reset` (regla del borrador IETF). Son señales de planificación, no un SLA:
  el servidor puede bajar un cupo por estabilidad.
* **Clave: el usuario autenticado**, nunca ``X-Forwarded-For``: esa cabecera la
  controla el cliente y usarla como clave permite evadir el límite rotándola. El
  límite se aplica SIEMPRE después de ``acceso_api``, así que en la práctica
  nunca hay clave anónima.
* ``cache.add`` fija el TTL en la MISMA operación que crea la clave. Un
  ``INCR`` seguido de un ``EXPIRE`` (patrón habitual con Redis) deja la clave
  viva para siempre si el proceso muere entre ambas llamadas.
* Si la caché falla, se **falla abierto** (con aviso en el logger ``security``):
  se prioriza que el camionero pueda ver sus pedidos por sobre el límite.

Alcance honesto: esto **no** es protección DDoS —la saturación de red se resuelve
en infraestructura— y no hay *override* por cuenta: la API tiene una sola
audiencia (camioneros), así que subir un cupo es cambiar esta tabla y el §6.4 en
el mismo commit. Como ``settings.CACHES`` no está definido, el contador es **por
instancia**: con más de una, el cupo real se multiplica (§10).
"""

import logging
import math
import time
from functools import wraps
from typing import NamedTuple

from django.core.cache import cache

from mockups.api import respuestas

security_logger = logging.getLogger('security')


class Cupo(NamedTuple):
    """Cupo publicado de un alcance.

    ``peticiones``/``ventana_segundos`` es el cupo **sostenido**. La ráfaga no
    se declara aparte porque con ventana fija es el cupo completo en el primer
    instante: inventarle un número propio sería publicar algo que el contador no
    puede cumplir.
    """

    peticiones: int
    ventana_segundos: int


# Tabla ÚNICA de cupos: es lo que publica el §6.4, así que los dos lados se
# cambian juntos y no puede haber drift entre doc y enforcement.
POLITICA = {
    # Lectura del panel: la consulta más frecuente de la app.
    'entregas': Cupo(60, 60),
    # Las cuatro acciones comparten cubo: alternarlas no multiplica el cupo y
    # mueven estado y dinero, así que van más estrechas que el listado.
    'acciones_entregas': Cupo(30, 60),
    # Crea dinero, igual que las acciones.
    'tarreo': Cupo(30, 60),
    # Catálogo: lectura, como el listado.
    'balones': Cupo(60, 60),
    # Una pasada por la actividad del día.
    'resumen_hoy': Cupo(60, 60),
    # La consulta más cara del API: recorre el mes entero día por día.
    'historial': Cupo(20, 60),
    # Arranque de identidad.
    'perfil': Cupo(30, 60),
    # El APK se consulta al arrancar; no hay razón para preguntarlo seguido.
    'version': Cupo(10, 60),
    # Alta o baja del teléfono: pasa al iniciar y al cerrar sesión, no más.
    'dispositivos': Cupo(10, 60),
}


def cupo_de(alcance):
    """Cupo publicado de un alcance. Un alcance sin cupo es un bug de código."""
    try:
        return POLITICA[alcance]
    except KeyError:
        raise ValueError(f'Alcance de límite sin cupo publicado: {alcance}')


class EstadoLimite(NamedTuple):
    """Lo que hace falta para decidir y para contestar en las cabeceras."""

    permitida: bool
    restantes: int
    reinicio_segundos: int


def _claves(alcance, identificador):
    raiz = f'api.v1.limite.{alcance}.{identificador}'
    return raiz, f'{raiz}.inicio'


def _segundos_para_el_reinicio(clave_inicio, ventana_segundos, ahora):
    """Lo que de verdad queda de la ventana, no la ventana completa."""
    inicio = cache.get(clave_inicio, ahora)
    return max(1, math.ceil(ventana_segundos - (ahora - inicio)))


def consumir(alcance, identificador, peticiones, ventana_segundos):
    """Cuenta una petición y devuelve su ``EstadoLimite``."""
    clave, clave_inicio = _claves(alcance, identificador)
    ahora = time.time()

    if cache.add(clave, 1, ventana_segundos):
        cache.add(clave_inicio, ahora, ventana_segundos)
        return EstadoLimite(True, max(0, peticiones - 1), ventana_segundos)

    try:
        usadas = cache.incr(clave)
    except ValueError:
        # La clave expiró entre el add y el incr (o alguien la borró): ventana nueva.
        cache.add(clave, 1, ventana_segundos)
        cache.add(clave_inicio, ahora, ventana_segundos)
        return EstadoLimite(True, max(0, peticiones - 1), ventana_segundos)

    reinicio = _segundos_para_el_reinicio(clave_inicio, ventana_segundos, ahora)
    if usadas <= peticiones:
        return EstadoLimite(True, max(0, peticiones - usadas), reinicio)
    return EstadoLimite(False, 0, reinicio)


def _cabeceras(alcance, cupo, estado):
    """Estado del cupo en las cabeceras del §6.4 (una respuesta, un estado)."""
    cabeceras = {
        'X-RateLimit-Limit': str(cupo.peticiones),
        'X-RateLimit-Remaining': str(estado.restantes),
        # Segundos restantes, NO epoch: decirlo en el contrato para que el
        # cliente no intente parsear una fecha.
        'X-RateLimit-Reset': str(estado.reinicio_segundos),
        'X-RateLimit-Scope': alcance,
    }
    if not estado.permitida:
        # El borrador IETF lo deja claro: `Retry-After` manda sobre el reinicio.
        cabeceras['Retry-After'] = str(estado.reinicio_segundos)
    return cabeceras


def limitar(alcance):
    """Aplica el cupo publicado del ``alcance``, por usuario autenticado.

    Toda respuesta del alcance sale con el trío `X-RateLimit-*`; el 429 además
    con `Retry-After`. Si el contador no se puede consultar, se falla abierto y
    se registra el hueco.
    """
    cupo = cupo_de(alcance)

    def decorador(vista):
        @wraps(vista)
        def envoltorio(request, *args, **kwargs):
            identificador = request.user.pk or 'anonimo'
            try:
                estado = consumir(
                    alcance,
                    identificador,
                    cupo.peticiones,
                    cupo.ventana_segundos,
                )
            except Exception:
                # Caché caída (store compartido, reinicio, backend mal
                # configurado). Se falla abierto y se registra el hueco sin
                # incluir datos del usuario más allá del alcance.
                security_logger.warning(f'API_LIMITE_SIN_CACHE | Alcance: {alcance}')
                return vista(request, *args, **kwargs)

            if not estado.permitida:
                security_logger.warning(
                    f'API_LIMITE_EXCEDIDO | Alcance: {alcance} | '
                    f'User: {request.user.username} | '
                    f'Espera: {estado.reinicio_segundos}s'
                )
                return respuestas.error(
                    'demasiadas_peticiones',
                    headers=_cabeceras(alcance, cupo, estado),
                )

            respuesta = vista(request, *args, **kwargs)
            for nombre, valor in _cabeceras(alcance, cupo, estado).items():
                respuesta[nombre] = valor
            return respuesta

        return envoltorio

    return decorador
