"""
Pruebas de la corrección administrativa del descuento por balón.

Ejecutar:
    python manage.py test mockups.tests.admin.test_descuento_pedido -v 2

Cubren:
- Solo admin accede; jefe/telefonista/camionero quedan bloqueados y auditados.
- Aplica el descuento olvidado en cualquier estado, incluidos entregado/cancelado.
- Recalcula totales (monto baja, ganancia no) y reemplaza un descuento existente.
- Respeta el tope del 50% del precio de venta registrado en la línea.
- Refresca el sobre ABIERTO del día; los sobres cerrados no se reabren (se advierte).
- Trazabilidad en HistorialCambioPedido y AuditoriaAccion.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from mockups.models import AuditoriaAccion, HistorialCambioPedido
from mockups.tests.base import crear_balon_11kg, crear_pedido, crear_sobre, crear_usuario, dt_chile

DIA = date(2026, 6, 15)
PREFIJO = 'detalles'


class AdminDescuentoFixturesMixin:
    @classmethod
    def setUpTestData(cls):
        cls.admin = crear_usuario('admin', 'admin_desc_ped')
        cls.jefe = crear_usuario('jefe', 'jefe_desc_ped')
        cls.telefonista = crear_usuario('telefonista', 'telefonista_desc_ped')
        cls.camionero = crear_usuario(
            'camionero', 'camionero_desc_ped', first_name='Camion', last_name='Ero',
        )
        # 11 kg: precio_domicilio 14000 → tope de descuento 7000 por unidad.
        cls.balon = crear_balon_11kg()

    def _dt_chile(self, hora=12):
        return dt_chile(DIA, hora)

    def _crear_pedido(self, *, estado='entregado', entregador=None, cantidad=2,
                      descuento=0, origen='telefono'):
        return crear_pedido(
            registrador=self.telefonista,
            entregador=entregador if entregador is not None else (
                self.camionero if estado == 'entregado' else None
            ),
            estado=estado,
            origen=origen,
            fecha=self._dt_chile(),
            lineas=[(self.balon, cantidad, descuento)],
        )

    def _post_descuento(self, pedido, descuentos, **kwargs):
        """POST del formset: `descuentos` es la lista de descuentos por línea (en orden de id)."""
        detalles = list(pedido.detalles.order_by('id'))
        data = {
            f'{PREFIJO}-TOTAL_FORMS': str(len(detalles)),
            f'{PREFIJO}-INITIAL_FORMS': str(len(detalles)),
            f'{PREFIJO}-MIN_NUM_FORMS': '0',
            f'{PREFIJO}-MAX_NUM_FORMS': '1000',
        }
        for i, detalle in enumerate(detalles):
            data[f'{PREFIJO}-{i}-id'] = str(detalle.id)
            data[f'{PREFIJO}-{i}-descuento_unitario'] = str(descuentos[i])
        return self.client.post(
            reverse('pedidos_admin_descuento', args=[pedido.id]), data, **kwargs,
        )

    def _crear_sobre_camion(self, cerrado=False):
        return crear_sobre(
            tipo='camion',
            fecha_correspondiente=DIA,
            creado_por=self.admin,
            trabajador=self.camionero,
            cerrado=cerrado,
        )


class AplicarDescuentoTests(AdminDescuentoFixturesMixin, TestCase):
    def test_admin_aplica_descuento_olvidado(self):
        pedido = self._crear_pedido(estado='entregado', descuento=0)
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [3000])

        pedido.refresh_from_db()
        # (14000 − 3000) × 2 : el descuento baja el monto pero NO la ganancia.
        self.assertEqual(pedido.monto_total, 22000)
        self.assertEqual(pedido.ganancia_total, 12000)
        self.assertEqual(pedido.descuento_total, 6000)
        self.assertTrue(pedido.tiene_descuento)

    def test_funciona_en_pedido_entregado(self):
        """editar_pedido bloquea los entregados; esta corrección debe permitirlo."""
        pedido = self._crear_pedido(estado='entregado')
        self.client.force_login(self.admin)

        respuesta = self._post_descuento(pedido, [2000])

        self.assertEqual(respuesta.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'entregado')
        self.assertEqual(pedido.descuento_total, 4000)

    def test_funciona_en_pedido_cancelado(self):
        pedido = self._crear_pedido(estado='cancelado', entregador=None)
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [1500])

        pedido.refresh_from_db()
        self.assertEqual(pedido.descuento_total, 3000)

    def test_reemplaza_un_descuento_existente(self):
        pedido = self._crear_pedido(descuento=1000)
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [4000])

        pedido.refresh_from_db()
        self.assertEqual(pedido.descuento_total, 8000)

    def test_vaciar_el_campo_quita_el_descuento(self):
        pedido = self._crear_pedido(descuento=3000)
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [''])

        pedido.refresh_from_db()
        self.assertEqual(pedido.descuento_total, 0)
        self.assertFalse(pedido.tiene_descuento)

    def test_sin_cambios_no_genera_historial(self):
        pedido = self._crear_pedido(descuento=0)
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [0])

        pedido.refresh_from_db()
        self.assertFalse(HistorialCambioPedido.objects.filter(pedido=pedido).exists())


class TopeDescuentoTests(AdminDescuentoFixturesMixin, TestCase):
    def test_descuento_hasta_el_tope_es_valido(self):
        pedido = self._crear_pedido()
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [7000])  # 50% de 14000

        pedido.refresh_from_db()
        self.assertEqual(pedido.descuento_total, 14000)

    def test_descuento_sobre_el_tope_se_rechaza(self):
        pedido = self._crear_pedido()
        self.client.force_login(self.admin)

        respuesta = self._post_descuento(pedido, [7001])

        self.assertEqual(respuesta.status_code, 200)  # Re-render con el error
        pedido.refresh_from_db()
        self.assertEqual(pedido.descuento_total, 0)


class AdminDescuentoPermisosTests(AdminDescuentoFixturesMixin, TestCase):
    def _assert_denegado(self, usuario):
        pedido = self._crear_pedido(descuento=0)
        self.client.force_login(usuario)

        respuesta = self.client.get(reverse('pedidos_admin_descuento', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.descuento_total, 0)

    def test_jefe_no_puede_entrar(self):
        self._assert_denegado(self.jefe)

    def test_telefonista_no_puede_entrar(self):
        self._assert_denegado(self.telefonista)

    def test_camionero_no_puede_entrar(self):
        self._assert_denegado(self.camionero)

    def test_anonimo_va_al_login(self):
        pedido = self._crear_pedido()

        respuesta = self.client.get(reverse('pedidos_admin_descuento', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/auth/login/', respuesta.url)

    def test_acceso_denegado_queda_auditado(self):
        pedido = self._crear_pedido()
        self.client.force_login(self.jefe)

        self.client.get(reverse('pedidos_admin_descuento', args=[pedido.id]))

        self.assertTrue(AuditoriaAccion.objects.filter(tipo='PERM_DENIED').exists())


class AdminDescuentoSobresTests(AdminDescuentoFixturesMixin, TestCase):
    def test_sobre_abierto_refresca_el_monto(self):
        pedido = self._crear_pedido(estado='entregado', descuento=0)
        sobre = self._crear_sobre_camion()
        sobre.refresh_from_db()
        self.assertEqual(sobre.monto_calculado_app, 28000)

        self.client.force_login(self.admin)
        self._post_descuento(pedido, [3000])

        sobre.refresh_from_db()
        self.assertEqual(sobre.monto_calculado_app, 22000)

    def test_sobre_cerrado_no_se_reabre_y_advierte(self):
        pedido = self._crear_pedido(estado='entregado', descuento=0)
        sobre = self._crear_sobre_camion(cerrado=True)

        self.client.force_login(self.admin)
        respuesta = self._post_descuento(pedido, [3000], follow=True)

        sobre.refresh_from_db()
        self.assertTrue(sobre.cerrado)
        # El sobre cerrado NO se recalcula: conserva el monto previo (28000),
        # no el nuevo con descuento (22000).
        self.assertEqual(sobre.monto_calculado_app, 28000)
        self.assertContains(respuesta, 'CERRADO')


class AdminDescuentoTrazabilidadTests(AdminDescuentoFixturesMixin, TestCase):
    def test_registra_historial_y_auditoria(self):
        pedido = self._crear_pedido(descuento=0)
        self.client.force_login(self.admin)

        self._post_descuento(pedido, [3000])

        historial = HistorialCambioPedido.objects.filter(pedido=pedido)
        self.assertEqual(historial.count(), 1)
        self.assertIn('Descuento corregido por admin', historial.first().descripcion)

        auditoria = AuditoriaAccion.objects.filter(
            tipo='PEDIDO_DESCUENTO', objeto_id=pedido.id,
        )
        self.assertTrue(auditoria.exists())


class AdminDescuentoTemplateTests(AdminDescuentoFixturesMixin, TestCase):
    def test_admin_ve_la_opcion_en_el_detalle(self):
        pedido = self._crear_pedido()
        self.client.force_login(self.admin)

        respuesta = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Corregir Descuento')
        self.assertContains(
            respuesta, reverse('pedidos_admin_descuento', args=[pedido.id])
        )

    def test_jefe_no_ve_la_opcion(self):
        pedido = self._crear_pedido()
        self.client.force_login(self.jefe)

        respuesta = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Corregir Descuento')

    def test_admin_abre_la_pagina_de_descuento(self):
        pedido = self._crear_pedido(descuento=1000)
        self.client.force_login(self.admin)

        respuesta = self.client.get(reverse('pedidos_admin_descuento', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, f'Pedido #{pedido.id}')
