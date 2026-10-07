"""
Pruebas de lógica y matemática de negocio (baseline antes de refactors críticos).

Ejecutar:
    python manage.py test mockups.tests.pedidos.test_logica_negocio -v 2

Estas pruebas documentan el comportamiento correcto de:
- Totales de pedidos (monto / ganancia)
- Kilos del camionero (aggregate vs suma manual)
- Sincronización de sobres (montos y cantidades por balón)
- Coherencia entre get_pedidos_queryset_para_sobre y calcular_desde_pedidos

Si un refactor rompe estas pruebas, los números operativos cambiaron: revisar antes de mergear.
"""

from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from mockups.models import Pedido
from mockups.tests.base import (
    TZ_CHILE,
    crear_balon_11kg,
    crear_balon_5kg,
    crear_pedido,
    crear_sobre,
    crear_usuario,
    dt_chile,
)
from mockups.views import (
    _kilos_de_pedido,
    _stats_dia_camionero,
    get_pedidos_queryset_para_sobre,
    sincronizar_sobre_desde_pedidos,
    stats_ventas_camionero,
)


class LogicaNegocioFixturesMixin:
    """Datos mínimos compartidos para escenarios de cuadratura."""

    @classmethod
    def setUpTestData(cls):
        cls.bodeguero = crear_usuario(
            'bodeguero', 'bodeguero_test', password='test',
            first_name='Bode', last_name='Guero',
        )
        cls.camionero = crear_usuario(
            'camionero', 'camionero_test', password='test',
            first_name='Camion', last_name='Ero',
        )
        cls.otro_camionero = crear_usuario('camionero', 'camionero2_test', password='test')

        cls.balon_11 = crear_balon_11kg()
        cls.balon_5 = crear_balon_5kg()

        cls.fecha_dia = date(2026, 6, 15)
        cls.mediodia_chile = dt_chile(cls.fecha_dia)

    def _crear_pedido_entregado(
        self,
        *,
        registrador,
        entregador=None,
        origen='local',
        fecha=None,
        lineas=None,
    ):
        """Crea pedido entregado con detalles y totales calculados."""
        return crear_pedido(
            registrador=registrador,
            entregador=entregador or registrador,
            estado='entregado',
            origen=origen,
            fecha=fecha or self.mediodia_chile,
            lineas=lineas or [(self.balon_11, 2)],
        )

    def _crear_sobre_bodega(self):
        return crear_sobre(
            tipo='bodega',
            fecha_correspondiente=self.fecha_dia,
            creado_por=self.bodeguero,
            trabajador=None,
        )

    def _crear_sobre_camion(self, camionero=None):
        return crear_sobre(
            tipo='camion',
            fecha_correspondiente=self.fecha_dia,
            creado_por=self.bodeguero,
            trabajador=camionero or self.camionero,
        )

    def _monto_esperado_pedidos(self, queryset):
        return queryset.aggregate(total=Sum('monto_total'))['total'] or 0


class PedidoTotalesTests(LogicaNegocioFixturesMixin, TestCase):
    def test_calcular_totales_suma_detalles(self):
        pedido = self._crear_pedido_entregado(
            registrador=self.bodeguero,
            lineas=[(self.balon_11, 2), (self.balon_5, 1)],
        )
        esperado_monto = Decimal(2 * 12000 + 1 * 7000)
        esperado_ganancia = Decimal(
            2 * (12000 - 8000) + 1 * (7000 - 4000)
        )
        self.assertEqual(pedido.monto_total, esperado_monto)
        self.assertEqual(pedido.ganancia_total, esperado_ganancia)


class KilosCamioneroTests(LogicaNegocioFixturesMixin, TestCase):
    def test_stats_kilos_coincide_con_suma_manual(self):
        p1 = self._crear_pedido_entregado(
            registrador=self.camionero,
            entregador=self.camionero,
            origen='telefono',
            lineas=[(self.balon_11, 2)],
        )
        p2 = self._crear_pedido_entregado(
            registrador=self.camionero,
            entregador=self.camionero,
            origen='tarreo',
            lineas=[(self.balon_5, 3)],
        )
        entregados = Pedido.objects.filter(pk__in=[p1.pk, p2.pk])
        stats = stats_ventas_camionero(entregados)

        kilos_manual = sum(_kilos_de_pedido(p) for p in entregados.prefetch_related('detalles__balon'))
        self.assertEqual(stats['total_kilos'], kilos_manual)
        self.assertEqual(stats['total_kilos'], 2 * 11 + 3 * 5)

    def test_stats_dia_desglose_domicilio_tarreo_suma_total(self):
        self._crear_pedido_entregado(
            registrador=self.camionero,
            entregador=self.camionero,
            origen='telefono',
            lineas=[(self.balon_11, 1)],
        )
        self._crear_pedido_entregado(
            registrador=self.camionero,
            entregador=self.camionero,
            origen='tarreo',
            lineas=[(self.balon_5, 2)],
        )
        stats = _stats_dia_camionero(self.camionero, self.fecha_dia)
        self.assertEqual(
            stats['kilos_domicilio'] + stats['kilos_tarreo'],
            stats['kilos'],
        )
        self.assertEqual(stats['kilos'], 11 + 10)


class SobreSincronizacionTests(LogicaNegocioFixturesMixin, TestCase):
    def test_sobre_bodega_monto_calculado_suma_pedidos_local(self):
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_11, 1)],
        )
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_5, 2)],
        )
        # Pedido domicilio del mismo día no debe entrar al sobre bodega
        self._crear_pedido_entregado(
            registrador=self.camionero,
            entregador=self.camionero,
            origen='telefono',
            lineas=[(self.balon_11, 5)],
        )

        sobre = self._crear_sobre_bodega()
        sincronizar_sobre_desde_pedidos(sobre)
        sobre.refresh_from_db()

        pedidos_bodega = get_pedidos_queryset_para_sobre(sobre)
        self.assertEqual(sobre.monto_calculado_app, self._monto_esperado_pedidos(pedidos_bodega))
        self.assertEqual(pedidos_bodega.count(), 2)

        linea_11 = sobre.lineas.get(balon=self.balon_11)
        linea_5 = sobre.lineas.get(balon=self.balon_5)
        self.assertEqual(linea_11.cantidad_calculada, 1)
        self.assertEqual(linea_5.cantidad_calculada, 2)

    def test_sobre_camion_solo_pedidos_del_entregador(self):
        self._crear_pedido_entregado(
            registrador=self.camionero,
            entregador=self.camionero,
            origen='telefono',
            lineas=[(self.balon_11, 2)],
        )
        self._crear_pedido_entregado(
            registrador=self.otro_camionero,
            entregador=self.otro_camionero,
            origen='telefono',
            lineas=[(self.balon_11, 9)],
        )

        sobre = self._crear_sobre_camion(self.camionero)
        sincronizar_sobre_desde_pedidos(sobre)
        sobre.refresh_from_db()

        pedidos_camion = get_pedidos_queryset_para_sobre(sobre)
        self.assertEqual(pedidos_camion.count(), 1)
        self.assertEqual(sobre.monto_calculado_app, self._monto_esperado_pedidos(pedidos_camion))

        linea = sobre.lineas.get(balon=self.balon_11)
        self.assertEqual(linea.cantidad_calculada, 2)

    def test_sincronizar_preserva_declarado_ajustado_manualmente(self):
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_11, 2)],
        )
        sobre = self._crear_sobre_bodega()
        sincronizar_sobre_desde_pedidos(sobre)

        linea = sobre.lineas.get(balon=self.balon_11)
        linea.cantidad_declarada = 1
        linea.save(update_fields=['cantidad_declarada'])

        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_11, 1)],
        )
        sincronizar_sobre_desde_pedidos(sobre)
        linea.refresh_from_db()

        self.assertEqual(linea.cantidad_calculada, 3)
        self.assertEqual(linea.cantidad_declarada, 1, 'Debe conservar el ajuste manual del bodeguero')

    def test_sincronizar_no_toca_declarada_aunque_coincida_con_calculada_anterior(self):
        """Tras guardar borrador, la declarada queda fija aunque lleguen más pedidos."""
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_11, 2)],
        )
        sobre = self._crear_sobre_bodega()
        sincronizar_sobre_desde_pedidos(sobre)
        linea = sobre.lineas.get(balon=self.balon_11)
        self.assertEqual(linea.cantidad_calculada, 2)
        self.assertEqual(linea.cantidad_declarada, 2)

        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_11, 1)],
        )
        sincronizar_sobre_desde_pedidos(sobre)
        linea.refresh_from_db()

        self.assertEqual(linea.cantidad_calculada, 3)
        self.assertEqual(linea.cantidad_declarada, 2)

    def test_monto_declarado_y_diferencia_cuadran(self):
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            lineas=[(self.balon_11, 2)],
        )
        sobre = self._crear_sobre_bodega()
        sincronizar_sobre_desde_pedidos(sobre)
        sobre.refresh_from_db()

        monto_declarado_esperado = sum(
            linea.cantidad_declarada * linea.precio_venta_unitario
            for linea in sobre.lineas.all()
        )
        self.assertEqual(sobre.monto_declarado, monto_declarado_esperado)
        self.assertEqual(sobre.diferencia, sobre.monto_declarado - sobre.monto_calculado_app)


class CoherenciaFechaSobreTests(LogicaNegocioFixturesMixin, TestCase):
    """
    Documenta que get_pedidos_queryset_para_sobre (rangos UTC) y
    calcular_desde_pedidos (fecha__date) deben coincidir en el día laboral típico.
    """

    def test_sobre_bodega_usa_queryset_no_calcular_desde_pedidos(self):
        """
        La sync real (sincronizar_sobre_desde_pedidos) usa get_pedidos_queryset_para_sobre.
        calcular_desde_pedidos en el modelo filtra registrador=trabajador, pero bodega tiene
        trabajador=None — hoy devuelve 0. Cualquier refactor debe alinear ambos caminos.
        """
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            fecha=self.mediodia_chile,
            lineas=[(self.balon_11, 1)],
        )
        sobre = self._crear_sobre_bodega()

        monto_queryset = self._monto_esperado_pedidos(get_pedidos_queryset_para_sobre(sobre))
        monto_modelo = sobre.calcular_desde_pedidos()

        self.assertEqual(monto_queryset, Decimal('12000'))
        self.assertEqual(monto_modelo, 0, 'Deuda conocida: no usar calcular_desde_pedidos en bodega')

    def test_pedido_antes_de_medianoche_pertenece_al_dia_chile(self):
        """Pedido 23:30 Chile del 15-jun debe contar para sobre del 15-jun."""
        tarde_chile = timezone.make_aware(
            datetime.combine(self.fecha_dia, time(23, 30)),
            TZ_CHILE,
        )
        self._crear_pedido_entregado(
            registrador=self.bodeguero,
            origen='local',
            fecha=tarde_chile,
            lineas=[(self.balon_5, 1)],
        )
        sobre = self._crear_sobre_bodega()
        sincronizar_sobre_desde_pedidos(sobre)

        linea = sobre.lineas.get(balon=self.balon_5)
        self.assertEqual(linea.cantidad_calculada, 1)