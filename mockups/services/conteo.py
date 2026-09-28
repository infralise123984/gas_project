"""Lógica de dominio del conteo diario de balones (bodega).

Sin dependencias de HTTP: las vistas orquestan, aquí vive la regla de negocio.
"""

from django.db import transaction

from mockups.models import ConteoDiarioBalon, LineaConteoBalon, TipoBalon
from mockups.utils.fechas import today_chile

CAMPOS_MOVIMIENTO = ('llenos_entran', 'llenos_salen', 'vacios_entran', 'vacios_salen')

CAMPOS_SALDO = (
    'stock_inicial_llenos',
    'stock_inicial_vacios',
    'stock_final_llenos',
    'stock_final_vacios',
)


def es_editable(conteo: ConteoDiarioBalon) -> bool:
    """Un conteo solo se edita el mismo día de su fecha, en calendario Chile."""
    return conteo.fecha == today_chile()


def _conteo_anterior(fecha):
    """Conteo más reciente anterior a `fecha` (o None si es el primero)."""
    return (
        ConteoDiarioBalon.objects
        .filter(fecha__lt=fecha)
        .order_by('-fecha')
        .first()
    )


def saldos_iniciales(fecha):
    """Saldos arrastrados del conteo anterior: {balon_id: (llenos, vacios)}.

    Si no hay conteo anterior devuelve un dict vacío, y cada línea arranca en 0.
    """
    anterior = _conteo_anterior(fecha)
    if anterior is None:
        return {}
    return {
        linea.balon_id: (linea.stock_final_llenos, linea.stock_final_vacios)
        for linea in anterior.lineas.all()
    }


def obtener_o_crear_conteo(fecha, usuario):
    """Devuelve (conteo, creado) para la fecha dada.

    Al crearlo genera una línea por cada tipo de balón activo, arrastrando el
    saldo final del conteo anterior como saldo inicial.
    """
    with transaction.atomic():
        conteo, creado = ConteoDiarioBalon.objects.get_or_create(
            fecha=fecha, defaults={'creado_por': usuario}
        )
        if not creado:
            return conteo, False

        saldos = saldos_iniciales(fecha)
        balones = TipoBalon.objects.filter(activo=True).order_by('-peso_neto_gas', 'tipo_gas')

        LineaConteoBalon.objects.bulk_create([
            LineaConteoBalon(
                conteo=conteo,
                balon=balon,
                stock_inicial_llenos=saldos.get(balon.id, (0, 0))[0],
                stock_inicial_vacios=saldos.get(balon.id, (0, 0))[1],
            )
            for balon in balones
        ])

    return conteo, True


def resumen(lineas) -> dict:
    """Totales agregados de las líneas de un conteo. Función pura."""

    def total(campo):
        return sum(getattr(linea, campo) for linea in lineas)

    return {
        **{campo: total(campo) for campo in CAMPOS_MOVIMIENTO},
        **{campo: total(campo) for campo in CAMPOS_SALDO},
        'hay_descuadre': any(linea.tiene_descuadre for linea in lineas),
    }
