"""
Pruebas de las features de devolución de pedidos y descuento por línea.

Ejecutar:
    python manage.py test mockups.tests_descuento_devolucion -v 2

Cubren:
- Descuento por balón: cálculo de totales (monto baja, ganancia no) y tope del 50%.
- Permisos de descuento: bodeguero/camionero no descuentan; jefe/admin no modifican
  un descuento ya aplicado.
- Devolución de pedido: vuelve al pool, limpia entregador y deja trazabilidad.
- Aviso al telefonista de pedidos devueltos.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from mockups.forms import DetallePedidoForm
from mockups.models import (
    AuditoriaAccion,
    DetallePedido,
    HistorialEstadoPedido,
    Pedido,
    TipoBalon,
)
from mockups.views.pedidos import _pedidos_devueltos_al_pool

User = get_user_model()


class FixturesMixin:
    """Usuarios y balones mínimos para los escenarios de descuento y devolución."""

    @classmethod
    def setUpTestData(cls):
        cls.telefonista = User.objects.create_user(
            username='telefonista_desc', password='test12345', rol='telefonista',
        )
        cls.jefe = User.objects.create_user(
            username='jefe_desc', password='test12345', rol='jefe',
        )
        cls.bodeguero = User.objects.create_user(
            username='bodeguero_desc', password='test12345', rol='bodeguero',
        )
        cls.camionero = User.objects.create_user(
            username='camionero_desc', password='test12345', rol='camionero',
            first_name='Camion', last_name='Ero',
        )
        cls.otro_camionero = User.objects.create_user(
            username='camionero_desc2', password='test12345', rol='camionero',
        )

        # 5 kg: precio_domicilio 8500 → tope de descuento 4250 por unidad.
        cls.balon_5 = TipoBalon.objects.create(
            nombre='Balón 5 kg', peso_neto_gas=5, tipo_gas='normal',
            precio_compra=4000, precio_local=7000, precio_domicilio=8500, activo=True,
        )

    def _crear_pedido(self, *, estado='pendiente', entregador=None, con_descuento=0, cantidad=2):
        pedido = Pedido.objects.create(
            registrador=self.telefonista,
            entregador=entregador,
            origen='telefono',
            estado=estado,
            metodo_pago='efectivo',
        )
        DetallePedido.objects.create(
            pedido=pedido,
            balon=self.balon_5,
            cantidad=cantidad,
            precio_venta_unitario=self.balon_5.precio_domicilio,
            precio_compra_unitario=self.balon_5.precio_compra,
            descuento_unitario=con_descuento,
        )
        pedido.calcular_totales()
        pedido.refresh_from_db()
        return pedido


class DescuentoTotalesTests(FixturesMixin, TestCase):
    def test_descuento_baja_monto_pero_no_ganancia(self):
        """El descuento es individual por balón y se multiplica por la cantidad."""
        pedido = self._crear_pedido(con_descuento=3000, cantidad=2)

        # (8500 - 3000) * 2
        self.assertEqual(pedido.monto_total, Decimal(11000))
        # El descuento NO reduce la ganancia: se registra aparte como pérdida.
        self.assertEqual(pedido.ganancia_total, Decimal((8500 - 4000) * 2))
        self.assertEqual(pedido.descuento_total, Decimal(6000))
        self.assertEqual(pedido.subtotal_bruto, Decimal(17000))
        self.assertEqual(pedido.ganancia_neta, Decimal(9000 - 6000))
        self.assertTrue(pedido.tiene_descuento)

    def test_pedido_sin_descuento_mantiene_totales(self):
        pedido = self._crear_pedido(con_descuento=0, cantidad=2)

        self.assertEqual(pedido.monto_total, Decimal(17000))
        self.assertEqual(pedido.descuento_total, Decimal(0))
        self.assertFalse(pedido.tiene_descuento)
        self.assertEqual(pedido.subtotal_bruto, pedido.monto_total)

    def test_sobre_camionero_cobra_el_monto_con_descuento(self):
        """La cuadratura del sobre usa monto_total, por lo que el camionero rinde menos."""
        self._crear_pedido(
            estado='entregado', entregador=self.camionero, con_descuento=3000, cantidad=2,
        )

        total_app = Pedido.objects.filter(
            entregador=self.camionero, estado='entregado',
        ).aggregate(total=Sum('monto_total'))['total']

        self.assertEqual(total_app, Decimal(11000))


class DescuentoFormularioTests(FixturesMixin, TestCase):
    def _form(self, *, user, descuento, instance=None, cantidad='2'):
        data = {
            'balon': str(self.balon_5.id),
            'cantidad': cantidad,
            'precio_venta_unitario': '',
            'descuento_unitario': descuento,
        }
        return DetallePedidoForm(data=data, user=user, instance=instance or DetallePedido())

    def test_descuento_hasta_el_50_por_ciento_es_valido(self):
        form = self._form(user=self.telefonista, descuento='4250')
        self.assertTrue(form.is_valid(), form.errors)

    def test_descuento_sobre_el_50_por_ciento_se_rechaza(self):
        form = self._form(user=self.telefonista, descuento='4251')
        self.assertFalse(form.is_valid())
        self.assertIn('descuento_unitario', form.errors)

    def test_descuento_negativo_se_rechaza(self):
        form = self._form(user=self.telefonista, descuento='-100')
        self.assertFalse(form.is_valid())
        self.assertIn('descuento_unitario', form.errors)

    def test_descuento_vacio_es_valido(self):
        form = self._form(user=self.telefonista, descuento='')
        self.assertTrue(form.is_valid(), form.errors)

    def test_bodeguero_no_ve_el_campo_de_descuento(self):
        form = DetallePedidoForm(user=self.bodeguero, instance=DetallePedido())
        self.assertNotIn('descuento_unitario', form.fields)

    def test_camionero_no_ve_el_campo_de_descuento(self):
        form = DetallePedidoForm(user=self.camionero, instance=DetallePedido())
        self.assertNotIn('descuento_unitario', form.fields)

    def test_jefe_puede_otorgar_descuento_nuevo(self):
        form = DetallePedidoForm(user=self.jefe, instance=DetallePedido())
        self.assertIn('descuento_unitario', form.fields)
        self.assertFalse(form.fields['descuento_unitario'].disabled)

    def test_solo_telefonista_modifica_descuento_ya_aplicado(self):
        detalle = DetallePedido(
            balon=self.balon_5, cantidad=2,
            precio_venta_unitario=self.balon_5.precio_domicilio,
            precio_compra_unitario=self.balon_5.precio_compra,
            descuento_unitario=3000,
        )

        form_jefe = DetallePedidoForm(user=self.jefe, instance=detalle)
        self.assertTrue(form_jefe.fields['descuento_unitario'].disabled)

        form_telefonista = DetallePedidoForm(user=self.telefonista, instance=detalle)
        self.assertFalse(form_telefonista.fields['descuento_unitario'].disabled)

    def test_camionero_editando_no_pierde_el_descuento_existente(self):
        """Al ocultar el campo a quien no puede descontar, el valor guardado se conserva."""
        detalle = DetallePedido(
            balon=self.balon_5, cantidad=2,
            precio_venta_unitario=self.balon_5.precio_domicilio,
            precio_compra_unitario=self.balon_5.precio_compra,
            descuento_unitario=3000,
        )
        form = DetallePedidoForm(
            data={
                'balon': str(self.balon_5.id),
                'cantidad': '3',
                'precio_venta_unitario': '',
            },
            user=self.camionero,
            instance=detalle,
        )

        self.assertTrue(form.is_valid(), form.errors)
        guardado = form.save(commit=False)
        self.assertEqual(guardado.cantidad, 3)
        self.assertEqual(guardado.descuento_unitario, Decimal(3000))


class DescuentoCreacionPedidoTests(FixturesMixin, TestCase):
    """Flujo completo: el telefonista registra un pedido con descuento."""

    def _post_pedido(self, descuento):
        session = self.client.session
        session['form_token_pedido'] = 'token-prueba'
        session.save()

        return self.client.post(reverse('pedidos_crear'), {
            'metodo_pago': 'efectivo',
            'sector': '',
            'direccion_entrega': 'Calle Falsa 123',
            'form_token': 'token-prueba',
            'detalles-TOTAL_FORMS': '1',
            'detalles-INITIAL_FORMS': '0',
            'detalles-MIN_NUM_FORMS': '0',
            'detalles-MAX_NUM_FORMS': '1000',
            'detalles-0-balon': str(self.balon_5.id),
            'detalles-0-cantidad': '2',
            'detalles-0-precio_venta_unitario': '',
            'detalles-0-descuento_unitario': descuento,
        })

    def test_pedido_creado_con_descuento(self):
        self.client.force_login(self.telefonista)
        self._post_pedido('3000')

        pedido = Pedido.objects.get()
        self.assertEqual(pedido.monto_total, Decimal(11000))
        self.assertEqual(pedido.descuento_total, Decimal(6000))
        self.assertEqual(pedido.ganancia_total, Decimal(9000))

    def test_pedido_con_descuento_excesivo_no_se_crea(self):
        self.client.force_login(self.telefonista)
        self._post_pedido('9000')

        self.assertFalse(Pedido.objects.exists())


class DevolucionPedidoTests(FixturesMixin, TestCase):
    def test_camionero_devuelve_pedido_al_pool(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.camionero)

        respuesta = self.client.post(reverse('entregas_devolver', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'pendiente')
        self.assertIsNone(pedido.entregador)
        self.assertIsNotNone(pedido.devuelto_el)
        self.assertEqual(pedido.devuelto_por, self.camionero)

        cambio = HistorialEstadoPedido.objects.get(pedido=pedido)
        self.assertEqual(cambio.estado_anterior, 'en_ruta')
        self.assertEqual(cambio.estado_nuevo, 'pendiente')

        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='PEDIDO_DEVOLVER', objeto_id=pedido.id).exists()
        )

    def test_otro_camionero_no_puede_devolver(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.otro_camionero)

        self.client.post(reverse('entregas_devolver', args=[pedido.id]))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'en_ruta')
        self.assertEqual(pedido.entregador, self.camionero)

    def test_no_se_puede_devolver_pedido_entregado(self):
        pedido = self._crear_pedido(estado='entregado', entregador=self.camionero)
        self.client.force_login(self.camionero)

        self.client.post(reverse('entregas_devolver', args=[pedido.id]))

        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, 'entregado')

    def test_telefonista_recibe_aviso_de_pedido_devuelto(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_devolver', args=[pedido.id]))

        devueltos = _pedidos_devueltos_al_pool(self.telefonista)
        self.assertIn(pedido, list(devueltos))

    def test_devolucion_del_camionero_excluye_push_a_si_mismo(self):
        """El camionero que devuelve no debe recibir la alerta del pedido que liberó."""
        from mockups.push_notifications import notificar_nuevo_pedido

        pedido = self._crear_pedido(estado='pendiente')
        notificados = notificar_nuevo_pedido(
            pedido, excluir_usuario_id=self.camionero.id, title='🚚 ¡Pedido disponible!'
        )
        self.assertEqual(notificados, 0)


class TemplatesRenderTests(FixturesMixin, TestCase):
    """Smoke tests: los templates tocados deben renderizar sin errores."""

    def test_card_camionero_muestra_badge_y_boton_devolver(self):
        self._crear_pedido(estado='en_ruta', entregador=self.camionero, con_descuento=3000)
        self.client.force_login(self.camionero)

        respuesta = self.client.get(reverse('entregas_lista'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'CON DESCUENTO')
        self.assertContains(respuesta, 'DEVOLVER PEDIDO')
        self.assertContains(respuesta, 'Descuento')

    def test_pedido_pendiente_muestra_descuento_antes_de_tomarlo(self):
        self._crear_pedido(estado='pendiente', con_descuento=3000)
        self.client.force_login(self.camionero)

        respuesta = self.client.get(reverse('entregas_lista'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'CON DESCUENTO')

    def test_telefonista_ve_aviso_de_pedido_devuelto(self):
        pedido = self._crear_pedido(estado='en_ruta', entregador=self.camionero)
        self.client.force_login(self.camionero)
        self.client.post(reverse('entregas_devolver', args=[pedido.id]))

        self.client.force_login(self.telefonista)
        respuesta = self.client.get(reverse('pedidos_mios'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'devuelto')
        self.assertContains(respuesta, 'Devuelto por camionero')

    def test_detalle_pedido_muestra_desglose_de_descuento(self):
        pedido = self._crear_pedido(estado='pendiente', con_descuento=3000)
        self.client.force_login(self.telefonista)

        respuesta = self.client.get(reverse('pedidos_detalle', args=[pedido.id]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Descuento')
        self.assertContains(respuesta, 'por balón')
        # El separador de miles depende del locale: se compara con el mismo filtro del template.
        self.assertContains(respuesta, intcomma(6000))
