"""Lógica de negocio: anulación administrativa de pedidos.

La anulación NO borra la fila: marca el pedido como ``cancelado`` dejando traza
completa (historial de estados, historial de cambios y auditoría). Es la vía para
corregir errores humanos sobre pedidos ya cerrados —por ejemplo una entrega
correcta cargada a la cuenta equivocada—, casos donde ``editar_pedido`` bloquea
la edición por estado.
"""

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from mockups.models import (
    AuditoriaAccion,
    HistorialCambioPedido,
    HistorialEstadoPedido,
    Usuario,
)
from mockups.services.sobres import (
    hay_sobre_cerrado_para_pedido,
    resincronizar_sobres_afectados_por_pedido,
)

TZ_CHILE = ZoneInfo("America/Santiago")

ESTADO_ANULADO = "cancelado"

#: Motivo usado cuando la anulación se ejecuta en lote desde el admin de Django.
MOTIVO_ANULACION_MASIVA = "Anulación masiva desde el panel de administración de Django."


class AnulacionNoPermitida(Exception):
    """El pedido no puede anularse: falta el motivo o ya está anulado."""


@dataclass(frozen=True)
class ResultadoAnulacion:
    """Efectos colaterales de una anulación, para informar a quien la ejecuta."""

    sobres_actualizados: tuple[int, ...] = ()
    sobre_cerrado_sin_ajuste: bool = False


def _fecha_local(pedido) -> date:
    """Fecha (zona Chile) a la que pertenece el pedido, clave de su sobre."""
    return timezone.localtime(pedido.fecha, TZ_CHILE).date()


def _registrar_auditoria(
    *,
    pedido,
    usuario: Usuario,
    motivo: str,
    estado_anterior: str,
    ip_address: str | None,
    user_agent: str,
) -> AuditoriaAccion:
    """Deja el rastro de auditoría sin acoplar el dominio a ``HttpRequest``."""
    return AuditoriaAccion.objects.create(
        tipo="PEDIDO_CANCEL",
        usuario=usuario,
        username=usuario.username,
        ip_address=ip_address or None,
        user_agent=(user_agent or "")[:500],
        descripcion=f"Pedido #{pedido.id} anulado por {usuario.username}. Motivo: {motivo}",
        objeto_tipo=pedido.__class__.__name__,
        objeto_id=pedido.pk,
        objeto_repr=str(pedido)[:255],
        datos_anteriores={"estado": estado_anterior},
        datos_nuevos={"estado": ESTADO_ANULADO, "motivo": motivo},
    )


def anular_pedido(
    pedido,
    *,
    usuario: Usuario,
    motivo: str,
    ip_address: str | None = None,
    user_agent: str = "",
) -> ResultadoAnulacion:
    """Marca un pedido como anulado registrando la traza completa.

    Args:
        pedido: Pedido a anular (cualquier estado salvo ``cancelado``).
        usuario: Quien ejecuta la anulación; queda en historial y auditoría.
        motivo: Texto obligatorio que explica la corrección.
        ip_address: IP de origen, opcional, solo para auditoría.
        user_agent: User agent de origen, opcional, solo para auditoría.

    Returns:
        ResultadoAnulacion con los sobres refrescados y la señal de si un sobre
        cerrado impidió reflejar el cambio.

    Raises:
        AnulacionNoPermitida: si falta el motivo o el pedido ya está anulado.
    """
    motivo_normalizado = (motivo or "").strip()
    if not motivo_normalizado:
        raise AnulacionNoPermitida("Debes indicar el motivo de la anulación.")
    if pedido.estado == ESTADO_ANULADO:
        raise AnulacionNoPermitida(f"El pedido #{pedido.id} ya está anulado.")

    estado_anterior = pedido.estado
    fecha_local = _fecha_local(pedido)

    with transaction.atomic():
        pedido.estado = ESTADO_ANULADO
        pedido.save(update_fields=["estado"])

        HistorialEstadoPedido.objects.create(
            pedido=pedido,
            estado_anterior=estado_anterior,
            estado_nuevo=ESTADO_ANULADO,
            cambiado_por=usuario,
            fecha_cambio=timezone.now(),
            comentario=f"Anulación administrativa. Motivo: {motivo_normalizado}",
        )
        HistorialCambioPedido.objects.create(
            pedido=pedido,
            usuario=usuario,
            descripcion=(
                f"Pedido anulado ({estado_anterior} → {ESTADO_ANULADO}) por "
                f"{usuario.username}. Motivo: {motivo_normalizado}"
            ),
        )
        _registrar_auditoria(
            pedido=pedido,
            usuario=usuario,
            motivo=motivo_normalizado,
            estado_anterior=estado_anterior,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    # Fuera de la transacción: refrescar otros sobres es I/O de base de datos y no
    # debe alargar el bloqueo de la fila del pedido.
    sobres_actualizados = resincronizar_sobres_afectados_por_pedido(pedido, fecha_local)

    return ResultadoAnulacion(
        sobres_actualizados=tuple(sobre.id for sobre in sobres_actualizados),
        sobre_cerrado_sin_ajuste=hay_sobre_cerrado_para_pedido(pedido, fecha_local),
    )
