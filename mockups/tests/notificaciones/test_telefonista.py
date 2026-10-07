"""
Pruebas de los avisos in-app (efimeros) del telefonista.

Ejecutar:
    python manage.py test mockups.tests.notificaciones.test_telefonista -v 2

Cubren:
- El telefonista REGISTRADOR recibe aviso cuando su camionero toma, entrega,
  cancela o devuelve un pedido, con el nombre del camionero.
- Otro telefonista NO ve esos avisos (aislamiento por registrador).
- Solo acciones de camionero generan aviso (una anulacion del admin, no).
- Acceso: jefe/admin quedan fuera (403); anonimo va al login.
- Sin `desde` la API responde vacio (linea base, sin avisos viejos).
- `desde` filtra por fecha y los avisos llegan del mas reciente al mas antiguo.
"""

from datetime import timedelta
from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from mockups.models import HistorialEstadoPedido
from mockups.tests.base import crear_balon_11kg, crear_pedido, crear_usuario


class NotifFixturesMixin:
    @classmethod
    def setUpTestData(cls):
        cls.telefonista = crear_usuario(
            'telefonista', 'tel_notif', first_name='Tere', last_name='Fonista',
        )
        cls.otro_telefonista = crear_usuario('telefonista', 'tel_notif2')
        cls.camionero = crear_usuario(
            'camionero', 'cam_notif', first_name='Cami', last_name='Nero',
        )
        cls.jefe = crear_usuario('jefe', 'jefe_notif')
        cls.balon = crear_balon_11kg()

    def _crear_pedido(self, *, registrador=None, estado='pendiente', entregador=None,
                      origen='telefono'):
        return crear_pedido(
            registrador=registrador or self.telefonista,
            entregador=entregador,
            estado=estado,
            origen=origen,
            lineas=[(self.balon, 2)],
        )

    def _desde(self, delta=timedelta(minutes=1)):
        return timezone.now() - delta

    def _get_avisos(self, *, desde=None):
        url = reverse('notificaciones_api')
        if desde is not None:
            url += '?desde=' + quote(desde.isoformat())
        return self.client.get(url)


class AvisosPorAccionTests(NotifFixturesMixin, TestCase):
    def _primer_aviso(self, pedido):
        self.client.force_login(self.telefonista)
        data = self._get_avisos(desde=self._desde()).json()
        self.assertEqual(data['count'], 1, data)
        return data['items'][0]

    def test_camionero_toma_pedido_genera_aviso(self):
        pedido = self._crear_pedido()
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_tomar', args=[pedido.id]))

        aviso = self._primer_aviso(pedido)

        self.assertEqual(aviso['tipo'], 'tomado')
        self.assertEqual(aviso['pedido_id'], pedido.id)
        self.assertIn('Cami', aviso['camionero'])
        self.assertEqual(aviso['url'], reverse('pedidos_detalle', args=[pedido.id]))

    def test_camionero_entrega_genera_aviso(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_entregado', args=[pedido.id]))

        aviso = self._primer_aviso(pedido)

        self.assertEqual(aviso['tipo'], 'entregado')
        self.assertIn('entrego', aviso['mensaje'])

    def test_camionero_cancela_genera_aviso(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_cancelar', args=[pedido.id]))

        aviso = self._primer_aviso(pedido)

        self.assertEqual(aviso['tipo'], 'cancelado')

    def test_camionero_devuelve_genera_aviso(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_devolver', args=[pedido.id]))

        aviso = self._primer_aviso(pedido)

        self.assertEqual(aviso['tipo'], 'devuelto')
        self.assertIn('devolvio', aviso['mensaje'])


class AislamientoYOrdenTests(NotifFixturesMixin, TestCase):
    def test_otro_telefonista_no_ve_los_avisos(self):
        pedido = self._crear_pedido(registrador=self.telefonista)
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_tomar', args=[pedido.id]))

        self.client.force_login(self.otro_telefonista)
        data = self._get_avisos(desde=self._desde()).json()

        self.assertEqual(data['count'], 0)
        self.assertEqual(data['items'], [])

    def test_solo_acciones_de_camionero_generan_aviso(self):
        """Una anulacion hecha por jefe/admin no debe avisar al telefonista."""
        pedido = self._crear_pedido()
        HistorialEstadoPedido.objects.create(
            pedido=pedido,
            estado_anterior='pendiente',
            estado_nuevo='cancelado',
            cambiado_por=self.jefe,
            fecha_cambio=timezone.now(),
        )

        self.client.force_login(self.telefonista)
        data = self._get_avisos(desde=self._desde()).json()

        self.assertEqual(data['count'], 0)

    def test_avisos_ordenados_del_mas_reciente_al_mas_antiguo(self):
        pedido = self._crear_pedido()
        HistorialEstadoPedido.objects.create(
            pedido=pedido, estado_anterior='pendiente', estado_nuevo='en_ruta',
            cambiado_por=self.camionero, fecha_cambio=timezone.now() - timedelta(minutes=5),
        )
        HistorialEstadoPedido.objects.create(
            pedido=pedido, estado_anterior='en_ruta', estado_nuevo='entregado',
            cambiado_por=self.camionero, fecha_cambio=timezone.now(),
        )

        self.client.force_login(self.telefonista)
        items = self._get_avisos(desde=self._desde(delta=timedelta(minutes=10))).json()['items']

        self.assertEqual([i['tipo'] for i in items], ['entregado', 'tomado'])


class VentanaYFiltrosTests(NotifFixturesMixin, TestCase):
    def test_sin_desde_responde_vacio(self):
        pedido = self._crear_pedido()
        HistorialEstadoPedido.objects.create(
            pedido=pedido, estado_anterior='pendiente', estado_nuevo='en_ruta',
            cambiado_por=self.camionero, fecha_cambio=timezone.now(),
        )

        self.client.force_login(self.telefonista)
        data = self._get_avisos().json()

        self.assertEqual(data['count'], 0)
        self.assertEqual(data['items'], [])
        # La linea base usa la hora del servidor (evita depender del reloj del navegador).
        self.assertIn('server_time', data)

    def test_desde_filtra_eventos_antiguos(self):
        pedido = self._crear_pedido()
        HistorialEstadoPedido.objects.create(
            pedido=pedido, estado_anterior='pendiente', estado_nuevo='en_ruta',
            cambiado_por=self.camionero, fecha_cambio=timezone.now() - timedelta(hours=2),
        )

        self.client.force_login(self.telefonista)
        data = self._get_avisos(desde=self._desde(delta=timedelta(hours=1))).json()

        self.assertEqual(data['count'], 0)

    def test_desde_invalido_devuelve_400(self):
        self.client.force_login(self.telefonista)

        respuesta = self.client.get(reverse('notificaciones_api') + '?desde=no-es-fecha')

        self.assertEqual(respuesta.status_code, 400)


class AccesoTests(NotifFixturesMixin, TestCase):
    def test_jefe_no_accede(self):
        self.client.force_login(self.jefe)

        respuesta = self._get_avisos(desde=self._desde())

        self.assertEqual(respuesta.status_code, 403)

    def test_anonimo_va_al_login(self):
        respuesta = self._get_avisos(desde=self._desde())

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/auth/login/', respuesta.url)
