"""
Pruebas de la anulación administrativa de pedidos.

Ejecutar:
    python manage.py test mockups.tests_anular_pedido -v 2

Cubren:
- El admin puede anular un pedido ya entregado (antes imposible desde la app).
- La anulación deja traza: historial de estado, historial de cambios y auditoría.
- Motivo obligatorio y no se anula dos veces.
- Solo admin: jefe, telefonista y camionero quedan fuera y auditados.
- Se refrescan los sobres ABIERTOS; con sobre CERRADO se advierte y no se toca.
- El template del detalle solo muestra la anulación al admin.
"""

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from mockups.admin import PedidoAdmin
from mockups.models import (
    AuditoriaAccion,
    DetallePedido,
    HistorialCambioPedido,
    HistorialEstadoPedido,
    Pedido,
    SobreDiario,
    TipoBalon,
)
from mockups.services.pedidos import AnulacionNoPermitida, anular_pedido
from mockups.services.sobres import sincronizar_sobre_desde_pedidos

User = get_user_model()
TZ_CHILE = ZoneInfo('America/Santiago')

MOTIVO_PRUEBA = 'Entrega cargada a la cuenta equivocada.'


class AnulacionFixturesMixin:
    """Usuarios, balón y fecha base para los escenarios de anulación."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='admin_anula', password='test12345', rol='admin',
        )
        cls.jefe = User.objects.create_user(
            username='jefe_anula', password='test12345', rol='jefe',
        )
        cls.telefonista = User.objects.create_user(
            username='telefonista_anula', password='test12345', rol='telefonista',
        )
        cls.bodeguero = User.objects.create_user(
            username='bodeguero_anula', password='test12345', rol='bodeguero',
        )
        cls.camionero = User.objects.create_user(
            username='camionero_anula', password='test12345', rol='camionero',
        )

        cls.balon_5 = TipoBalon.objects.create(
            nombre='Balón 5 kg', peso_neto_gas=5, tipo_gas='normal',
            precio_compra=4000, precio_local=7000, precio_domicilio=8500, activo=True,
        )

        cls.fecha_dia = date(2026, 6, 15)
        cls.mediodia_chile = timezone.make_aware(
            datetime.combine(cls.fecha_dia, time(12, 0)), TZ_CHILE,
        )

    def _crear_pedido(self, *, estado='entregado', entregador=None, cantidad=2, origen='telefono'):
        pedido = Pedido.objects.create(
            registrador=self.telefonista,
            entregador=entregador if entregador is not None else self.camionero,
            origen=origen,
            estado=estado,
            metodo_pago='efectivo',
            fecha=self.mediodia_chile,
        )
        DetallePedido.objects.create(
            pedido=pedido,
            balon=self.balon_5,
            cantidad=cantidad,
            precio_venta_unitario=self.balon_5.precio_domicilio,
            precio_compra_unitario=self.balon_5.precio_compra,
        )
        pedido.calcular_totales()
        pedido.refresh_from_db()
        return pedido

    def _url_anular(self, pedido):
        return reverse('pedidos_anular', args=[pedido.id])


class AnulacionPedidoEntregadoTests(AnulacionFixturesMixin, TestCase):
    def test_admin_anula_pedido_entregado(self):
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        respuesta = self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

        self.assertRedirects(
            respuesta,
            reverse('pedidos_detalle', args=[pedido.id]),
            fetch_redirect_response=False,
        )
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'cancelado')

    def test_anulacion_deja_traza_completa(self):
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

        cambio = HistorialEstadoPedido.objects.get(pedido=pedido)
        self.assertEqual(cambio.estado_anterior, 'entregado')
        self.assertEqual(cambio.estado_nuevo, 'cancelado')
        self.assertEqual(cambio.cambiado_por, self.admin)
        self.assertIn(MOTIVO_PRUEBA, cambio.comentario)

        historial = HistorialCambioPedido.objects.get(pedido=pedido)
        self.assertEqual(historial.usuario, self.admin)
        self.assertIn(MOTIVO_PRUEBA, historial.descripcion)

        auditoria = AuditoriaAccion.objects.get(tipo='PEDIDO_CANCEL', objeto_id=pedido.id)
        self.assertEqual(auditoria.username, self.admin.username)
        self.assertEqual(auditoria.datos_anteriores, {'estado': 'entregado'})
        self.assertEqual(auditoria.datos_nuevos, {'estado': 'cancelado', 'motivo': MOTIVO_PRUEBA})

    def test_pedido_anulado_sale_de_los_totales(self):
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

        vigentes = Pedido.objects.filter(estado='entregado')
        self.assertFalse(vigentes.exists())
        self.assertEqual(pedido.monto_total, Decimal(17000))  # el pedido no se borra ni se altera

    def test_pedido_no_se_borra(self):
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

        self.assertTrue(Pedido.objects.filter(pk=pedido.pk).exists())
        self.assertEqual(pedido.detalles.count(), 1)


class AnulacionValidacionTests(AnulacionFixturesMixin, TestCase):
    def test_motivo_obligatorio(self):
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        respuesta = self.client.post(self._url_anular(pedido), {'motivo': '   '}, follow=True)

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'entregado')
        self.assertContains(respuesta, 'Debes indicar el motivo')
        self.assertFalse(HistorialEstadoPedido.objects.filter(pedido=pedido).exists())
        self.assertFalse(AuditoriaAccion.objects.filter(tipo='PEDIDO_CANCEL').exists())

    def test_no_se_puede_anular_dos_veces(self):
        pedido = self._crear_pedido(estado='cancelado')
        self.client.force_login(self.admin)

        respuesta = self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA}, follow=True)

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'cancelado')
        self.assertContains(respuesta, 'ya está anulado')
        self.assertFalse(HistorialEstadoPedido.objects.filter(pedido=pedido).exists())

    def test_get_no_permitido(self):
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        respuesta = self.client.get(self._url_anular(pedido))

        self.assertEqual(respuesta.status_code, 405)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'entregado')

    def test_solo_admin_puede_anular(self):
        for usuario in (self.jefe, self.telefonista, self.camionero):
            with self.subTest(rol=usuario.rol):
                pedido = self._crear_pedido(estado='entregado')
                self.client.force_login(usuario)

                respuesta = self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

                self.assertRedirects(
                    respuesta, reverse('index'), fetch_redirect_response=False,
                )
                pedido.refresh_from_db()
                self.assertEqual(pedido.estado, 'entregado')
                self.assertTrue(
                    AuditoriaAccion.objects.filter(
                        tipo='PERM_DENIED', username=usuario.username,
                    ).exists()
                )

    def test_servicio_rechaza_pedido_ya_anulado(self):
        pedido = self._crear_pedido(estado='cancelado')

        with self.assertRaises(AnulacionNoPermitida):
            anular_pedido(pedido, usuario=self.admin, motivo=MOTIVO_PRUEBA)

    def test_servicio_rechaza_motivo_vacio(self):
        pedido = self._crear_pedido(estado='entregado')

        with self.assertRaises(AnulacionNoPermitida):
            anular_pedido(pedido, usuario=self.admin, motivo='   ')

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'entregado')


class AnulacionSobreTests(AnulacionFixturesMixin, TestCase):
    def _crear_sobre_camion(self, *, cerrado=False):
        return SobreDiario.objects.create(
            tipo='camion',
            fecha_correspondiente=self.fecha_dia,
            creado_por=self.bodeguero,
            trabajador=self.camionero,
            cerrado=cerrado,
        )

    def test_refresca_sobre_abierto_del_camionero(self):
        pedido = self._crear_pedido(estado='entregado', cantidad=2)
        sobre = self._crear_sobre_camion()
        sincronizar_sobre_desde_pedidos(sobre)
        linea = sobre.lineas.get(balon=self.balon_5)
        self.assertEqual(linea.cantidad_calculada, 2)

        self.client.force_login(self.admin)
        self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

        linea.refresh_from_db()
        self.assertEqual(linea.cantidad_calculada, 0)

    def test_advierte_si_el_sobre_esta_cerrado(self):
        pedido = self._crear_pedido(estado='entregado', cantidad=2)
        sobre = self._crear_sobre_camion(cerrado=True)
        sincronizar_sobre_desde_pedidos(sobre)
        self.assertFalse(sobre.lineas.exists())

        self.client.force_login(self.admin)
        respuesta = self.client.post(
            self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA}, follow=True,
        )

        self.assertContains(respuesta, 'CERRADO')
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'cancelado')
        self.assertFalse(sobre.lineas.exists())

    def test_sobre_de_bodega_para_pedido_local(self):
        pedido = self._crear_pedido(estado='entregado', entregador=self.bodeguero, origen='local')
        sobre = SobreDiario.objects.create(
            tipo='bodega', fecha_correspondiente=self.fecha_dia, creado_por=self.bodeguero,
        )
        sincronizar_sobre_desde_pedidos(sobre)
        self.assertEqual(sobre.lineas.get(balon=self.balon_5).cantidad_calculada, 2)

        self.client.force_login(self.admin)
        self.client.post(self._url_anular(pedido), {'motivo': MOTIVO_PRUEBA})

        self.assertEqual(sobre.lineas.get(balon=self.balon_5).cantidad_calculada, 0)


class AnulacionPanelAdminTests(AnulacionFixturesMixin, TestCase):
    def test_accion_deja_traza_en_lugar_de_update_masivo(self):
        pedido = self._crear_pedido(estado='entregado')

        request = RequestFactory().post('/admin/mockups/pedido/')
        request.user = self.admin
        request.session = {}
        request._messages = FallbackStorage(request)

        modelo_admin = PedidoAdmin(Pedido, admin.site)
        modelo_admin.marcar_cancelado(request, Pedido.objects.filter(pk=pedido.pk))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'cancelado')
        self.assertTrue(HistorialEstadoPedido.objects.filter(pedido=pedido).exists())
        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='PEDIDO_CANCEL', objeto_id=pedido.pk).exists()
        )


class AnulacionTemplateTests(AnulacionFixturesMixin, TestCase):
    def test_boton_visible_solo_para_admin(self):
        pedido = self._crear_pedido(estado='entregado')

        self.client.force_login(self.admin)
        respuesta_admin = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))
        self.assertContains(respuesta_admin, 'Anulación administrativa')

        self.client.force_login(self.jefe)
        respuesta_jefe = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))
        self.assertNotContains(respuesta_jefe, 'Anulación administrativa')

    def test_boton_oculto_si_ya_esta_anulado(self):
        pedido = self._crear_pedido(estado='cancelado')

        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))

        self.assertNotContains(respuesta, 'Anulación administrativa')
