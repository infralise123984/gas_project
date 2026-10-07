"""Venta en la calle (tarreo) del camionero (docs/API_MOVIL.md §7.4).

Réplica de ``tarreo_pedido`` (``mockups/views/entregas.py``) con dos
diferencias deliberadas, las dos documentadas en el contrato:

* el ``Idempotency-Key`` reemplaza al ``form_token`` de sesión de la web;
* al final se llama ``Pedido.calcular_totales()``, así que ``ganancia_total`` y
  ``descuento_total`` quedan consistentes. La web asigna ``monto_total`` a mano
  y deja esos dos campos en cero.

El precio **no** llega en el payload: sale del catálogo activo. Si el cliente
pudiera mandarlo, podría vender a cualquier precio. Tampoco se aceptan
descuentos: el camionero no está en ``ROLES_CON_DESCUENTO`` y la web ignora el
campo, así que acá se omite al construir la línea.
"""

import logging

from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import never_cache

from mockups.api import respuestas
from mockups.api.acceso import ROLES_CAMIONERO, acceso_api, cuerpo_json, solo_post
from mockups.api.limites import limitar
from mockups.api.serializadores import serializar_pedido
from mockups.models import DetallePedido, HistorialEstadoPedido, Pedido
from mockups.services.catalogos import get_balones_activos_ordenados
from mockups.utils.permisos import get_client_ip

audit_logger = logging.getLogger('audit')

# Clave y ventana de la dirección por defecto: mismo texto que la web, para que
# el historial del teléfono y el de la web digan lo mismo.
DIRECCION_POR_DEFECTO = 'Tarreo / venta directa en camión'

# Único payload complejo del API, así que el límite va igual de estrecho que el
# de las acciones: mueve estado y dinero.
LIMITE_TARREO = {'peticiones': 30, 'ventana_segundos': 60}

CAMPOS_PERMITIDOS = ('lineas', 'metodo_pago', 'direccion_entrega')

# Se derivan del modelo en vez de repetirlos: si mañana cambia el catálogo de
# formas de pago, la validación cambia sola y no queda una lista desactualizada.
METODOS_PAGO = tuple(
    valor for valor, _ in Pedido._meta.get_field('metodo_pago').choices
)


def _parsear_lineas(lineas):
    """Valida la forma de ``lineas`` y devuelve ``(cantidades, None)``.

    Devuelve ``cantidad`` por ``balon_id``, ya sumada: la web tiene un campo por
    balón, así que dos líneas del mismo balón solo pueden venir de la app, y
    sumarlas es lo que el usuario quiso decir. No se crea nada hasta que el
    payload esté entero validado.
    """
    if not isinstance(lineas, list) or not lineas:
        return None, respuestas.error(
            'validacion', mensaje='Debes agregar al menos un producto.'
        )

    cantidades = {}
    for linea in lineas:
        if not isinstance(linea, dict):
            return None, respuestas.error('validacion')

        try:
            balon_id = int(linea.get('balon_id'))
            cantidad = int(linea.get('cantidad', 0))
        except (TypeError, ValueError):
            return None, respuestas.error('validacion')

        if cantidad <= 0:
            # Igual que la web: una línea en cero no es un error, simplemente no
            # se guarda. Si ninguna línea trae cantidad, se rechaza más abajo.
            continue

        cantidades[balon_id] = cantidades.get(balon_id, 0) + cantidad

    if not cantidades:
        return None, respuestas.error(
            'validacion', mensaje='Debes agregar al menos un producto.'
        )

    return cantidades, None


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('tarreo', **LIMITE_TARREO)
def registrar_venta_tarreo(request):
    """Registra una venta directa ya entregada (§7.4)."""
    datos, error = cuerpo_json(request, permitidos=CAMPOS_PERMITIDOS)
    if error is not None:
        return error

    metodo_pago = (datos.get('metodo_pago') or '').strip()
    if metodo_pago not in METODOS_PAGO:
        return respuestas.error(
            'validacion', mensaje='Selecciona un método de pago.'
        )

    cantidades, error = _parsear_lineas(datos.get('lineas'))
    if error is not None:
        return error

    # Solo balones activos: la web itera el catálogo vigente, así que un balón
    # retirado no es vendible por el API tampoco.
    catalogo = {balon.pk: balon for balon in get_balones_activos_ordenados()}
    desconocidos = sorted(set(cantidades) - set(catalogo))
    if desconocidos:
        return respuestas.error(
            'validacion', mensaje='Uno de los balones ya no está disponible.'
        )

    direccion = (datos.get('direccion_entrega') or '').strip()

    # Todo en una transacción: un fallo a mitad no puede dejar media venta.
    with transaction.atomic():
        pedido = Pedido.objects.create(
            origen='tarreo',
            metodo_pago=metodo_pago,
            direccion_entrega=direccion or DIRECCION_POR_DEFECTO,
            registrador=request.user,
            entregador=request.user,
            estado='entregado',  # venta directa: entregada en el acto
            fecha=timezone.now(),
        )

        for balon_id, cantidad in cantidades.items():
            balon = catalogo[balon_id]
            DetallePedido.objects.create(
                pedido=pedido,
                balon=balon,
                cantidad=cantidad,
                # Snapshot de precios, igual que la web.
                precio_venta_unitario=balon.precio_domicilio,
                precio_compra_unitario=balon.precio_compra,
            )

        # El total lo calcula el servidor desde las líneas: la app nunca lo manda.
        pedido.calcular_totales()

        HistorialEstadoPedido.objects.create(
            pedido=pedido,
            estado_nuevo='entregado',
            cambiado_por=request.user,
            comentario='Venta tarreo registrada y entregada directamente.',
        )

    # La web no registra AuditoriaAccion en esta vista; se mantiene la paridad y
    # queda el rastro en el log de auditoría. Si se decide auditarla también en
    # base, va en los dos lados a la vez.
    audit_logger.info(
        f'PEDIDO_TARREO_API | #{pedido.id} | Camionero: {request.user.username} | '
        f'Monto: {pedido.monto_total} | IP: {get_client_ip(request)}'
    )

    return respuestas.ok({'pedido': serializar_pedido(pedido)})
