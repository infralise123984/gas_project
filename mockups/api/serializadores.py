"""Serializadores de modelos a los objetos JSON del contrato (docs/API_MOVIL.md §7).

Solo formato: no hay lógica de negocio. Los montos salen como enteros en pesos
(el separador de miles lo pone la app) y las fechas en ISO 8601 con el desfase
real de Chile (que cambia con el horario de verano).
"""

from mockups.services.camionero import kilos_de_pedido
from mockups.utils.fechas import TZ_CHILE


def _entero(valor):
    """Decimal/None -> entero en pesos. La app nunca recibe formato ni decimales."""
    return int(valor or 0)


def _fecha_chile(valor):
    return valor.astimezone(TZ_CHILE).isoformat() if valor else None


def serializar_usuario(usuario):
    return {
        'id': usuario.pk,
        'username': usuario.username,
        'nombre': usuario.get_full_name() or usuario.username,
        'rol': usuario.rol,
        'rol_etiqueta': usuario.get_rol_display(),
        'totp_activo': usuario.totp_activo,
    }


def serializar_pedido(pedido):
    """Objeto Pedido del §7.2.

    Requiere ``prefetch_related('detalles__balon')``: tanto ``kilos_de_pedido``
    como las líneas recorren los detalles, y sin prefetch cada pedido dispara
    consultas adicionales.
    """
    return {
        'id': pedido.pk,
        'estado': pedido.estado,
        'estado_etiqueta': pedido.get_estado_display(),
        'origen': pedido.origen,
        'origen_etiqueta': pedido.get_origen_display(),
        'sector': pedido.sector,
        'direccion_entrega': pedido.direccion_entrega,
        'metodo_pago': pedido.metodo_pago,
        'fecha': _fecha_chile(pedido.fecha),
        'monto_total': _entero(pedido.monto_total),
        'subtotal_bruto': _entero(pedido.subtotal_bruto),
        'descuento_total': _entero(pedido.descuento_total),
        'tiene_descuento': bool(pedido.tiene_descuento),
        'kilos': int(kilos_de_pedido(pedido)),
        'lineas': [serializar_linea(detalle) for detalle in pedido.detalles.all()],
    }


def serializar_linea(detalle):
    return {
        'balon_id': detalle.balon_id,
        'balon_nombre': detalle.balon.nombre,
        'peso_neto_gas': detalle.balon.peso_neto_gas,
        'cantidad': detalle.cantidad,
        'precio_venta_unitario': _entero(detalle.precio_venta_unitario),
        'descuento_unitario': _entero(detalle.descuento_unitario),
        'subtotal': _entero(detalle.subtotal),
        'subtotal_neto': _entero(detalle.subtotal_neto),
    }


def serializar_balon(balon):
    """Objeto balón del catálogo (§7.5).

    ``precio_domicilio`` es el precio que rige para la venta tarreo (§7.4), así
    que la app lo lee de acá y nunca lo manda ni lo calcula. **No** se expone
    ``precio_compra``: es el costo, y con él se reconstruye el margen.
    """
    return {
        'id': balon.pk,
        'nombre': balon.nombre,
        'peso_neto_gas': balon.peso_neto_gas,
        'precio_domicilio': _entero(balon.precio_domicilio),
        # El tarreo de la web pinta una etiqueta CATALÍTICO/ALUMINIO según este
        # campo: sin él la app no puede verse igual que la PWA.
        'tipo_gas': balon.tipo_gas,
    }
