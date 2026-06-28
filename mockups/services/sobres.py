"""Lógica de negocio: sincronización y querysets de sobres diarios."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from mockups.models import LineaSobre, Pedido
from mockups.services.catalogos import get_balones_activos_ordenados


def get_pedidos_queryset_para_sobre(sobre):
    """Construye el queryset de pedidos que alimenta el sobre."""
    tz_chile = ZoneInfo('America/Santiago')
    tz_utc = ZoneInfo('UTC')
    fecha_para_calcular = (
        sobre.fecha_correspondiente
        or sobre.fecha.astimezone(tz_chile).date()
    )

    inicio_dia = timezone.make_aware(
        datetime.combine(fecha_para_calcular, time.min), tz_chile
    ).astimezone(tz_utc)
    fin_dia = timezone.make_aware(
        datetime.combine(fecha_para_calcular, time.max), tz_chile
    ).astimezone(tz_utc)

    if sobre.tipo == 'bodega':
        return Pedido.objects.filter(
            fecha__gte=inicio_dia,
            fecha__lte=fin_dia,
            origen='local',
            estado='entregado',
        )

    return Pedido.objects.filter(
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia,
        estado='entregado',
        entregador=sobre.trabajador,
    )


def sincronizar_sobre_desde_pedidos(sobre, crear_lineas_faltantes=True):
    """
    Refresca cantidades calculadas del sobre abierto usando los pedidos vigentes.
    No modifica cantidad_declarada en líneas existentes.
    """
    if not sobre.pk or sobre.cerrado:
        return False

    balones_activos = list(get_balones_activos_ordenados())
    pedidos_qs = get_pedidos_queryset_para_sobre(sobre)
    cantidades_por_balon = {
        item['detalles__balon']: int(item['total'] or 0)
        for item in pedidos_qs.values('detalles__balon').annotate(total=Sum('detalles__cantidad'))
        if item['detalles__balon']
    }

    cambios = False
    lineas_existentes = {
        linea.balon_id: linea
        for linea in sobre.lineas.select_related('balon')
    }

    with transaction.atomic():
        for balon in balones_activos:
            qty_calc = cantidades_por_balon.get(balon.id, 0)
            linea = lineas_existentes.get(balon.id)

            if not linea:
                if not crear_lineas_faltantes:
                    continue
                precio = balon.precio_local if sobre.tipo == 'bodega' else balon.precio_domicilio
                LineaSobre.objects.create(
                    sobre=sobre,
                    balon=balon,
                    cantidad_calculada=qty_calc,
                    cantidad_declarada=qty_calc,
                    precio_venta_unitario=precio,
                )
                cambios = True
                continue

            old_calc = int(linea.cantidad_calculada or 0)
            update_fields = []

            if old_calc != qty_calc:
                linea.cantidad_calculada = qty_calc
                update_fields.append('cantidad_calculada')

            if update_fields:
                linea.save(update_fields=update_fields)
                cambios = True

        sobre.refresh_from_db()
        monto_calculado_app = pedidos_qs.aggregate(total=Sum('monto_total'))['total'] or 0
        monto_declarado = sum(
            (linea.cantidad_declarada or 0) * (linea.precio_venta_unitario or 0)
            for linea in sobre.lineas.all()
        )
        diferencia = monto_declarado - monto_calculado_app

        sobre_update_fields = []
        if sobre.monto_calculado_app != monto_calculado_app:
            sobre.monto_calculado_app = monto_calculado_app
            sobre_update_fields.append('monto_calculado_app')
        if sobre.monto_declarado != monto_declarado:
            sobre.monto_declarado = monto_declarado
            sobre_update_fields.append('monto_declarado')
        if sobre.diferencia != diferencia:
            sobre.diferencia = diferencia
            sobre_update_fields.append('diferencia')

        if sobre_update_fields:
            sobre.save(update_fields=sobre_update_fields)
            cambios = True

    return cambios