"""Ingesta de la posición del camión (docs/API_MOVIL.md §7.8).

Es **transporte + validación**: normaliza y filtra lo que manda el teléfono y
delega el cálculo de tramos al servicio (`mockups/services/ubicacion.py`).

Decisiones que importan:

* **Llega en lotes.** Sin señal la app acumula muestras y las vacía al recuperar
  conexión; el tope del lote es `UBICACION_MAX_MUESTRAS`. Por eso este endpoint
  es el único del API que sube el techo de tamaño de cuerpo: un lote ronda los
  18 KB y el límite global es de 4 KB.
* **Idempotente por clave natural** ``(trabajador, capturado_el)``. Si el
  teléfono reintenta el mismo lote —lo hace tras cada corte de red— las muestras
  ya guardadas se ignoran en vez de duplicarse. La garantía la da la restricción
  única del modelo, no una comprobación optimista.
* **La jornada se valida acá, no en la app.** El teléfono no es autoridad sobre
  cuándo se puede rastrear: fuera de la ventana la muestra se descarta aunque la
  app se empeñe en enviarla.
* **Nunca se registra una coordenada.** Los logs dicen cuántas muestras
  entraron, no dónde estaba el camionero: un archivo de texto con el recorrido
  de una persona es exactamente lo que hay que evitar.
"""

import logging
import math
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from mockups.api import respuestas
from mockups.api.acceso import ROLES_CAMIONERO, acceso_api, cuerpo_json, solo_post
from mockups.api.limites import limitar
from mockups.models import UbicacionCamion
from mockups.services.ubicacion import podar_muestras_vencidas

audit_logger = logging.getLogger('audit')
security_logger = logging.getLogger('security')

CAMPOS_LOTE = ('muestras',)
CAMPOS_MUESTRA = ('lat', 'lng', 'precision_m', 'velocidad', 'bateria', 'capturado_el')

# Cotas del modelo: `lat`/`lng` son decimales de 9 enteros y 6 decimales.
DECIMALES_COORDENADA = 6
LATITUD_MAXIMA = Decimal('90')
LONGITUD_MAXIMA = Decimal('180')

# Un error de GPS mayor a esto no sirve para decidir si el camión se movió, y
# una velocidad imposible solo puede venir de una muestra corrupta.
PRECISION_MAXIMA_ACEPTADA_M = 100_000
VELOCIDAD_MAXIMA_M_S = 150

# El campo `bateria` del modelo es un entero pequeño sin signo.
BATERIA_MAXIMA = 100


def _decimal(valor, *, tope: Decimal):
    """Convierte a ``Decimal`` cuantizado a la precisión del modelo, o ``None``.

    Se cuantiza acá y no al guardar porque el motor decide distinto qué hacer
    con los decimales sobrantes: unos redondean y otros fallan.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    if not numero.is_finite() or not -tope <= numero <= tope:
        return None
    return numero.quantize(Decimal(1).scaleb(-DECIMALES_COORDENADA))


def _no_negativo(valor, *, tope: float):
    """Número real acotado entre 0 y ``tope``, o ``None``."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero):
        return None
    if numero < 0 or numero > tope:
        return None
    return numero


def _momento(valor):
    """Instante aware. Sin zona horaria se asume la de Chile.

    La app puede mandar un ``datetime`` local sin offset (Dart lo hace con
    ``toIso8601String()``). Rechazarlo dejaría el canal inservible; interpretarlo
    como hora de Chile es una decisión explícita, y el desfase se detecta
    comparando con ``recibido_el``. Un momento en el futuro no se acepta: el
    reloj del teléfono iría adelantado y ensuciaría la bitácora.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None
    momento = parse_datetime(valor.strip())
    if momento is None:
        return None
    if timezone.is_naive(momento):
        momento = timezone.make_aware(momento, timezone.get_current_timezone())
    if momento > timezone.now() + timedelta(minutes=1):
        return None
    return momento


def _muestra_normalizada(cruda):
    """Muestra lista para guardar, o ``None`` si no es utilizable.

    Un campo desconocido invalida la muestra: aceptar y descartar en silencio
    dejaría a la app creyendo que envía algo que el servidor ignora.
    """
    if not isinstance(cruda, dict):
        return None

    lat = _decimal(cruda.get('lat'), tope=LATITUD_MAXIMA)
    lng = _decimal(cruda.get('lng'), tope=LONGITUD_MAXIMA)
    capturado_el = _momento(cruda.get('capturado_el'))
    if lat is None or lng is None or capturado_el is None:
        return None

    normalizada = {'lat': lat, 'lng': lng, 'capturado_el': capturado_el}

    if cruda.get('precision_m') is not None:
        precision = _no_negativo(cruda.get('precision_m'), tope=PRECISION_MAXIMA_ACEPTADA_M)
        if precision is None:
            return None
        normalizada['precision_m'] = precision

    if cruda.get('velocidad') is not None:
        velocidad = _no_negativo(cruda.get('velocidad'), tope=VELOCIDAD_MAXIMA_M_S)
        if velocidad is None:
            return None
        normalizada['velocidad'] = velocidad

    if cruda.get('bateria') is not None:
        bateria = _no_negativo(cruda.get('bateria'), tope=BATERIA_MAXIMA)
        if bateria is None:
            return None
        normalizada['bateria'] = int(bateria)

    return normalizada


def _en_jornada(momento) -> bool:
    """¿El instante cae dentro de la jornada configurada, en hora de Chile?"""
    hora = timezone.localtime(momento).hour
    return settings.UBICACION_JORNADA_INICIO <= hora < settings.UBICACION_JORNADA_FIN


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('ubicacion')
def registrar_ubicacion(request):
    """Guarda el lote de posiciones del camionero autenticado (§7.8)."""
    datos, error_respuesta = cuerpo_json(
        request,
        permitidos=CAMPOS_LOTE,
        limite_bytes=settings.UBICACION_LIMITE_CUERPO_BYTES,
    )
    if error_respuesta is not None:
        return error_respuesta

    crudas = datos.get('muestras')
    if not isinstance(crudas, list) or not crudas:
        return respuestas.error(
            'validacion', mensaje='Falta la lista de posiciones o está vacía.'
        )
    if len(crudas) > settings.UBICACION_MAX_MUESTRAS:
        return respuestas.error(
            'validacion',
            mensaje=f'El lote no puede superar {settings.UBICACION_MAX_MUESTRAS} posiciones.',
        )

    utiles = []
    invalidas = 0
    fuera_de_jornada = 0
    for cruda in crudas:
        muestra = _muestra_normalizada(cruda)
        if muestra is None:
            invalidas += 1
            continue
        if not _en_jornada(muestra['capturado_el']):
            fuera_de_jornada += 1
            continue
        utiles.append(muestra)

    # Se descartan antes de insertar para poder informar cuántas eran repetidas:
    # `ignore_conflicts` protege la integridad, pero no cuenta.
    momentos = [muestra['capturado_el'] for muestra in utiles]
    ya_guardadas = set(
        UbicacionCamion.objects.filter(
            trabajador=request.user, capturado_el__in=momentos
        ).values_list('capturado_el', flat=True)
    )
    nuevas = [muestra for muestra in utiles if muestra['capturado_el'] not in ya_guardadas]

    if nuevas:
        UbicacionCamion.objects.bulk_create(
            [
                UbicacionCamion(trabajador=request.user, **muestra)
                for muestra in nuevas
            ],
            ignore_conflicts=True,
        )
        # La retención se aplica acá y no con un cron: el plan gratuito no tiene
        # tareas programadas fiables, y una tabla que solo crece es una bomba de
        # tiempo. Solo cuando entra algo nuevo, para no pagarlo en cada POST.
        podar_muestras_vencidas()

    if fuera_de_jornada:
        # Sin coordenadas: el log dice que hubo intentos fuera de horario, no
        # dónde estaba el camionero a esa hora.
        security_logger.warning(
            f'UBICACION_FUERA_JORNADA | User: {request.user.username} | '
            f'Descartadas: {fuera_de_jornada}'
        )

    if invalidas:
        security_logger.warning(
            f'UBICACION_INVALIDA | User: {request.user.username} | Muestras: {invalidas}'
        )

    audit_logger.info(
        f'UBICACION_INGESTA | User: {request.user.username} | '
        f'Nuevas: {len(nuevas)} | Repetidas: {len(utiles) - len(nuevas)} | '
        f'Descartadas: {invalidas + fuera_de_jornada}'
    )
    return respuestas.ok(
        {
            'aceptadas': len(nuevas),
            'repetidas': len(utiles) - len(nuevas),
            'descartadas': invalidas + fuera_de_jornada,
        }
    )
