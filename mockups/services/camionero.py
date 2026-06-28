"""Lógica de negocio: actividad y estadísticas del camionero."""

from django.db.models import Count, F, Q, Sum

from mockups.models import DetallePedido, Pedido
from mockups.utils.fechas import rango_dia_chile


def filtro_actividad_camionero_q(usuario):
    """Pedidos visibles del camionero (entregas + tarreo propio)."""
    return Q(entregador=usuario) | Q(
        registrador=usuario,
        origen__in=['tarreo', 'venta_extra'],
    )


def queryset_actividad_camionero_dia(usuario, fecha_dia):
    """Pedidos del camionero en un día (todas las actividades visibles)."""
    inicio, fin = rango_dia_chile(fecha_dia)
    return (
        Pedido.objects.filter(
            fecha__gte=inicio,
            fecha__lt=fin,
        )
        .filter(filtro_actividad_camionero_q(usuario))
        .select_related('registrador')
        .prefetch_related('detalles__balon')
        .order_by('-fecha')
    )


def stats_ventas_camionero(queryset_entregados):
    """Totales de entregas, monto y kilos para un queryset de pedidos entregados."""
    stats = queryset_entregados.aggregate(
        total_entregas=Count('id'),
        total_monto=Sum('monto_total'),
    )
    total_kilos = (
        DetallePedido.objects.filter(pedido__in=queryset_entregados.values('pk'))
        .aggregate(total=Sum(F('cantidad') * F('balon__peso_neto_gas')))['total']
        or 0
    )
    return {
        'total_entregas': stats['total_entregas'] or 0,
        'total_monto': stats['total_monto'] or 0,
        'total_kilos': total_kilos,
    }


def kilos_de_pedido(pedido):
    """Kilos de un pedido con detalles precargados."""
    return sum(
        (detalle.cantidad or 0) * (detalle.balon.peso_neto_gas or 0)
        for detalle in pedido.detalles.all()
    )


def stats_dia_camionero(usuario, fecha_dia):
    """
    Mismo flujo que mis_entregas_camionero, aplicado a cualquier día.
    pedidos del día → entregados → stats + desglose domicilio/tarreo.
    """
    pedidos_dia = queryset_actividad_camionero_dia(usuario, fecha_dia)
    pedidos_entregados = pedidos_dia.filter(estado='entregado')
    stats = stats_ventas_camionero(pedidos_entregados)

    kilos_domicilio = 0
    kilos_tarreo = 0
    for pedido in pedidos_entregados:
        kilos = kilos_de_pedido(pedido)
        if pedido.origen == 'tarreo':
            kilos_tarreo += kilos
        else:
            kilos_domicilio += kilos

    return {
        'entregas': stats['total_entregas'],
        'kilos': int(stats['total_kilos'] or 0),
        'monto': int(stats['total_monto'] or 0),
        'kilos_domicilio': int(kilos_domicilio),
        'kilos_tarreo': int(kilos_tarreo),
        'pedidos_entregados': pedidos_entregados,
    }


# Alias con guión bajo para compatibilidad con tests y _legacy
_kilos_de_pedido = kilos_de_pedido
_stats_dia_camionero = stats_dia_camionero