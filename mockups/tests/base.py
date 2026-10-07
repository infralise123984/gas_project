"""Utilidades compartidas por la suite de tests de ``mockups``.

Centraliza la creación de datos de prueba (usuarios por rol, balones de
catálogo, pedidos con líneas y sobres diarios) para no repetir la misma
construcción en cada módulo de tests.

Solo expone funciones puras de fábrica: no hay estado global mutable, de modo
que cada ``TestCase`` conserva el aislamiento transaccional que Django revierte
al terminar el test.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone

from mockups.models import DetallePedido, Pedido, SobreDiario, TipoBalon

Usuario = get_user_model()

TZ_CHILE = ZoneInfo('America/Santiago')

#: Contraseña por defecto de los usuarios de prueba (solo entorno de tests).
PASSWORD_TEST = 'test12345'

#: Precios de catálogo (compra, local, domicilio) reutilizados por los tests.
PRECIOS_BALON_11KG = (8000, 12000, 14000)
PRECIOS_BALON_5KG = (4000, 7000, 8500)


def crear_usuario(
    rol: str,
    username: Optional[str] = None,
    *,
    password: str = PASSWORD_TEST,
    **extra,
) -> Usuario:
    """Crea un ``Usuario`` con el ``rol`` indicado.

    ``username`` se autogenera a partir del rol si no se indica; pásalo explícito
    cuando una misma clase necesite dos usuarios del mismo rol.
    """
    return Usuario.objects.create_user(
        username=username or f'{rol}_test',
        password=password,
        rol=rol,
        **extra,
    )


def crear_balon_11kg(*, nombre: str = 'Balón 11 kg', activo: bool = True, **extra) -> TipoBalon:
    """Balón de 11 kg con precios de catálogo (8000 / 12000 / 14000)."""
    precio_compra, precio_local, precio_domicilio = PRECIOS_BALON_11KG
    return TipoBalon.objects.create(
        nombre=nombre,
        peso_neto_gas=11,
        tipo_gas='normal',
        precio_compra=precio_compra,
        precio_local=precio_local,
        precio_domicilio=precio_domicilio,
        activo=activo,
        **extra,
    )


def crear_balon_5kg(*, nombre: str = 'Balón 5 kg', activo: bool = True, **extra) -> TipoBalon:
    """Balón de 5 kg con precios de catálogo (4000 / 7000 / 8500)."""
    precio_compra, precio_local, precio_domicilio = PRECIOS_BALON_5KG
    return TipoBalon.objects.create(
        nombre=nombre,
        peso_neto_gas=5,
        tipo_gas='normal',
        precio_compra=precio_compra,
        precio_local=precio_local,
        precio_domicilio=precio_domicilio,
        activo=activo,
        **extra,
    )


def dt_chile(dia: date, hora: int = 12, minuto: int = 0) -> datetime:
    """``datetime`` aware en zona horaria de Chile para el día y hora indicados."""
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)), TZ_CHILE)


def crear_pedido(
    *,
    registrador,
    estado: str = 'pendiente',
    origen: str = 'telefono',
    entregador=None,
    fecha: Optional[datetime] = None,
    metodo_pago: str = 'efectivo',
    lineas: Optional[Iterable[Sequence]] = None,
    calcular_totales: bool = True,
    **extra,
) -> Pedido:
    """Crea un ``Pedido`` con sus ``DetallePedido`` y totales calculados.

    ``lineas`` es un iterable de ``(balon, cantidad)`` o ``(balon, cantidad,
    descuento_unitario)``. El precio de venta y de compra de cada línea se toma
    del catálogo del balón (precio local si ``origen == 'local'``, domicilio en
    cualquier otro caso). Con ``calcular_totales=False`` el pedido se devuelve
    tal cual, útil cuando el test fija ``monto_total`` a mano.
    """
    datos = {
        'registrador': registrador,
        'entregador': entregador,
        'origen': origen,
        'estado': estado,
        'metodo_pago': metodo_pago,
    }
    if fecha is not None:
        datos['fecha'] = fecha
    datos.update(extra)
    pedido = Pedido.objects.create(**datos)

    for linea in lineas or ():
        balon, cantidad = linea[0], linea[1]
        descuento = linea[2] if len(linea) > 2 else 0
        DetallePedido.objects.create(
            pedido=pedido,
            balon=balon,
            cantidad=cantidad,
            precio_venta_unitario=(
                balon.precio_local if origen == 'local' else balon.precio_domicilio
            ),
            precio_compra_unitario=balon.precio_compra,
            descuento_unitario=descuento,
        )

    if calcular_totales:
        pedido.calcular_totales()
        pedido.refresh_from_db()
    return pedido


def crear_sobre(
    *,
    tipo: str,
    fecha_correspondiente: date,
    creado_por,
    trabajador=None,
    cerrado: bool = False,
) -> SobreDiario:
    """Crea un ``SobreDiario``; si ``cerrado`` se marca cerrado (sin reabrir)."""
    sobre = SobreDiario.objects.create(
        tipo=tipo,
        fecha_correspondiente=fecha_correspondiente,
        creado_por=creado_por,
        trabajador=trabajador,
    )
    if cerrado:
        sobre.cerrado = True
        sobre.save(update_fields=['cerrado'])
    return sobre
