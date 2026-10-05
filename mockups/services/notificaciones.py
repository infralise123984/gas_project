"""Avisos in-app (efimeros) para el telefonista sobre los pedidos que registro.

No persiste nada propio: los avisos se DERIVAN del `HistorialEstadoPedido` (y de los
campos de devolucion del `Pedido`). Asi el telefonista ve, en vivo, lo que hace el
camionero con sus pedidos sin crear una tabla de notificaciones.

Regla de alcance (aislamiento de datos): un telefonista solo ve avisos de los pedidos
que EL registro, y solo de acciones hechas por un camionero.
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from mockups.models import HistorialEstadoPedido

# Transiciones de estado visibles para el telefonista y su etiqueta corta.
#   en_ruta   -> el camionero tomo el pedido
#   entregado -> el camionero lo entrego
#   cancelado -> el camionero lo cancelo
#   pendiente -> el camionero lo devolvio al pool
EVENTOS_POR_ESTADO = {
    'en_ruta': ('tomado', 'Camionero tomo el pedido', 'tomo el pedido'),
    'entregado': ('entregado', 'Pedido entregado', 'entrego el pedido'),
    'cancelado': ('cancelado', 'Pedido cancelado', 'cancelo el pedido'),
    'pendiente': ('devuelto', 'Pedido devuelto al pool', 'devolvio el pedido al pool'),
}

# Ventana maxima por defecto si no se entrega un `desde` (evita escanear todo el historial).
VENTANA_MAXIMA = timedelta(hours=12)

# Tope de avisos devueltos en una sola consulta.
LIMITE_AVISOS = 20


def _nombre_camionero(usuario):
    """Nombre legible del camionero, con fallback seguro."""
    if not usuario:
        return 'Un camionero'
    return usuario.get_full_name() or usuario.username


def eventos_recientes_telefonista(usuario, desde=None, limite=LIMITE_AVISOS):
    """Avisos (mas reciente primero) de los pedidos registrados por `usuario`.

    Args:
        usuario: debe ser rol 'telefonista'; cualquier otro rol devuelve [].
        desde: datetime aware. Se devuelven los eventos con fecha estrictamente
            posterior. Si es None, se usa una ventana de las ultimas 12 h.
        limite: maximo de avisos.

    Returns:
        list[dict]: cada aviso con tipo, pedido_id, camionero, titulo, mensaje,
        fecha (ISO) y url.
    """
    if getattr(usuario, 'rol', None) != 'telefonista':
        return []

    if desde is None:
        desde = timezone.now() - VENTANA_MAXIMA

    cambios = (
        HistorialEstadoPedido.objects
        .filter(
            pedido__registrador=usuario,
            fecha_cambio__gt=desde,
            estado_nuevo__in=EVENTOS_POR_ESTADO.keys(),
            cambiado_por__rol='camionero',  # solo acciones del camionero, no del admin
        )
        .select_related('pedido', 'cambiado_por')
        .order_by('-fecha_cambio')[:limite]
    )

    avisos = []
    for cambio in cambios:
        tipo, titulo, accion = EVENTOS_POR_ESTADO[cambio.estado_nuevo]
        camionero = _nombre_camionero(cambio.cambiado_por)
        avisos.append({
            'tipo': tipo,
            'pedido_id': cambio.pedido_id,
            'camionero': camionero,
            'titulo': titulo,
            'mensaje': f'{camionero} {accion}',
            'fecha': cambio.fecha_cambio.isoformat(),
            'url': reverse('pedidos_detalle', args=[cambio.pedido_id]),
        })
    return avisos
