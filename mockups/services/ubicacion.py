"""Lógica de negocio: ubicación de los camiones y tramos de movimiento.

Qué resuelve y por qué así:

* **El cálculo vive en el servidor, no en el teléfono.** El criterio de qué es
  "estar detenido" se ajusta sin recompilar el APK, igual que la jornada
  (`UBICACION_JORNADA_*`). La app solo reporta coordenadas.
* **Tramos, no puntos.** A la telefonista no le sirve una nube de posiciones:
  le sirve leer "09:40-10:25 detenido en Av. Colón". Un tramo es una secuencia
  de muestras del mismo tipo, y eso es lo que se guarda en la bitácora.
* **"Sin señal" es un estado propio.** Si el teléfono se apagó, se cerró la app
  o el camión entró a un bache, no se movió *ni dejó de moverse*: no hay dato.
  Confundirlo con "detenido" inventaría una parada que nadie observó.
* **La precisión manda.** Una muestra con error de GPS de 500 m al lado de otra
  de 10 m fabrica un desplazamiento que nunca ocurrió. Se descartan antes de
  calcular; sin dato de precisión la muestra se acepta, porque "no sé" no es
  "está mal".

Funciones puras salvo las dos que consultan la base: se pueden probar sin
levantar Django más allá de la configuración.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Iterable, NamedTuple, Optional, Sequence

from django.conf import settings
from django.utils import timezone

from mockups.models import UbicacionCamion
from mockups.utils.fechas import rango_dia_chile

audit_logger = logging.getLogger('audit')

#: Tipos de tramo. Son valores de presentación: el panel los pinta y la bitácora
#: los lista; no se guardan en base (se recalculan desde las muestras).
MOVIMIENTO = 'movimiento'
DETENIDO = 'detenido'
SIN_SENAL = 'sin_senal'

#: Radio medio terrestre en metros, para la fórmula del semiverseno.
RADIO_TIERRA_M = 6_371_000.0


class Parametros(NamedTuple):
    """Criterios de cálculo, inyectados en vez de leídos dentro de cada función.

    Se pasan explícitos para que los tests fijen umbrales sin tocar `settings`.
    """

    min_desplazamiento_m: int
    precision_maxima_m: int
    minutos_sin_senal: int
    minutos_detenido: int


class Tramo(NamedTuple):
    """Un período continuo en movimiento, detenido, o sin datos."""

    inicio: datetime
    fin: datetime
    tipo: str
    lat: Optional[float]
    lng: Optional[float]

    @property
    def minutos(self) -> int:
        """Duración del tramo, redondeada a minutos enteros."""
        return int(round((self.fin - self.inicio).total_seconds() / 60))


class EstadoCamion(NamedTuple):
    """Lo que el panel necesita para pintar un camión ahora mismo.

    ``minutos`` se lee según el estado: en ``detenido`` es cuánto lleva parado;
    en ``movimiento`` y ``sin_senal`` es cuánto hace de la última muestra. Es
    un solo campo porque el panel lo usa siempre como antigüedad del dato.
    """

    estado: str
    minutos: Optional[int]
    lat: Optional[float]
    lng: Optional[float]
    desde: Optional[datetime]


def parametros_actuales() -> Parametros:
    """Lee los criterios de `settings` (único punto que toca configuración)."""
    return Parametros(
        min_desplazamiento_m=settings.UBICACION_MIN_DESPLAZAMIENTO_M,
        precision_maxima_m=settings.UBICACION_PRECISION_MAXIMA_M,
        minutos_sin_senal=settings.UBICACION_MINUTOS_SIN_SENAL,
        minutos_detenido=settings.UBICACION_MINUTOS_DETENIDO,
    )


def desplazamiento_m(lat_a, lng_a, lat_b, lng_b) -> float:
    """Distancia en metros entre dos coordenadas (fórmula del semiverseno).

    Suficiente para decidir "se movió o no": a las distancias que importan acá
    (decenas o cientos de metros) el error frente a una fórmula geodésica es
    despreciable.
    """
    rad = math.radians
    diferencia_lat = rad(float(lat_b) - float(lat_a))
    diferencia_lng = rad(float(lng_b) - float(lng_a))
    a = (
        math.sin(diferencia_lat / 2) ** 2
        + math.cos(rad(float(lat_a)))
        * math.cos(rad(float(lat_b)))
        * math.sin(diferencia_lng / 2) ** 2
    )
    return 2 * RADIO_TIERRA_M * math.asin(math.sqrt(a))


#: Metros por grado de latitud (constante suficiente para la escala de una ciudad).
METROS_POR_GRADO = 111_320.0


def _distancia_al_segmento_m(punto, inicio, fin) -> float:
    """Distancia del punto al segmento ``inicio-fin``, en metros.

    Se proyecta a un plano local escalado por la latitud: a escala de ciudad el
    error frente a una fórmula sobre esfera es irrelevante para decidir si un
    punto aporta forma a la ruta.
    """
    escala_lng = METROS_POR_GRADO * math.cos(math.radians(punto[0]))
    px, py = punto[1] * escala_lng, punto[0] * METROS_POR_GRADO
    ax, ay = inicio[1] * escala_lng, inicio[0] * METROS_POR_GRADO
    bx, by = fin[1] * escala_lng, fin[0] * METROS_POR_GRADO

    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplificar_ruta(puntos: Sequence, tolerancia_m: float) -> list:
    """Reduce una ruta a los puntos que le dan forma (Douglas-Peucker).

    Sin esto, el panel recibiría cientos de posiciones por camión en cada
    refresco para dibujar una línea que se ve igual con una cuarta parte. Los
    extremos siempre se conservan, así que el inicio y el fin del recorrido no
    se pierden.

    Iterativo y no recursivo a propósito: una jornada larga son cientos de
    puntos y la recursión clásica se anida tanto como la longitud de la ruta.
    """
    if len(puntos) <= 2:
        return list(puntos)

    conservar = [False] * len(puntos)
    conservar[0] = conservar[-1] = True

    pendientes = [(0, len(puntos) - 1)]
    while pendientes:
        inicio, fin = pendientes.pop()
        if fin <= inicio + 1:
            continue

        peor_distancia = 0.0
        peor_indice = None
        for indice in range(inicio + 1, fin):
            distancia = _distancia_al_segmento_m(
                puntos[indice], puntos[inicio], puntos[fin]
            )
            if distancia > peor_distancia:
                peor_distancia = distancia
                peor_indice = indice

        if peor_indice is not None and peor_distancia > tolerancia_m:
            conservar[peor_indice] = True
            pendientes.append((inicio, peor_indice))
            pendientes.append((peor_indice, fin))

    return [punto for punto, se_queda in zip(puntos, conservar) if se_queda]


def muestras_utiles(muestras: Iterable, parametros: Parametros) -> list:
    """Ordena por momento y descarta las muestras con precisión inservible."""
    utiles = [
        muestra
        for muestra in muestras
        if muestra.precision_m is None
        or float(muestra.precision_m) <= parametros.precision_maxima_m
    ]
    return sorted(utiles, key=lambda muestra: muestra.capturado_el)


def _tramo(desde, hasta, tipo: str) -> Tramo:
    return Tramo(
        inicio=desde.capturado_el,
        fin=hasta.capturado_el,
        tipo=tipo,
        lat=float(desde.lat),
        lng=float(desde.lng),
    )


def _tipo_entre(anterior, siguiente, parametros: Parametros) -> str:
    """Clasifica el intervalo entre dos muestras consecutivas."""
    minutos = (siguiente.capturado_el - anterior.capturado_el).total_seconds() / 60
    if minutos > parametros.minutos_sin_senal:
        return SIN_SENAL
    distancia = desplazamiento_m(
        anterior.lat, anterior.lng, siguiente.lat, siguiente.lng
    )
    return MOVIMIENTO if distancia > parametros.min_desplazamiento_m else DETENIDO


def calcular_tramos(muestras: Iterable, parametros: Parametros) -> list:
    """Convierte una lista de muestras en tramos contiguos del mismo tipo.

    Cada tramo toma la posición de su primera muestra: para un tramo detenido
    eso es exactamente "dónde estaba parado", que es el dato que se lee.
    """
    utiles = muestras_utiles(muestras, parametros)
    if not utiles:
        return []

    tramos: list = []
    tipo_actual: Optional[str] = None
    inicio = utiles[0]
    ultimo = utiles[0]

    for anterior, siguiente in zip(utiles, utiles[1:]):
        tipo = _tipo_entre(anterior, siguiente, parametros)

        if tipo == SIN_SENAL:
            # El tramo en curso termina donde se acabaron los datos; el hueco
            # se registra como tal y el siguiente tramo arranca en la muestra
            # con la que se recuperó la señal.
            if tipo_actual is not None:
                tramos.append(_tramo(inicio, anterior, tipo_actual))
            tramos.append(_tramo(anterior, siguiente, SIN_SENAL))
            tipo_actual = None
            inicio = siguiente
        elif tipo_actual is None:
            tipo_actual, inicio = tipo, anterior
        elif tipo != tipo_actual:
            tramos.append(_tramo(inicio, anterior, tipo_actual))
            tipo_actual, inicio = tipo, anterior

        ultimo = siguiente

    if tipo_actual is not None:
        tramos.append(_tramo(inicio, ultimo, tipo_actual))

    return tramos


def estado_actual(
    tramos: Sequence[Tramo],
    *,
    ahora: datetime,
    parametros: Parametros,
    ultima_muestra=None,
) -> EstadoCamion:
    """Estado del camión ahora, a partir de sus tramos de hoy.

    Sin muestras, o con la última demasiado vieja, el estado es ``sin_senal``:
    el panel no debe pintar en verde un camión del que no sabe nada. Aun así se
    conserva la última posición conocida —en gris y con la antigüedad a la
    vista—: para la telefonista "estaba acá hace 44 minutos" responde mucho
    mejor que un mapa vacío.

    ``ultima_muestra`` cubre el caso de una posición suelta: un camión que acaba
    de arrancar tiene una sola muestra, y con ella aún no se puede formar un
    tramo. Sin este parámetro desaparecería del panel en vez de aparecer como
    detenido, que es lo que sus datos dicen.
    """
    if not tramos:
        return _estado_de_muestra_suelta(ultima_muestra, ahora, parametros)

    ultimo = tramos[-1]
    minutos_desde_ultima = (ahora - ultimo.fin).total_seconds() / 60

    if ultimo.tipo == SIN_SENAL or minutos_desde_ultima > parametros.minutos_sin_senal:
        return EstadoCamion(SIN_SENAL, int(minutos_desde_ultima), ultimo.lat, ultimo.lng, ultimo.fin)

    if ultimo.tipo == MOVIMIENTO:
        return EstadoCamion(MOVIMIENTO, int(minutos_desde_ultima), ultimo.lat, ultimo.lng, ultimo.fin)

    # Detenido: la antigüedad de la parada es la del tramo entero, y sigue
    # corriendo mientras no llegue una muestra nueva.
    minutos = ultimo.minutos + int(minutos_desde_ultima)
    return EstadoCamion(DETENIDO, minutos, ultimo.lat, ultimo.lng, ultimo.inicio)


def _estado_de_muestra_suelta(ultima_muestra, ahora: datetime, parametros: Parametros) -> EstadoCamion:
    """Estado a partir de una única posición, sin tramo que la respalde."""
    if ultima_muestra is None:
        return EstadoCamion(SIN_SENAL, None, None, None, None)

    minutos = (ahora - ultima_muestra.capturado_el).total_seconds() / 60
    lat, lng = float(ultima_muestra.lat), float(ultima_muestra.lng)

    if minutos > parametros.minutos_sin_senal:
        # Dato viejo, pero la posición sigue siendo la última conocida.
        return EstadoCamion(SIN_SENAL, int(minutos), lat, lng, ultima_muestra.capturado_el)

    # Hay dato fresco pero ningún intervalo medido: no consta que se moviera, así
    # que se informa detenido con la posición, no en verde.
    return EstadoCamion(DETENIDO, 0, lat, lng, ultima_muestra.capturado_el)


def tramos_de_camionero(
    trabajador, *, desde: datetime, hasta: datetime, parametros: Optional[Parametros] = None
) -> list:
    """Tramos del camionero en el rango indicado, leídos de la base."""
    parametros = parametros or parametros_actuales()
    muestras = UbicacionCamion.objects.filter(
        trabajador=trabajador,
        capturado_el__gte=desde,
        capturado_el__lt=hasta,
    ).order_by('capturado_el')
    return calcular_tramos(muestras, parametros)


def tramos_de_hoy(trabajador, *, ahora: Optional[datetime] = None, parametros=None) -> list:
    """Tramos de la jornada en curso (día de Chile)."""
    ahora = ahora or timezone.localtime()
    inicio, fin = rango_dia_chile(ahora.date())
    return tramos_de_camionero(
        trabajador, desde=inicio, hasta=fin, parametros=parametros
    )


def estado_de_flota(camioneros: Sequence, *, ahora: Optional[datetime] = None):
    """Estado de cada camionero en una sola pasada por la base.

    Una consulta para todos y no una por camionero: el panel se refresca cada
    30 s y con diez camiones serían diez consultas por refresco.
    """
    ahora = ahora or timezone.localtime()
    parametros = parametros_actuales()
    inicio, fin = rango_dia_chile(ahora.date())

    ids = [camionero.pk for camionero in camioneros]
    por_camionero: dict = {pk: [] for pk in ids}
    muestras = UbicacionCamion.objects.filter(
        trabajador_id__in=ids,
        capturado_el__gte=inicio,
        capturado_el__lt=fin,
    ).order_by('capturado_el')

    for muestra in muestras:
        por_camionero.setdefault(muestra.trabajador_id, []).append(muestra)

    resultado = []
    for camionero in camioneros:
        # La consulta viene ordenada por `capturado_el`: el último de la lista es
        # la posición más reciente.
        muestras_camionero = por_camionero.get(camionero.pk, [])
        utiles = muestras_utiles(muestras_camionero, parametros)
        tramos = calcular_tramos(utiles, parametros)
        resultado.append(
            {
                'camionero': camionero,
                'tramos': tramos,
                # El recorrido del día, ya simplificado: es lo que dibuja la
                # polilínea del panel. Se calcula sobre las mismas muestras
                # buenas, así que una lectura con error grande no deforma la ruta.
                'rastro': simplificar_ruta(
                    [(float(muestra.lat), float(muestra.lng)) for muestra in utiles],
                    settings.UBICACION_RASTRO_TOLERANCIA_M,
                ),
                'estado': estado_actual(
                    tramos,
                    ahora=ahora,
                    parametros=parametros,
                    ultima_muestra=utiles[-1] if utiles else None,
                ),
            }
        )
    return resultado


def corte_de_retencion(ahora: Optional[datetime] = None) -> datetime:
    """Momento anterior al cual las muestras se consideran vencidas."""
    ahora = ahora or timezone.now()
    return ahora - timedelta(hours=settings.UBICACION_RETENCION_HORAS)


def podar_muestras_vencidas(ahora: Optional[datetime] = None) -> int:
    """Borra las muestras fuera de la ventana de retención.

    Se hace en la propia ingesta y no con un cron: el plan gratuito de Render no
    tiene tareas programadas fiables, y una tabla que solo crece es una bomba
    de tiempo silenciosa. Devuelve cuántas filas se borraron.
    """
    borradas, _ = UbicacionCamion.objects.filter(
        recibido_el__lt=corte_de_retencion(ahora)
    ).delete()
    if borradas:
        audit_logger.info(f'UBICACION_PODA | Muestras eliminadas: {borradas}')
    return borradas
