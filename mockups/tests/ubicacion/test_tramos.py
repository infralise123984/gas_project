"""Tests del cálculo de tramos (``mockups/services/ubicacion.py``).

Son pruebas **puras**: no tocan la base ni levantan la pila HTTP. La lógica de
"se movió o no" es la que decide qué ve la telefonista, así que se prueba aislada
de la ingesta y del panel.

Ejecutar con:

    python manage.py test mockups.tests.ubicacion -v 2
"""

from datetime import datetime, timedelta, timezone as tz
from typing import NamedTuple, Optional

from django.test import SimpleTestCase

from mockups.services.ubicacion import (
    DETENIDO,
    MOVIMIENTO,
    SIN_SENAL,
    Parametros,
    calcular_tramos,
    desplazamiento_m,
    estado_actual,
    muestras_utiles,
    simplificar_ruta,
)

#: Umbrales fijos para que el test no dependa de `settings`.
PARAMETROS = Parametros(
    min_desplazamiento_m=100,
    precision_maxima_m=100,
    minutos_sin_senal=15,
    minutos_detenido=45,
)

#: Un punto en el centro de Santiago.
LAT, LNG = -33.450000, -70.660000

#: ~1112 m de desplazamiento en latitud (un grado son ~111,1 km).
UN_POBLADO_LEJOS = 0.010000


class Muestra(NamedTuple):
    """Muestra con la forma que consume el servicio, sin pasar por el ORM."""

    lat: float
    lng: float
    capturado_el: datetime
    precision_m: Optional[float] = None


def momento(minutos: int) -> datetime:
    """Instante base (09:00 UTC) desplazado en minutos."""
    return datetime(2026, 10, 14, 9, 0, tzinfo=tz.utc) + timedelta(minutes=minutos)


def quieto(minutos: int, *, precision: Optional[float] = 10.0) -> Muestra:
    return Muestra(LAT, LNG, momento(minutos), precision)


def movido(minutos: int, *, precision: Optional[float] = 10.0) -> Muestra:
    return Muestra(LAT + UN_POBLADO_LEJOS, LNG, momento(minutos), precision)


class DesplazamientoTest(SimpleTestCase):
    def test_distancia_conocida_en_latitud(self):
        metros = desplazamiento_m(LAT, LNG, LAT + 0.01, LNG)

        # Un grado de latitud son ~111,1 km; 0,01 grados son ~1111 m.
        self.assertAlmostEqual(metros, 1111, delta=15)

    def test_mismo_punto_no_se_desplaza(self):
        self.assertAlmostEqual(desplazamiento_m(LAT, LNG, LAT, LNG), 0.0, places=6)

    def test_es_simetrico(self):
        ida = desplazamiento_m(LAT, LNG, LAT + 0.01, LNG + 0.01)
        vuelta = desplazamiento_m(LAT + 0.01, LNG + 0.01, LAT, LNG)

        self.assertAlmostEqual(ida, vuelta, places=6)


class MuestrasUtilesTest(SimpleTestCase):
    def test_descarta_las_de_precision_inservible(self):
        muestras = [quieto(0), quieto(3, precision=500.0), quieto(6)]

        utiles = muestras_utiles(muestras, PARAMETROS)

        self.assertEqual(len(utiles), 2)

    def test_acepta_la_muestra_sin_precision(self):
        # "No sé la precisión" no es lo mismo que "está mal medida".
        utiles = muestras_utiles([quieto(0, precision=None)], PARAMETROS)

        self.assertEqual(len(utiles), 1)

    def test_ordena_por_momento(self):
        utiles = muestras_utiles([quieto(6), quieto(0), quieto(3)], PARAMETROS)

        self.assertEqual([m.capturado_el for m in utiles], [momento(0), momento(3), momento(6)])


class CalcularTramosTest(SimpleTestCase):
    def test_sin_muestras_no_hay_tramos(self):
        self.assertEqual(calcular_tramos([], PARAMETROS), [])

    def test_una_sola_muestra_no_alcanza_para_un_tramo(self):
        # Un tramo es un intervalo: hace falta un segundo punto.
        self.assertEqual(calcular_tramos([quieto(0)], PARAMETROS), [])

    def test_desplazamiento_grande_es_movimiento(self):
        tramos = calcular_tramos([quieto(0), movido(3)], PARAMETROS)

        self.assertEqual(len(tramos), 1)
        self.assertEqual(tramos[0].tipo, MOVIMIENTO)

    def test_quietud_es_detenido(self):
        tramos = calcular_tramos([quieto(0), quieto(3)], PARAMETROS)

        self.assertEqual(tramos[0].tipo, DETENIDO)

    def test_mismo_tipo_contiguo_se_fusiona_en_un_tramo(self):
        tramos = calcular_tramos([quieto(0), quieto(3), quieto(6)], PARAMETROS)

        self.assertEqual(len(tramos), 1)
        self.assertEqual(tramos[0].inicio, momento(0))
        self.assertEqual(tramos[0].fin, momento(6))
        self.assertEqual(tramos[0].minutos, 6)

    def test_cambio_de_tipo_cierra_el_tramo_anterior(self):
        tramos = calcular_tramos([quieto(0), quieto(3), movido(6)], PARAMETROS)

        self.assertEqual([tramo.tipo for tramo in tramos], [DETENIDO, MOVIMIENTO])

    def test_hueco_largo_se_marca_como_sin_senal(self):
        # 40 minutos sin muestras: no se movió, pero tampoco consta que siguiera
        # parado. Se reporta como hueco, no como parada.
        tramos = calcular_tramos([quieto(0), quieto(3), quieto(43), quieto(46)], PARAMETROS)

        self.assertEqual([tramo.tipo for tramo in tramos], [DETENIDO, SIN_SENAL, DETENIDO])
        self.assertEqual(tramos[1].inicio, momento(3))
        self.assertEqual(tramos[1].fin, momento(43))

    def test_el_hueco_recupera_el_tramo_en_la_muestra_siguiente(self):
        tramos = calcular_tramos([quieto(0), quieto(3), quieto(70), quieto(73)], PARAMETROS)

        self.assertEqual(tramos[-1].tipo, DETENIDO)
        self.assertEqual(tramos[-1].inicio, momento(70))

    def test_un_hueco_al_final_no_inventa_un_tramo_posterior(self):
        # Sin muestra después del hueco no hay intervalo que clasificar: el
        # último tramo es el hueco, y así lo lee el panel (sin señal).
        tramos = calcular_tramos([quieto(0), quieto(3), quieto(60)], PARAMETROS)

        self.assertEqual([tramo.tipo for tramo in tramos], [DETENIDO, SIN_SENAL])

    def test_las_muestras_malas_no_generan_movimiento_falso(self):
        # Sin filtrar la precisión, el salto de 500 m parecería un viaje.
        muestras = [
            quieto(0),
            Muestra(LAT + 0.01, LNG, momento(3), 900.0),
            quieto(6),
        ]

        tramos = calcular_tramos(muestras, PARAMETROS)

        self.assertEqual([tramo.tipo for tramo in tramos], [DETENIDO])

    def test_el_tramo_guarda_donde_estaba(self):
        tramos = calcular_tramos([quieto(0), quieto(3)], PARAMETROS)

        self.assertAlmostEqual(tramos[0].lat, LAT, places=6)
        self.assertAlmostEqual(tramos[0].lng, LNG, places=6)


class EstadoActualTest(SimpleTestCase):
    def test_sin_tramos_el_estado_es_sin_senal(self):
        estado = estado_actual([], ahora=momento(10), parametros=PARAMETROS)

        self.assertEqual(estado.estado, SIN_SENAL)
        self.assertIsNone(estado.minutos)

    def test_en_movimiento_reporta_antiguedad_del_dato(self):
        tramos = calcular_tramos([quieto(0), movido(3)], PARAMETROS)

        estado = estado_actual(tramos, ahora=momento(5), parametros=PARAMETROS)

        self.assertEqual(estado.estado, MOVIMIENTO)
        # La última muestra llegó hace 2 minutos.
        self.assertEqual(estado.minutos, 2)

    def test_detenido_acumula_el_tiempo_de_la_parada(self):
        # La app reporta cada 3 minutos: media hora parado son once muestras.
        muestras = [quieto(minuto) for minuto in range(0, 31, 3)]
        tramos = calcular_tramos(muestras, PARAMETROS)

        estado = estado_actual(tramos, ahora=momento(30), parametros=PARAMETROS)

        self.assertEqual(estado.estado, DETENIDO)
        self.assertEqual(estado.minutos, 30)

    def test_sin_datos_recientes_el_estado_es_sin_senal(self):
        # Se sabe que estaba moviéndose hace 40 minutos: no se pinta en verde,
        # pero el camión sigue en el mapa con su última posición conocida.
        tramos = calcular_tramos([quieto(0), movido(3)], PARAMETROS)

        estado = estado_actual(tramos, ahora=momento(43), parametros=PARAMETROS)

        self.assertEqual(estado.estado, SIN_SENAL)
        self.assertIsNotNone(estado.lat)
        self.assertIsNotNone(estado.lng)

    def test_el_ultimo_tramo_sin_senal_manda(self):
        tramos = calcular_tramos([quieto(0), quieto(3), quieto(60)], PARAMETROS)

        estado = estado_actual(tramos, ahora=momento(60), parametros=PARAMETROS)

        self.assertEqual(estado.estado, SIN_SENAL)

    def test_una_muestra_suelta_ya_muestra_el_camion(self):
        # Un camión recién conectado tiene una posición y ningún intervalo: debe
        # salir como detenido con su posición, no desaparecer del panel.
        estado = estado_actual(
            [], ahora=momento(1), parametros=PARAMETROS, ultima_muestra=quieto(0)
        )

        self.assertEqual(estado.estado, DETENIDO)
        self.assertAlmostEqual(estado.lat, LAT, places=6)
        self.assertAlmostEqual(estado.lng, LNG, places=6)

    def test_una_muestra_suelta_y_vieja_es_sin_senal(self):
        estado = estado_actual(
            [], ahora=momento(40), parametros=PARAMETROS, ultima_muestra=quieto(0)
        )

        self.assertEqual(estado.estado, SIN_SENAL)
        self.assertAlmostEqual(estado.lat, LAT, places=6)
        self.assertAlmostEqual(estado.lng, LNG, places=6)

    def test_sin_tramos_y_sin_ultima_muestra_sigue_siendo_sin_senal(self):
        estado = estado_actual([], ahora=momento(0), parametros=PARAMETROS)

        self.assertEqual(estado.estado, SIN_SENAL)
        self.assertIsNone(estado.lat)


class SimplificarRutaTest(SimpleTestCase):
    """El panel dibuja el recorrido del día: se manda simplificado."""

    TOLERANCIA = 30

    def test_una_ruta_de_dos_puntos_no_se_toca(self):
        puntos = [(LAT, LNG), (LAT + 0.01, LNG)]

        self.assertEqual(simplificar_ruta(puntos, self.TOLERANCIA), puntos)

    def test_sin_puntos_no_falla(self):
        self.assertEqual(simplificar_ruta([], self.TOLERANCIA), [])

    def test_los_extremos_siempre_se_conservan(self):
        puntos = [(LAT, LNG), (LAT + 0.001, LNG + 0.001), (LAT + 0.02, LNG + 0.02)]

        resultado = simplificar_ruta(puntos, self.TOLERANCIA)

        self.assertEqual(resultado[0], puntos[0])
        self.assertEqual(resultado[-1], puntos[-1])

    def test_los_puntos_en_linea_recta_se_descartan(self):
        # Tres puntos sobre la misma recta: el del medio no aporta forma.
        puntos = [(LAT, LNG), (LAT, LNG + 0.0001), (LAT, LNG + 0.0002)]

        self.assertEqual(simplificar_ruta(puntos, self.TOLERANCIA), [puntos[0], puntos[-1]])

    def test_un_desvio_mayor_a_la_tolerancia_conserva_el_punto(self):
        # El punto del medio se aparta ~111 m de la recta: se conserva.
        puntos = [(LAT, LNG), (LAT + 0.001, LNG + 0.0001), (LAT, LNG + 0.0002)]

        self.assertEqual(len(simplificar_ruta(puntos, self.TOLERANCIA)), 3)

    def test_una_tolerancia_enorme_reduce_a_los_extremos(self):
        puntos = [(LAT, LNG), (LAT + 0.001, LNG + 0.001), (LAT, LNG + 0.002)]

        self.assertEqual(len(simplificar_ruta(puntos, 100_000)), 2)

    def test_reduce_una_ruta_larga_con_zigzag_suave(self):
        # 50 puntos avanzando con una ondulación pequeña: la ruta se acorta
        # mucho sin perder el trazo general.
        puntos = [
            (LAT + indice * 0.0005 + (0.00002 if indice % 2 else 0), LNG + indice * 0.0005)
            for indice in range(50)
        ]

        resultado = simplificar_ruta(puntos, self.TOLERANCIA)

        self.assertLess(len(resultado), len(puntos) / 2)
        self.assertEqual(resultado[0], puntos[0])
        self.assertEqual(resultado[-1], puntos[-1])
