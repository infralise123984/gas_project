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
import re

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

# Único payload complejo del API: su cupo va en `limites.POLITICA` (mueve
# estado y dinero, igual que las cuatro acciones).
CAMPOS_PERMITIDOS = ('lineas', 'metodo_pago', 'direccion_entrega')

# Se derivan del modelo en vez de repetirlos: si mañana cambia el catálogo de
# formas de pago, la validación cambia sola y no queda una lista desactualizada.
METODOS_PAGO = tuple(
    valor for valor, _ in Pedido._meta.get_field('metodo_pago').choices
)

# Tope de seguridad, no regla de negocio: un camión carga decenas de balones, así
# que mil por balón solo puede venir de un cliente roto o de un abuso. Sin tope,
# `monto_total` desborda `DecimalField(max_digits=12)` y el usuario provoca un
# error de base desde afuera (`decimal.InvalidOperation` en SQLite, error de
# rango en MySQL): un 500 pedido por el cliente.
MAX_CANTIDAD_POR_BALON = 1000

# Solo dígitos ASCII y con tope de largo. `str.isdigit()` acepta '²' —y
# `int('²')` levanta ValueError— así que la comprobación tiene que ser explícita
# sobre el alfabeto: cualquier otro dígito Unicode es una entrada que el
# contrato no define. El tope de 15 dígitos evita además la conversión de un
# string desmesurado (`int()` es cuadrático y Python 3.11+ corta a los 4300
# dígitos levantando ValueError, o sea el mismo 500 por otra puerta).
_ENTERO_ASCII = re.compile(r'^[0-9]{1,15}$')


def _entero_estricto(valor):
    """Convierte a entero sin aceptar lo que `int()` acepta de más.

    `int(2.9)` da 2 y `int(True)` da 1: quedarse con eso significa vender otro
    balón (o una cantidad distinta) del que se pidió, en vez de rechazar la
    petición. Devuelve ``None`` si no es un entero limpio. Se aceptan strings de
    dígitos ASCII porque la web manda texto (viene de un formulario).
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and _ENTERO_ASCII.match(valor.strip()):
        return int(valor)
    return None


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

        balon_id = _entero_estricto(linea.get('balon_id'))
        if balon_id is None or balon_id < 1:
            return None, respuestas.error(
                'validacion', mensaje='Uno de los balones no es válido.'
            )

        # Una línea sin `cantidad` equivale a cero, igual que en la web (campo
        # ausente en el formulario). Un valor que no es entero sí es un error.
        cantidad_cruda = linea.get('cantidad', 0)
        cantidad = 0 if cantidad_cruda is None else _entero_estricto(cantidad_cruda)
        if cantidad is None:
            return None, respuestas.error(
                'validacion', mensaje='Las cantidades deben ser números enteros.'
            )

        if cantidad <= 0:
            # Igual que la web: una línea en cero no es un error, simplemente no
            # se guarda. Si ninguna línea trae cantidad, se rechaza más abajo.
            continue

        # El tope se aplica a la SUMA por balón: repetir líneas del mismo balón
        # es válido (solo puede venir de la app), pero no sirve para saltarse el
        # límite troceando la cantidad.
        cantidades[balon_id] = cantidades.get(balon_id, 0) + cantidad
        if cantidades[balon_id] > MAX_CANTIDAD_POR_BALON:
            return None, respuestas.error(
                'validacion', mensaje='Una de las cantidades es demasiado alta.'
            )

    if not cantidades:
        return None, respuestas.error(
            'validacion', mensaje='Debes agregar al menos un producto.'
        )

    return cantidades, None


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('tarreo')
def registrar_venta_tarreo(request):
    """Registra una venta directa ya entregada (§7.4)."""
    datos, error = cuerpo_json(request, permitidos=CAMPOS_PERMITIDOS)
    if error is not None:
        return error

    metodo_pago = datos.get('metodo_pago')
    if not isinstance(metodo_pago, str) or metodo_pago.strip() not in METODOS_PAGO:
        return respuestas.error(
            'validacion', mensaje='Selecciona un método de pago.'
        )
    metodo_pago = metodo_pago.strip()

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

    # Un `direccion_entrega` que no sea texto (lista, objeto, número) es una
    # entrada fuera del contrato: se rechaza en vez de dejar que `.strip()`
    # levante AttributeError y el cliente reciba un 500.
    direccion_cruda = datos.get('direccion_entrega')
    if direccion_cruda is None:
        direccion = ''
    elif isinstance(direccion_cruda, str):
        direccion = direccion_cruda.strip()
    else:
        return respuestas.error(
            'validacion', mensaje='La dirección debe ser texto.'
        )

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
