"""
Pruebas del cambio de fecha de un pedido por parte del administrador.

Ejecutar:
    python manage.py test mockups.tests.admin.test_fecha_pedido -v 2

Cubren:
- Solo admin puede cambiar la fecha (jefe/telefonista no).
- Se conserva la hora original; cambia solo el día.
- Funciona en pedidos ya entregados (editar_pedido los bloquea).
- El cambio se refleja en las ventas del camionero: el sobre abierto de la fecha
  destino lo incorpora y el de la fecha origen lo libera.
- Los sobres cerrados no se reabren (se advierte al admin).
- Trazabilidad en HistorialCambioPedido y AuditoriaAccion.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from mockups.models import AuditoriaAccion, HistorialCambioPedido, SobreDiario
from mockups.tests.base import crear_balon_11kg, crear_pedido, crear_sobre, crear_usuario, dt_chile
from mockups.utils.fechas import TZ_CHILE

DIA_ORIGEN = date(2026, 6, 15)
DIA_DESTINO = date(2026, 6, 16)


class AdminFechaPedidoFixturesMixin:
    @classmethod
    def setUpTestData(cls):
        cls.admin = crear_usuario('admin', 'admin_fecha')
        cls.jefe = crear_usuario('jefe', 'jefe_fecha')
        cls.telefonista = crear_usuario('telefonista', 'telefonista_fecha')
        cls.camionero = crear_usuario(
            'camionero', 'camionero_fecha', first_name='Camion', last_name='Ero',
        )
        cls.balon = crear_balon_11kg()

    def _dt_chile(self, dia, hora=12):
        """Datetime aware (UTC internamente) para un día/hora en Chile."""
        return dt_chile(dia, hora)

    def _crear_pedido(self, *, dia, estado='entregado', entregador=None, cantidad=2, origen='telefono'):
        return crear_pedido(
            registrador=self.telefonista,
            entregador=entregador if entregador is not None else (
                self.camionero if estado == 'entregado' else None
            ),
            estado=estado,
            origen=origen,
            fecha=self._dt_chile(dia),
            lineas=[(self.balon, cantidad)],
        )

    def _crear_sobre_camion(self, *, dia, cerrado=False):
        return crear_sobre(
            tipo='camion',
            fecha_correspondiente=dia,
            creado_por=self.admin,
            trabajador=self.camionero,
            cerrado=cerrado,
        )

    def _post_cambio(self, pedido, nueva_fecha, **kwargs):
        return self.client.post(
            reverse('pedidos_cambiar_fecha', args=[pedido.id]),
            {'nueva_fecha': nueva_fecha.strftime('%Y-%m-%d')},
            **kwargs,
        )


class CambioFechaBasicoTests(AdminFechaPedidoFixturesMixin, TestCase):
    def test_admin_cambia_el_dia_conservando_la_hora(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.admin)

        self._post_cambio(pedido, DIA_DESTINO)

        pedido.refresh_from_db()
        fecha_local = timezone.localtime(pedido.fecha, TZ_CHILE)
        self.assertEqual(fecha_local.date(), DIA_DESTINO)
        self.assertEqual((fecha_local.hour, fecha_local.minute), (12, 0))

    def test_funciona_en_pedido_entregado(self):
        """editar_pedido bloquea los entregados; esta acción debe permitirlo."""
        pedido = self._crear_pedido(dia=DIA_ORIGEN, estado='entregado')
        self.client.force_login(self.admin)

        self._post_cambio(pedido, DIA_DESTINO)

        pedido.refresh_from_db()
        self.assertEqual(timezone.localtime(pedido.fecha, TZ_CHILE).date(), DIA_DESTINO)

    def test_funciona_en_pedido_cancelado(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN, estado='cancelado', entregador=None)
        self.client.force_login(self.admin)

        self._post_cambio(pedido, DIA_DESTINO)

        pedido.refresh_from_db()
        self.assertEqual(timezone.localtime(pedido.fecha, TZ_CHILE).date(), DIA_DESTINO)

    def test_fecha_invalida_no_cambia_nada(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        fecha_original = pedido.fecha
        self.client.force_login(self.admin)

        self.client.post(
            reverse('pedidos_cambiar_fecha', args=[pedido.id]),
            {'nueva_fecha': 'no-es-fecha'},
        )

        pedido.refresh_from_db()
        self.assertEqual(pedido.fecha, fecha_original)

    def test_misma_fecha_no_genera_cambios_ni_historial(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.admin)

        self._post_cambio(pedido, DIA_ORIGEN)

        pedido.refresh_from_db()
        self.assertEqual(timezone.localtime(pedido.fecha, TZ_CHILE).date(), DIA_ORIGEN)
        self.assertFalse(HistorialCambioPedido.objects.filter(pedido=pedido).exists())

    def test_exige_post(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.admin)

        respuesta = self.client.get(reverse('pedidos_cambiar_fecha', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 405)


class CambioFechaPermisosTests(AdminFechaPedidoFixturesMixin, TestCase):
    def _assert_no_cambia(self, usuario):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        fecha_original = pedido.fecha
        self.client.force_login(usuario)

        respuesta = self._post_cambio(pedido, DIA_DESTINO)

        self.assertEqual(respuesta.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.fecha, fecha_original)

    def test_jefe_no_puede_cambiar_la_fecha(self):
        self._assert_no_cambia(self.jefe)

    def test_telefonista_no_puede_cambiar_la_fecha(self):
        self._assert_no_cambia(self.telefonista)

    def test_camionero_no_puede_cambiar_la_fecha(self):
        self._assert_no_cambia(self.camionero)

    def test_acceso_denegado_queda_auditado(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.telefonista)

        self._post_cambio(pedido, DIA_DESTINO)

        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='PERM_DENIED').exists()
        )


class CambioFechaSobresTests(AdminFechaPedidoFixturesMixin, TestCase):
    def test_sobre_abierto_destino_incorpora_el_pedido(self):
        """El camionero ve el pedido en las ventas del día nuevo."""
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        sobre_destino = self._crear_sobre_camion(dia=DIA_DESTINO)
        self.assertEqual(sobre_destino.monto_calculado_app, 0)

        self.client.force_login(self.admin)
        self._post_cambio(pedido, DIA_DESTINO)

        sobre_destino.refresh_from_db()
        self.assertEqual(sobre_destino.monto_calculado_app, pedido.monto_total)

    def test_sobre_abierto_origen_libera_el_pedido(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        sobre_origen = self._crear_sobre_camion(dia=DIA_ORIGEN)
        self.assertEqual(sobre_origen.monto_calculado_app, pedido.monto_total)

        self.client.force_login(self.admin)
        self._post_cambio(pedido, DIA_DESTINO)

        sobre_origen.refresh_from_db()
        self.assertEqual(sobre_origen.monto_calculado_app, 0)

    def test_sobre_cerrado_no_se_reabre_y_se_advierte(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        sobre_cerrado = self._crear_sobre_camion(dia=DIA_DESTINO, cerrado=True)

        self.client.force_login(self.admin)
        respuesta = self._post_cambio(pedido, DIA_DESTINO, follow=True)

        sobre_cerrado.refresh_from_db()
        self.assertEqual(sobre_cerrado.monto_calculado_app, 0)
        self.assertTrue(sobre_cerrado.cerrado)
        self.assertContains(respuesta, 'CERRADO')

    def test_pedido_local_afecta_sobre_de_bodega(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN, origen='local', entregador=None)
        sobre_bodega = SobreDiario.objects.create(
            tipo='bodega',
            fecha_correspondiente=DIA_DESTINO,
            creado_por=self.admin,
            trabajador=None,
        )
        self.assertEqual(sobre_bodega.monto_calculado_app, 0)

        self.client.force_login(self.admin)
        self._post_cambio(pedido, DIA_DESTINO)

        sobre_bodega.refresh_from_db()
        self.assertEqual(sobre_bodega.monto_calculado_app, pedido.monto_total)


class CambioFechaTrazabilidadTests(AdminFechaPedidoFixturesMixin, TestCase):
    def test_registra_historial_y_auditoria(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.admin)

        self._post_cambio(pedido, DIA_DESTINO)

        historial = HistorialCambioPedido.objects.filter(pedido=pedido)
        self.assertEqual(historial.count(), 1)
        self.assertIn('Fecha del pedido cambiada', historial.first().descripcion)

        auditoria = AuditoriaAccion.objects.filter(
            tipo='PEDIDO_UPDATE', objeto_id=pedido.id,
        )
        self.assertTrue(auditoria.exists())
        self.assertIn('fecha cambiada', auditoria.first().descripcion)


class CambioFechaTemplateTests(AdminFechaPedidoFixturesMixin, TestCase):
    def test_admin_ve_el_formulario_en_el_detalle(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.admin)

        respuesta = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Cambiar Fecha')
        self.assertContains(respuesta, reverse('pedidos_cambiar_fecha', args=[pedido.id]))

    def test_jefe_no_ve_el_formulario(self):
        pedido = self._crear_pedido(dia=DIA_ORIGEN)
        self.client.force_login(self.jefe)

        respuesta = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Cambiar Fecha')
