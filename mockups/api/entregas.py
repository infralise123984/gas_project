"""Panel de entregas del camionero (docs/API_MOVIL.md §7.1 y §7.3).

F1 es la lectura; F2 son las cuatro acciones, que responden con el pedido ya
actualizado y replican las vistas de ``mockups/views/entregas.py``: mismos
modelos de historial y misma auditoría que la web.
"""

import logging

from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import never_cache

from mockups.api import respuestas
from mockups.api.acceso import acceso_api, solo_post
from mockups.api.limites import limitar
from mockups.api.serializadores import serializar_pedido
from mockups.models import (
    AuditoriaAccion,
    HistorialCambioPedido,
    HistorialEstadoPedido,
    Pedido,
)
from mockups.services.camionero import queryset_actividad_camionero_dia
from mockups.utils.fechas import rango_dia_chile, today_chile
from mockups.utils.permisos import get_client_ip

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')

ROLES_CAMIONERO = ['camionero']

# Se aplica después de acceso_api: el límite siempre cuenta a un usuario real.
LIMITE_ENTREGAS = {'peticiones': 60, 'ventana_segundos': 60}

# Las acciones mueven estado y dinero: límite más estrecho que el del listado.
LIMITE_ACCIONES = {'peticiones': 30, 'ventana_segundos': 60}


@never_cache
@acceso_api(ROLES_CAMIONERO)
@limitar('entregas', **LIMITE_ENTREGAS)
def listar_entregas(request):
    """Réplica de las tres consultas de ``camionero_entregas``, sin reinterpretarlas.

    Nota: no se agrega ``select_related('registrador')`` aunque la web lo use,
    porque el objeto Pedido del §7.2 no expone ``registrador`` y el JOIN sería
    trabajo a cambio de nada. El prefetch de detalles sí es obligatorio: lo
    necesitan ``kilos_de_pedido`` y las líneas.
    """
    usuario = request.user
    hoy = today_chile()
    inicio_dia, fin_dia = rango_dia_chile(hoy)

    pendientes = (
        Pedido.objects.filter(
            estado='pendiente',
            origen='telefono',
            entregador__isnull=True,
            fecha__gte=inicio_dia,
            fecha__lt=fin_dia,
        )
        .prefetch_related('detalles__balon')
        .order_by('-fecha')
    )

    # Sin límite de fecha: un pedido en ruta puede ser de un día anterior.
    en_ruta = (
        Pedido.objects.filter(estado='en_ruta', entregador=usuario)
        .prefetch_related('detalles__balon')
        .order_by('fecha')
    )

    # Actividad completa del día del camionero: es lo que lista
    # `mis_entregas_camionero`, que además de las entregas muestra los
    # cancelados (con su franja roja) y los que siguen en ruta.
    actividad_hoy = queryset_actividad_camionero_dia(usuario, hoy)
    entregados_hoy = actividad_hoy.filter(estado='entregado')

    return respuestas.ok(
        {
            'hoy': hoy.isoformat(),
            # La lista vacía filtra lo que el camionero no debe tocar: sin este
            # recorte, un camionero leería los pedidos en ruta de los demás.
            'pendientes': [serializar_pedido(pedido) for pedido in pendientes],
            'en_ruta': [serializar_pedido(pedido) for pedido in en_ruta],
            'entregados_hoy': [
                serializar_pedido(pedido) for pedido in entregados_hoy
            ],
            # Todo lo del día que le pertenece al camionero, sin los pendientes
            # sin asignar. `entregados_hoy` es su subconjunto entregado.
            'actividad_hoy': [
                serializar_pedido(pedido) for pedido in actividad_hoy
            ],
        }
    )


def _historial_estado(pedido, estado_anterior, estado_nuevo, usuario, comentario=None):
    """Deja constancia del cambio de estado, igual que la web."""
    HistorialEstadoPedido.objects.create(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        cambiado_por=usuario,
        fecha_cambio=timezone.now(),
        comentario=comentario,
    )


def _auditar(request, *, tipo, descripcion, pedido, anteriores, nuevos):
    AuditoriaAccion.registrar(
        request=request,
        tipo=tipo,
        descripcion=descripcion,
        objeto=pedido,
        datos_anteriores=anteriores,
        datos_nuevos=nuevos,
    )


def _respuesta_pedido(pedido):
    """Sobre del §7.3: el pedido ya actualizado."""
    return respuestas.ok({'pedido': serializar_pedido(pedido)})


def _no_disponible(request, pedido_id, accion):
    """409 del §7.3.

    No es 500 ni 404: en la práctica casi siempre es una carrera legítima (otro
    camionero lo tomó antes) o un pedido que ya cambió de estado.
    """
    security_logger.warning(
        f'API_PEDIDO_{accion}_FAIL | #{pedido_id} no disponible | '
        f'User: {request.user.username} | IP: {get_client_ip(request)}'
    )
    return respuestas.error('no_disponible')


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('acciones_entregas', **LIMITE_ACCIONES)
def tomar_pedido(request, pedido_id):
    """`pendiente` → `en_ruta`, asignado a quien lo toma (§7.3)."""
    try:
        with transaction.atomic():
            # select_for_update: si dos camioneros lo toman a la vez, el segundo
            # espera y después ya no encuentra la fila en `pendiente`.
            pedido = Pedido.objects.select_for_update().get(
                id=pedido_id,
                estado='pendiente',
                entregador__isnull=True,
                origen='telefono',
            )
            estado_anterior = pedido.estado
            pedido.estado = 'en_ruta'
            pedido.entregador = request.user
            pedido.save(update_fields=['estado', 'entregador'])

            _historial_estado(pedido, estado_anterior, 'en_ruta', request.user)
            _auditar(
                request,
                tipo='PEDIDO_UPDATE',
                descripcion=(
                    f'Camionero {request.user.username} tomó Pedido #{pedido.id} '
                    '(pendiente → en_ruta)'
                ),
                pedido=pedido,
                anteriores={'estado': estado_anterior, 'entregador': None},
                nuevos={'estado': 'en_ruta', 'entregador': request.user.username},
            )
    except Pedido.DoesNotExist:
        return _no_disponible(request, pedido_id, 'TOMAR')

    audit_logger.info(
        f'PEDIDO_TOMAR_API | #{pedido.id} | Camionero: {request.user.username} | '
        f'IP: {get_client_ip(request)}'
    )
    return _respuesta_pedido(pedido)


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('acciones_entregas', **LIMITE_ACCIONES)
def entregar_pedido(request, pedido_id):
    """`en_ruta` propio → `entregado` (§7.3)."""
    try:
        with transaction.atomic():
            pedido = Pedido.objects.select_for_update().get(
                id=pedido_id,
                estado='en_ruta',
                entregador=request.user,
                origen='telefono',
            )
            estado_anterior = pedido.estado
            pedido.estado = 'entregado'
            pedido.save(update_fields=['estado'])

            _historial_estado(pedido, estado_anterior, 'entregado', request.user)
            _auditar(
                request,
                tipo='PEDIDO_UPDATE',
                descripcion=(
                    f'Camionero {request.user.username} marcó Pedido #{pedido.id} '
                    'como ENTREGADO'
                ),
                pedido=pedido,
                anteriores={
                    'estado': estado_anterior,
                    'entregador': request.user.username,
                },
                nuevos={
                    'estado': 'entregado',
                    'entregador': request.user.username,
                },
            )
    except Pedido.DoesNotExist:
        return _no_disponible(request, pedido_id, 'ENTREGAR')

    audit_logger.info(
        f'PEDIDO_ENTREGAR_API | #{pedido.id} | Camionero: {request.user.username} | '
        f'IP: {get_client_ip(request)}'
    )
    return _respuesta_pedido(pedido)


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('acciones_entregas', **LIMITE_ACCIONES)
def cancelar_pedido(request, pedido_id):
    """`en_ruta` propio → `cancelado`, conservando el entregador (§7.3).

    Nota de revisión: la vista web ``camionero_cancelar_entrega`` no usa
    transacción ni bloqueo. Acá sí, porque el bloqueo es lo que impide que un
    cancelar y una entrega simultáneos se pisen. Es aditivo: la web no cambia.
    """
    try:
        with transaction.atomic():
            pedido = Pedido.objects.select_for_update().get(
                id=pedido_id,
                estado='en_ruta',
                entregador=request.user,
            )
            estado_anterior = pedido.estado
            pedido.estado = 'cancelado'
            # No se limpia `entregador`: el camionero sigue asignado para que el
            # pedido siga apareciendo en su historial (decisión de negocio).
            pedido.save(update_fields=['estado'])

            _historial_estado(pedido, estado_anterior, 'cancelado', request.user)
            HistorialCambioPedido.objects.create(
                pedido=pedido,
                usuario=request.user,
                descripcion=(
                    'Entrega cancelada por el camionero '
                    f'{request.user.get_full_name() or request.user.username} '
                    '(estaba en ruta).'
                ),
            )
            _auditar(
                request,
                tipo='PEDIDO_CANCEL',
                descripcion=(
                    f'Pedido #{pedido.id} cancelado por camionero '
                    f'{request.user.username} (estaba en ruta)'
                ),
                pedido=pedido,
                anteriores={
                    'estado': estado_anterior,
                    'entregador': request.user.username,
                },
                nuevos={'estado': 'cancelado'},
            )
    except Pedido.DoesNotExist:
        return _no_disponible(request, pedido_id, 'CANCELAR')

    audit_logger.info(
        f'PEDIDO_CANCEL_API | #{pedido.id} | Camionero: {request.user.username} | '
        f'IP: {get_client_ip(request)}'
    )
    return _respuesta_pedido(pedido)


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('acciones_entregas', **LIMITE_ACCIONES)
def devolver_pedido(request, pedido_id):
    """`en_ruta` propio → `pendiente` otra vez en el pool, y re-notifica (§7.3)."""
    try:
        with transaction.atomic():
            pedido = Pedido.objects.select_for_update().get(
                id=pedido_id,
                estado='en_ruta',
                entregador=request.user,
                origen='telefono',
            )
            estado_anterior = pedido.estado
            pedido.estado = 'pendiente'
            pedido.entregador = None
            pedido.devuelto_el = timezone.now()
            pedido.devuelto_por = request.user
            pedido.save(
                update_fields=['estado', 'entregador', 'devuelto_el', 'devuelto_por']
            )

            _historial_estado(
                pedido,
                estado_anterior,
                'pendiente',
                request.user,
                comentario='Pedido devuelto al pool por el camionero (toma por accidente).',
            )
            HistorialCambioPedido.objects.create(
                pedido=pedido,
                usuario=request.user,
                descripcion=(
                    'Pedido devuelto al pool por '
                    f'{request.user.get_full_name() or request.user.username} '
                    '(lo había tomado por accidente). Vuelve a estar disponible '
                    'para otros camioneros.'
                ),
            )
            _auditar(
                request,
                tipo='PEDIDO_DEVOLVER',
                descripcion=(
                    f'Pedido #{pedido.id} devuelto al pool por camionero '
                    f'{request.user.username}'
                ),
                pedido=pedido,
                anteriores={
                    'estado': estado_anterior,
                    'entregador': request.user.username,
                },
                nuevos={'estado': 'pendiente', 'entregador': None},
            )
    except Pedido.DoesNotExist:
        return _no_disponible(request, pedido_id, 'DEVOLVER')

    audit_logger.info(
        f'PEDIDO_DEVOLVER_API | #{pedido.id} | Camionero: {request.user.username} | '
        f'IP: {get_client_ip(request)}'
    )

    # Fuera de la transacción: el envío es E/S de red y alargaría el bloqueo de
    # la fila. Si falla, el pedido ya volvió al pool igual; se registra y no se
    # propaga, porque la notificación no es parte del resultado de la acción.
    try:
        from mockups.push_notifications import notificar_nuevo_pedido

        notificar_nuevo_pedido(
            pedido,
            excluir_usuario_id=request.user.id,
            title='🚚 ¡Pedido disponible!',
        )
    except Exception as error:
        security_logger.warning(
            f'API_PEDIDO_DEVOLVER_PUSH_FAIL | #{pedido.id} | {error}'
        )

    return _respuesta_pedido(pedido)
