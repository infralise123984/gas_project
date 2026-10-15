"""Tests del panel de flota (docs/PLAN_MONITOREO_FLOTA.md §10).

Cubren las tres cosas que pueden romperse sin que nadie se entere: quién entra,
que la plantilla cargue de verdad el mapa, y que el JSON traiga el cruce de
movimiento con ventas —que es lo que la telefonista lee para decidir a quién
llama—.

El reloj se fija por la misma razón que en los tests de ingesta: sin eso, el
resultado depende de la hora a la que se ejecute la suite.

Ejecutar con:

    python manage.py test mockups.tests.flota -v 2
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from mockups.models import UbicacionCamion
from mockups.tests.base import TZ_CHILE, crear_balon_11kg, crear_pedido, crear_usuario

AHORA = datetime(2026, 10, 14, 15, 0, tzinfo=TZ_CHILE)
LAT, LNG = -33.450000, -70.660000


class BasePanelTest(TestCase):
    def setUp(self):
        self.reloj = patch('django.utils.timezone.now', return_value=AHORA)
        self.reloj.start()
        self.addCleanup(self.reloj.stop)

        self.telefonista = crear_usuario('telefonista', 'telefonista_flota')
        self.jefe = crear_usuario('jefe', 'jefe_flota')
        self.admin = crear_usuario('admin', 'admin_flota')
        self.bodeguero = crear_usuario('bodeguero', 'bodeguero_flota')
        self.camionero = crear_usuario('camionero', 'camionero_flota')
        self.balon = crear_balon_11kg()

    def _posicionar(self, *, lat=LAT, lng=LNG, minutos=3, usuario=None):
        """Dos muestras separadas ``minutos``: el intervalo mínimo para un tramo."""
        usuario = usuario or self.camionero
        for desplazamiento in (0, minutos):
            UbicacionCamion.objects.create(
                trabajador=usuario,
                lat=lat,
                lng=lng,
                capturado_el=AHORA - timedelta(minutes=desplazamiento),
            )

    def _venta(self, *, origen='tarreo', usuario=None, cantidad=2):
        usuario = usuario or self.camionero
        return crear_pedido(
            registrador=usuario,
            entregador=usuario,
            origen=origen,
            estado='entregado',
            fecha=AHORA,
            lineas=[(self.balon, cantidad)],
        )


class AccesoPanelTest(BasePanelTest):
    def test_la_telefonista_entra(self):
        self.client.force_login(self.telefonista)

        respuesta = self.client.get(reverse('flota_panel'))

        self.assertEqual(respuesta.status_code, 200)

    def test_el_jefe_entra(self):
        self.client.force_login(self.jefe)

        self.assertEqual(self.client.get(reverse('flota_panel')).status_code, 200)

    def test_el_admin_entra_con_paridad(self):
        # Regla del negocio: admin y jefe tienen el mismo acceso.
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(reverse('flota_panel')).status_code, 200)

    def test_el_bodeguero_queda_fuera(self):
        self.client.force_login(self.bodeguero)

        self.assertEqual(self.client.get(reverse('flota_panel')).status_code, 302)

    def test_el_camionero_no_ve_a_sus_pares(self):
        self.client.force_login(self.camionero)

        self.assertEqual(self.client.get(reverse('flota_panel')).status_code, 302)

    def test_sin_sesion_manda_al_login(self):
        respuesta = self.client.get(reverse('flota_panel'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/auth/login/', respuesta['Location'])


class PlantillaPanelTest(BasePanelTest):
    def test_la_pagina_carga_el_mapa_y_el_refresco(self):
        self.client.force_login(self.telefonista)

        contenido = self.client.get(reverse('flota_panel')).content.decode()

        self.assertIn('id="mapaFlota"', contenido)
        self.assertIn('leaflet', contenido.lower())
        self.assertIn('/flota/api/', contenido)
        self.assertIn('id="verRastro"', contenido)


class ApiFlotaTest(BasePanelTest):
    def _consultar(self, usuario=None):
        self.client.force_login(usuario or self.telefonista)
        return self.client.get(reverse('flota_api'))

    def test_la_telefonista_recibe_el_estado(self):
        self._posicionar()
        self._venta()

        respuesta = self._consultar()
        datos = respuesta.json()

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('minutos_detenido', datos)
        self.assertEqual(len(datos['camiones']), 1)

    def test_el_camionero_no_puede_consultar_la_flota(self):
        self.assertEqual(self._consultar(self.camionero).status_code, 403)

    def test_sin_sesion_no_hay_datos(self):
        self.assertEqual(self.client.get(reverse('flota_api')).status_code, 302)

    def test_trae_el_cruce_de_movimiento_y_ventas(self):
        self._posicionar(minutos=3)
        self._venta(origen='tarreo', cantidad=2)

        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['estado'], 'detenido')
        self.assertEqual(camion['pedidos'], 1)
        # Dos balones de 11 kg a precio de domicilio (14.000) son 28.000, todo
        # de tarreo: es el número que dice si el camión está vendiendo.
        self.assertEqual(camion['monto_tarreo'], 28000)
        self.assertEqual(camion['monto'], 28000)

    def test_separa_el_tarreo_del_resto_de_las_entregas(self):
        self._posicionar()
        self._venta(origen='tarreo', cantidad=2)
        self._venta(origen='telefono', cantidad=1)

        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['pedidos'], 2)
        self.assertEqual(camion['monto_tarreo'], 28000)
        self.assertEqual(camion['monto'], 42000)

    def test_sin_muestras_el_estado_es_sin_senal(self):
        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['estado'], 'sin_senal')
        self.assertIsNone(camion['lat'])

    def test_una_posicion_suelta_ya_aparece_en_el_panel(self):
        # El caso real: el camión acaba de encender la app. Con una sola posición
        # todavía no hay tramo, pero desaparecer del panel sería mentir.
        UbicacionCamion.objects.create(
            trabajador=self.camionero, lat=LAT, lng=LNG, capturado_el=AHORA
        )

        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['estado'], 'detenido')
        self.assertAlmostEqual(camion['lat'], LAT, places=6)
        self.assertEqual(camion['tramos'], [])

    def test_la_bitacora_trae_los_tramos_del_dia(self):
        self._posicionar(minutos=3)

        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(len(camion['tramos']), 1)
        self.assertEqual(camion['tramos'][0]['tipo'], 'detenido')
        self.assertEqual(camion['tramos'][0]['minutos'], 3)

    def test_el_recorrido_del_dia_viaja_en_el_json(self):
        self._posicionar(minutos=3)

        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['rastro'], [[LAT, LNG], [LAT, LNG]])

    def test_una_muestra_con_mala_precision_no_deforma_el_recorrido(self):
        self._posicionar(minutos=3)
        UbicacionCamion.objects.create(
            trabajador=self.camionero,
            lat=LAT + 0.5,          # a medio grado: error de GPS grosero
            lng=LNG,
            precision_m=900.0,      # y lo declara
            capturado_el=AHORA - timedelta(minutes=1),
        )

        camion = self._consultar().json()['camiones'][0]

        for lat, _ in camion['rastro']:
            self.assertLess(abs(lat - LAT), 0.01)

    def test_sin_muestras_el_recorrido_va_vacio(self):
        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['rastro'], [])

    def test_un_camion_que_dejo_de_reportar_conserva_su_ultima_posicion(self):
        # Tres horas sin señales: no se pinta en verde, pero la última posición
        # conocida sigue en el mapa. Un mapa vacío no le dice a la telefonista
        # dónde buscar el camión.
        UbicacionCamion.objects.create(
            trabajador=self.camionero,
            lat=LAT,
            lng=LNG,
            capturado_el=AHORA - timedelta(hours=3),
        )

        camion = self._consultar().json()['camiones'][0]

        self.assertEqual(camion['estado'], 'sin_senal')
        self.assertEqual(camion['lat'], LAT)
        self.assertEqual(camion['lng'], LNG)
        self.assertEqual(camion['rastro'], [[LAT, LNG]])

    def test_la_bitacora_no_expone_camioneros_inactivos(self):
        self.camionero.is_active = False
        self.camionero.save(update_fields=['is_active'])

        respuesta = self._consultar()

        self.assertEqual(respuesta.json()['camiones'], [])

    def test_no_se_lista_a_quien_no_es_camionero(self):
        respuesta = self._consultar()

        nombres = [camion['username'] for camion in respuesta.json()['camiones']]
        self.assertNotIn(self.telefonista.username, nombres)
        self.assertIn(self.camionero.username, nombres)
