"""Tests de la ingesta de ubicación (docs/API_MOVIL.md §7.8).

Cubre las tres cosas que pueden salir mal en producción: que el lote se guarde
(una sola vez, aunque el teléfono reintente), que lo de fuera de jornada no
entre, y que los límites no dejen al canal inutilizable.

El reloj se fija con ``patch``: sin eso, una muestra de "hoy a las 10:00" sería
pasado o futuro según la hora a la que se ejecute la suite, y el test pasaría o
fallaría según el día.

Ejecutar con:

    python manage.py test mockups.tests.api.test_ubicacion -v 2
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from mockups.api.limites import cupo_de
from mockups.models import UbicacionCamion
from mockups.services.ubicacion import corte_de_retencion
from mockups.tests.base import TZ_CHILE, crear_usuario

RUTA = '/api/v1/ubicacion/'

#: Un miércoles a las 15:00 de Chile: dentro de la jornada (08:00-20:00).
AHORA = datetime(2026, 10, 14, 15, 0, tzinfo=TZ_CHILE)

#: Fuera de la jornada: 04:00 de Chile.
DE_MADRUGADA = datetime(2026, 10, 14, 4, 0, tzinfo=TZ_CHILE)

LAT, LNG = -33.450000, -70.660000


def muestra(minutos: int = 0, *, lat: float = LAT, lng: float = LNG, **extra) -> dict:
    """Muestra de prueba, fechada hacia atrás desde ``AHORA``."""
    datos = {
        'lat': lat,
        'lng': lng,
        'capturado_el': (AHORA - timedelta(minutes=minutos)).isoformat(),
    }
    datos.update(extra)
    return datos


def cuerpo(muestras: list) -> str:
    return json.dumps({'muestras': muestras})


class BaseIngestaTest(TestCase):
    """Un camionero con teléfono y un telefonista sin permiso."""

    def setUp(self):
        # El reloj se fija acá y no con un decorador de clase: `patch` como
        # decorador de clase solo parchea los métodos definidos en ESA clase, y
        # los tests viven en las subclases. Con `start()` el parche cubre la
        # jerarquía entera y se deshace solo al terminar cada test.
        self.reloj = patch('django.utils.timezone.now', return_value=AHORA)
        self.reloj.start()
        self.addCleanup(self.reloj.stop)

        cache.clear()  # el limitador vive en la caché del proceso

        self.camionero = crear_usuario('camionero', 'camionero_ubi')
        self.otro_camionero = crear_usuario('camionero', 'camionero_ubi_2')
        self.telefonista = crear_usuario('telefonista', 'telefonista_ubi')

    def _enviar(self, muestras, usuario=None):
        self.client.force_login(usuario or self.camionero)
        return self.client.post(
            RUTA, data=cuerpo(muestras), content_type='application/json'
        )

    def _datos(self, respuesta):
        return respuesta.json()['data']


class IngestaTest(BaseIngestaTest):
    def test_acepta_un_lote_valido(self):
        respuesta = self._enviar([muestra(6), muestra(3), muestra(0)])

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._datos(respuesta)['aceptadas'], 3)
        self.assertEqual(
            UbicacionCamion.objects.filter(trabajador=self.camionero).count(), 3
        )

    def test_reenviar_el_mismo_lote_no_duplica(self):
        self._enviar([muestra(0), muestra(3)])

        respuesta = self._enviar([muestra(0), muestra(3)])

        self.assertEqual(self._datos(respuesta)['aceptadas'], 0)
        self.assertEqual(self._datos(respuesta)['repetidas'], 2)
        self.assertEqual(UbicacionCamion.objects.count(), 2)

    def test_lote_parcialmente_repetido_solo_agrega_lo_nuevo(self):
        self._enviar([muestra(0)])

        respuesta = self._enviar([muestra(0), muestra(3)])

        self.assertEqual(self._datos(respuesta)['aceptadas'], 1)
        self.assertEqual(self._datos(respuesta)['repetidas'], 1)

    def test_dos_camioneros_pueden_reportar_el_mismo_instante(self):
        self._enviar([muestra(0)])
        self._enviar([muestra(0)], usuario=self.otro_camionero)

        self.assertEqual(UbicacionCamion.objects.count(), 2)

    def test_guarda_los_campos_opcionales(self):
        self._enviar([muestra(0, precision_m=12.5, velocidad=8.3, bateria=74)])

        fila = UbicacionCamion.objects.get()
        self.assertEqual(fila.precision_m, 12.5)
        self.assertEqual(fila.velocidad, 8.3)
        self.assertEqual(fila.bateria, 74)

    def test_la_bateria_se_guarda_como_entero(self):
        self._enviar([muestra(0, bateria=74.9)])

        self.assertEqual(UbicacionCamion.objects.get().bateria, 74)

    def test_cuenta_la_muestra_invalida_sin_perder_el_lote(self):
        respuesta = self._enviar([muestra(0), muestra(3, lat=999.0)])

        self.assertEqual(self._datos(respuesta)['aceptadas'], 1)
        self.assertEqual(self._datos(respuesta)['descartadas'], 1)

    def test_descarta_latitud_fuera_de_rango(self):
        self._enviar([muestra(0, lat=-95.0)])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_longitud_fuera_de_rango(self):
        self._enviar([muestra(0, lng=181.0)])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_precision_negativa(self):
        self._enviar([muestra(0, precision_m=-1)])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_velocidad_imposible(self):
        self._enviar([muestra(0, velocidad=900)])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_bateria_fuera_de_rango(self):
        self._enviar([muestra(0, bateria=150)])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_momento_ilegible(self):
        self._enviar([muestra(0, capturado_el='ayer por la tarde')])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_momento_en_el_futuro(self):
        # Un teléfono con el reloj adelantado ensuciaría la bitácora.
        self._enviar([muestra(-60)])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_descarta_muestra_que_no_es_objeto(self):
        self._enviar(['no soy una muestra'])

        self.assertEqual(UbicacionCamion.objects.count(), 0)

    def test_los_decimales_se_ajustan_a_la_precision_del_modelo(self):
        self._enviar([muestra(0, lat=-33.4500001234)])

        self.assertEqual(str(UbicacionCamion.objects.get().lat), '-33.450000')

    def test_la_respuesta_no_devuelve_coordenadas(self):
        # La app ya sabe dónde está: reenviarle su posición solo agranda la
        # superficie donde puede quedar expuesta.
        respuesta = self._enviar([muestra(0)])

        self.assertNotIn(str(LAT), respuesta.content.decode())


class JornadaTest(BaseIngestaTest):
    def test_acepta_lo_que_cae_dentro_de_jornada(self):
        respuesta = self._enviar([muestra(0)])

        self.assertEqual(self._datos(respuesta)['aceptadas'], 1)

    def test_una_muestra_de_madrugada_no_se_guarda(self):
        de_madrugada = {
            'lat': LAT,
            'lng': LNG,
            'capturado_el': DE_MADRUGADA.isoformat(),
        }

        respuesta = self._enviar([muestra(0), de_madrugada])

        self.assertEqual(self._datos(respuesta)['aceptadas'], 1)
        self.assertEqual(self._datos(respuesta)['descartadas'], 1)
        self.assertEqual(UbicacionCamion.objects.count(), 1)


class ValidacionLoteTest(BaseIngestaTest):
    def test_lote_vacio(self):
        self.assertEqual(self._enviar([]).status_code, 400)

    def test_falta_la_lista(self):
        self.client.force_login(self.camionero)
        respuesta = self.client.post(
            RUTA, data=json.dumps({}), content_type='application/json'
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')

    def test_lote_demasiado_grande(self):
        tope = 120
        muestras = [muestra(minuto) for minuto in range(tope + 1)]

        respuesta = self._enviar(muestras)

        self.assertEqual(respuesta.status_code, 400)

    def test_campo_inesperado_en_el_lote(self):
        self.client.force_login(self.camionero)
        respuesta = self.client.post(
            RUTA,
            data=json.dumps({'muestras': [muestra(0)], 'camionero': 7}),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_acepta_un_lote_mayor_al_limite_global_del_api(self):
        # El techo global del API son 4 KB, pensado para payloads diminutos. Una
        # cola de 60 muestras ya lo supera: si no se hubiera subido el techo para
        # esta ruta, el canal de ubicación nacería roto en cuanto el camionero
        # pasara un rato sin señal.
        muestras = [muestra(minuto) for minuto in range(0, 180, 3)]
        largo = len(cuerpo(muestras))

        self.assertGreater(largo, 4096)
        self.assertLess(len(muestras), 120)
        respuesta = self._enviar(muestras)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._datos(respuesta)['aceptadas'], len(muestras))

    def test_rechaza_un_cuerpo_que_supera_el_techo_de_ubicacion(self):
        # 120 muestras con un campo inflado superan los 32 KB de techo. El corte
        # ocurre antes de parsear: no se llega a mirar el contenido.
        muestras = [muestra(0, capturado_el='x' * 400) for _ in range(120)]

        respuesta = self._enviar(muestras)

        self.assertEqual(respuesta.status_code, 400)


class AccesoIngestaTest(BaseIngestaTest):
    def test_get_no_permitido(self):
        self.client.force_login(self.camionero)

        self.assertEqual(self.client.get(RUTA).status_code, 405)

    def test_sin_sesion(self):
        self.assertEqual(
            self.client.post(RUTA, data=cuerpo([muestra(0)]), content_type='application/json').status_code,
            401,
        )

    def test_telefonista_sin_permiso(self):
        self.assertEqual(self._enviar([muestra(0)], usuario=self.telefonista).status_code, 403)

    def test_el_cupo_publicado_corta_la_racha(self):
        cupo = cupo_de('ubicacion')

        for _ in range(cupo.peticiones):
            self.assertEqual(self._enviar([muestra(0)]).status_code, 200)

        respuesta = self._enviar([muestra(5)])

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.json()['error']['codigo'], 'demasiadas_peticiones')
        self.assertIn('Retry-After', respuesta.headers)

    def test_las_cabeceras_de_cupo_viajan_en_la_respuesta(self):
        respuesta = self._enviar([muestra(0)])

        self.assertEqual(respuesta.headers['X-RateLimit-Scope'], 'ubicacion')
        self.assertIn('X-RateLimit-Remaining', respuesta.headers)


class PodaTest(BaseIngestaTest):
    def _muestra_vencida(self):
        """Fila guardada antes de la ventana de retención.

        El instante es distinto al de las muestras que envían los tests: si
        coincidiera, el lote entraría como repetido, no habría nada nuevo y la
        poda no se dispararía, con lo que el test no probaría nada.
        """
        fila = UbicacionCamion.objects.create(
            trabajador=self.camionero,
            lat=LAT,
            lng=LNG,
            capturado_el=AHORA - timedelta(minutes=60),
        )
        # `recibido_el` es auto_now_add: se reescribe con update directo.
        UbicacionCamion.objects.filter(pk=fila.pk).update(
            recibido_el=corte_de_retencion(AHORA) - timedelta(hours=1)
        )
        return fila

    def test_la_ingesta_borra_lo_que_sale_de_la_ventana(self):
        vencida = self._muestra_vencida()

        self._enviar([muestra(0)])

        self.assertFalse(UbicacionCamion.objects.filter(pk=vencida.pk).exists())

    def test_no_borra_lo_reciente_al_podar(self):
        reciente = UbicacionCamion.objects.create(
            trabajador=self.camionero, lat=LAT, lng=LNG, capturado_el=AHORA - timedelta(minutes=30)
        )

        self._enviar([muestra(0)])

        self.assertTrue(UbicacionCamion.objects.filter(pk=reciente.pk).exists())

    def test_un_lote_repetido_no_dispara_la_poda(self):
        # Sin muestras nuevas no hay motivo para recorrer la tabla.
        vencida = self._muestra_vencida()
        self._enviar([muestra(0)])

        self._enviar([muestra(0)])

        self.assertFalse(UbicacionCamion.objects.filter(pk=vencida.pk).exists())
