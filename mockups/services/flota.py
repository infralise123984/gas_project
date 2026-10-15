"""Lógica de negocio: resumen de la flota para el panel de monitoreo.

Cruza dos cosas que viven en sitios distintos y que juntas son lo que se mira:

* **dónde está cada camión ahora** (``services.ubicacion``): en movimiento,
  detenido o sin señal;
* **qué vendió hoy** (``services.camionero``): pedidos entregados y cuánto de eso
  fue tarreo.

El cruce es el punto: "detenido hace una hora" solo tiene sentido al lado de
"cero pedidos y cero tarreo". Sin la segunda mitad, el panel invita a llamar a
quien está descargando en bodega.

El costo por camionero es una consulta de actividad; el estado se resuelve en
una sola pasada para todos (``estado_de_flota``). Con la flota real —unos pocos
camiones— el refresco cada 30 s no se nota. Si algún día crece, el siguiente
paso es agrupar la actividad por trabajador en una consulta.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from django.db.models import Sum
from django.utils import timezone

from mockups.models import Usuario
from mockups.services.camionero import (
    queryset_actividad_camionero_dia,
    stats_ventas_camionero,
)
from mockups.services.ubicacion import estado_de_flota

#: Quiénes pueden mirar el panel (docs/PLAN_MONITOREO_FLOTA.md §3).
#: El bodeguero no participa y el camionero nunca ve a sus pares.
ROLES_PANEL_FLOTA = ('telefonista', 'jefe', 'admin')


def _actividad_del_dia(camionero, dia) -> dict:
    """Pedidos entregados y monto del día, con el tarreo desglosado."""
    entregados = queryset_actividad_camionero_dia(camionero, dia).filter(
        estado='entregado'
    )
    stats = stats_ventas_camionero(entregados)
    monto_tarreo = entregados.filter(origen='tarreo').aggregate(
        total=Sum('monto_total')
    )['total']

    return {
        'pedidos': stats['total_entregas'],
        'monto': int(stats['total_monto']),
        'monto_tarreo': int(monto_tarreo or 0),
        'kilos': int(stats['total_kilos']),
    }


def _tramo_json(tramo) -> dict:
    """Tramo en la hora del operador, no en UTC: el panel lo lee una persona."""
    return {
        'inicio': timezone.localtime(tramo.inicio).isoformat(),
        'fin': timezone.localtime(tramo.fin).isoformat(),
        'tipo': tramo.tipo,
        'minutos': tramo.minutos,
        'lat': tramo.lat,
        'lng': tramo.lng,
    }


def _camion_json(item: dict, actividad: dict) -> dict:
    camionero = item['camionero']
    estado = item['estado']
    return {
        'id': camionero.pk,
        'nombre': camionero.get_full_name().strip() or camionero.username,
        'username': camionero.username,
        'estado': estado.estado,
        'minutos': estado.minutos,
        'lat': estado.lat,
        'lng': estado.lng,
        'desde': timezone.localtime(estado.desde).isoformat() if estado.desde else None,
        'pedidos': actividad['pedidos'],
        'monto': actividad['monto'],
        'monto_tarreo': actividad['monto_tarreo'],
        'kilos': actividad['kilos'],
        'tramos': [_tramo_json(tramo) for tramo in item['tramos']],
        # Pares [lat, lng] y no objetos: el panel dibuja la línea y no necesita
        # nada más, así el refresco cada 30 s mueve la mitad de bytes.
        'rastro': item['rastro'],
    }


def resumen_flota(*, ahora: Optional[datetime] = None) -> list:
    """Un elemento por camionero activo, listo para serializar.

    Solo camioneros activos: uno dado de baja no debe aparecer como "sin señal",
    que se leería como un problema de red.
    """
    ahora = ahora or timezone.localtime()
    camioneros = list(
        Usuario.objects.filter(rol='camionero', is_active=True).order_by(
            'first_name', 'username'
        )
    )
    if not camioneros:
        return []

    estados = estado_de_flota(camioneros, ahora=ahora)
    return [
        _camion_json(item, _actividad_del_dia(item['camionero'], ahora.date()))
        for item in estados
    ]
