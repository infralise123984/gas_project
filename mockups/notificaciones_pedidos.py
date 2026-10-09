"""Despacho del aviso "hay un pedido disponible" a los dos canales.

Existe por dos razones concretas:

* **DRY:** hoy hay tres puntos que avisan de un pedido (crear en la web, devolver
  en la web, devolver por el API). Sin este módulo, agregar el canal móvil
  significa repetir el mismo fan-out en los tres.
* **Aislamiento de fallos:** un canal caído no puede impedir que el otro avise
  ni romper la respuesta al usuario. El aviso ocurre *después* de una acción de
  negocio ya confirmada, así que ningún fallo de push puede propagarse: todo se
  captura y queda en el log.

Lo que **no** hace: decidir qué es un pedido nuevo. Eso vive en
``notificar_nuevo_pedido`` (Web Push) y ``notificar_nuevo_pedido_movil`` (FCM),
que aplican los mismos filtros de estado y origen.
"""

import logging

audit_logger = logging.getLogger('audit')


def notificar_pedido_disponible(
    pedido,
    *,
    excluir_usuario_id=None,
    title=None,
    motivo='',
    logger_fallo=None,
):
    """Avisa por Web Push y por FCM. Devuelve ``{'web': n, 'movil': n}``.

    ``motivo`` es solo para el log (de dónde vino la llamada) y ``logger_fallo``
    permite que el API siga registrando sus fallos en el logger de seguridad en
    vez del de auditoría. Nunca levanta excepción.
    """
    from mockups.push_notifications import notificar_nuevo_pedido
    from mockups.push_notifications_movil import notificar_nuevo_pedido_movil

    registro_fallo = logger_fallo or audit_logger
    etiqueta_motivo = motivo or 'sin_motivo'
    resultado = {'web': 0, 'movil': 0}

    try:
        resultado['web'] = notificar_nuevo_pedido(
            pedido,
            excluir_usuario_id=excluir_usuario_id,
            title=title,
        )
        if resultado['web']:
            audit_logger.info(
                f'PUSH_SENT | Pedido #{pedido.id} -> {resultado["web"]} camioneros notificados'
            )
    except Exception as error:
        registro_fallo.warning(
            f'PUSH_FAIL | Pedido #{pedido.id} | Motivo: {etiqueta_motivo} | Error: {error}'
        )

    try:
        resultado['movil'] = notificar_nuevo_pedido_movil(
            pedido,
            excluir_usuario_id=excluir_usuario_id,
            title=title,
        )
        if resultado['movil']:
            audit_logger.info(
                f'PUSH_MOVIL_SENT | Pedido #{pedido.id} -> '
                f'{resultado["movil"]} dispositivos notificados'
            )
    except Exception as error:
        # Un fallo acá no puede tapar el resultado del canal web, que ya se
        # registró arriba.
        registro_fallo.warning(
            f'PUSH_MOVIL_FAIL | Pedido #{pedido.id} | Motivo: {etiqueta_motivo} | '
            f'Error: {error}'
        )

    return resultado
