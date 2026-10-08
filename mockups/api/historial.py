"""Resúmenes del camionero: día, mes y detalle de un día (docs/API_MOVIL.md §7.5).

Réplica de tres consultas de la web, sin lógica nueva:

* ``resumen_hoy``    ← ``mis_entregas_camionero``
* ``historial_mes``  ← ``camionero_historial``
* ``historial_dia``  ← ``camionero_historial_dia``

Las tres usan los mismos servicios que la web (``services.camionero``), así que
los números no pueden diferir de la PWA. El mes se recorre día por día como en
la web: son ~30 días de consultas, y por eso el límite de este alcance es más
estrecho que el del listado.
"""

import logging
import re
from calendar import monthrange
from datetime import datetime

from django.views.decorators.cache import never_cache

from mockups.api import respuestas
from mockups.api.acceso import ROLES_CAMIONERO, acceso_api
from mockups.api.limites import limitar
from mockups.api.serializadores import serializar_pedido
from mockups.services.camionero import (
    queryset_actividad_camionero_dia,
    stats_dia_camionero,
    stats_ventas_camionero,
)
from mockups.utils.fechas import (
    MESES_ES_CAMIONERO,
    navegacion_mes,
    parse_mes_param,
    today_chile,
)

# Lectura barata: una sola pasada por la actividad del día.
LIMITE_RESUMEN = {'peticiones': 60, 'ventana_segundos': 60}

# El historial de un mes recorre el mes entero día por día: es la consulta más
# cara del API, así que va la mitad de estrecha que las acciones.
LIMITE_HISTORIAL = {'peticiones': 20, 'ventana_segundos': 60}


@never_cache
@acceso_api(ROLES_CAMIONERO)
@limitar('resumen_hoy', **LIMITE_RESUMEN)
def resumen_hoy(request):
    """Totales y actividad del día del camionero (§7.5).

    Los totales salen de los **entregados** (`stats_ventas_camionero`), pero la
    lista ``pedidos`` es toda la actividad del día, cancelados incluidos: es lo
    que muestra "Mis Entregas Hoy" en la web. Cancelar no suma plata ni kilos,
    pero el pedido tiene que verse.
    """
    hoy = today_chile()
    pedidos_hoy = queryset_actividad_camionero_dia(request.user, hoy)
    stats = stats_ventas_camionero(pedidos_hoy.filter(estado='entregado'))

    return respuestas.ok(
        {
            'hoy': hoy.isoformat(),
            'entregas': stats['total_entregas'],
            'monto': int(stats['total_monto'] or 0),
            'kilos': int(stats['total_kilos'] or 0),
            'pedidos': [serializar_pedido(pedido) for pedido in pedidos_hoy],
        }
    )


def _fila_dia(fecha_dia, hoy, datos):
    """Una fila del mes, con los mismos campos que usa la plantilla web."""
    return {
        'fecha': fecha_dia.isoformat(),
        'es_hoy': fecha_dia == hoy,
        'tiene_venta': datos['entregas'] > 0,
        'entregas': datos['entregas'],
        'monto': datos['monto'],
        'kilos': datos['kilos'],
        'kilos_domicilio': datos['kilos_domicilio'],
        'kilos_tarreo': datos['kilos_tarreo'],
    }


@never_cache
@acceso_api(ROLES_CAMIONERO)
@limitar('historial', **LIMITE_HISTORIAL)
def historial_mes(request):
    """Resumen del mes, día por día, en orden descendente (§7.5).

    Sin ``?mes=`` devuelve el mes actual. Con un ``?mes=`` mal formado responde
    ``400``: la web cae al mes actual en silencio, pero acá eso sería mostrarle
    al camionero un mes que no pidió, y él no tendría cómo notarlo.
    """
    mes_pedido = request.GET.get('mes')
    if mes_pedido:
        anio, mes = parse_mes_param(request, default_hoy=False)
        if anio is None:
            return respuestas.error(
                'validacion', mensaje='El mes debe venir como AAAA-MM.'
            )
    else:
        hoy = today_chile()
        anio, mes = hoy.year, hoy.month

    _, ultimo_dia = monthrange(anio, mes)
    hoy = today_chile()
    mes_anterior, mes_siguiente = navegacion_mes(anio, mes)

    dias = []
    totales = {'entregas': 0, 'monto': 0, 'kilos': 0}
    dias_con_venta = 0

    for dia_num in range(ultimo_dia, 0, -1):
        fecha_dia = datetime(anio, mes, dia_num).date()
        datos = stats_dia_camionero(request.user, fecha_dia)

        if datos['entregas'] > 0:
            dias_con_venta += 1
            totales['entregas'] += datos['entregas']
            totales['monto'] += datos['monto']
            totales['kilos'] += datos['kilos']

        dias.append(_fila_dia(fecha_dia, hoy, datos))

    return respuestas.ok(
        {
            'mes': f'{anio:04d}-{mes:02d}',
            'mes_nombre': MESES_ES_CAMIONERO[mes],
            'mes_anterior': f'{mes_anterior[0]:04d}-{mes_anterior[1]:02d}',
            'mes_siguiente': f'{mes_siguiente[0]:04d}-{mes_siguiente[1]:02d}',
            # No se puede navegar al futuro: no hay nada que mostrar.
            'mes_siguiente_habilitado': (
                mes_siguiente[0] < hoy.year
                or (mes_siguiente[0] == hoy.year and mes_siguiente[1] <= hoy.month)
            ),
            'dias_con_venta': dias_con_venta,
            'totales': totales,
            'dias': dias,
        }
    )


# El contrato dice AAAA-MM-DD exacto. `strptime` es laxo y aceptaría tanto
# '2026-1-1' como '26-10-07', así que la forma se comprueba antes.
FORMATO_FECHA = re.compile(r'^\d{4}-\d{2}-\d{2}$')


@never_cache
@acceso_api(ROLES_CAMIONERO)
@limitar('historial', **LIMITE_HISTORIAL)
def historial_dia(request, fecha):
    """Detalle de las ventas de un día, con sus pedidos entregados (§7.5).

    Los pedidos se mandan con el objeto ``Pedido`` del §7.2, que ya trae sus
    ``kilos``: la web arma un resumen aparte porque su plantilla no los calcula,
    la app no necesita ese envoltorio.
    """
    if not FORMATO_FECHA.match(fecha):
        return respuestas.error(
            'validacion', mensaje='La fecha debe venir como AAAA-MM-DD.'
        )

    try:
        fecha_dia = datetime.strptime(fecha, '%Y-%m-%d').date()
    except ValueError:
        return respuestas.error(
            'validacion', mensaje='La fecha debe venir como AAAA-MM-DD.'
        )

    datos = stats_dia_camionero(request.user, fecha_dia)

    return respuestas.ok(
        {
            'fecha': fecha_dia.isoformat(),
            'mes': f'{fecha_dia.year:04d}-{fecha_dia.month:02d}',
            'mes_nombre': MESES_ES_CAMIONERO[fecha_dia.month],
            'es_hoy': fecha_dia == today_chile(),
            'entregas': datos['entregas'],
            'monto': datos['monto'],
            'kilos': datos['kilos'],
            'kilos_domicilio': datos['kilos_domicilio'],
            'kilos_tarreo': datos['kilos_tarreo'],
            'pedidos': [
                serializar_pedido(pedido) for pedido in datos['pedidos_entregados']
            ],
        }
    )
