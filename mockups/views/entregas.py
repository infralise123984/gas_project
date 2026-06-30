"""Vistas HTTP — entregas."""

import logging
import uuid
from calendar import monthrange
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from mockups.models import (
    AuditoriaAccion,
    DetallePedido,
    HistorialCambioPedido,
    HistorialEstadoPedido,
    Pedido,
)
from mockups.services.camionero import (
    _kilos_de_pedido,
    _stats_dia_camionero,
    queryset_actividad_camionero_dia,
    stats_ventas_camionero,
)
from mockups.services.catalogos import get_balones_activos_ordenados
from mockups.utils.fechas import (
    MESES_ES_CAMIONERO,
    navegacion_mes,
    now_chile,
    parse_mes_param,
    rango_dia_chile,
    today_chile,
)
from mockups.utils.permisos import require_roles, require_roles_api

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')




# --- CAMIONERO ---

# Camionero: ver sus entregas completadas hoy
@login_required
def mis_entregas_camionero(request):
    """Ver entregas finalizadas por este camionero hoy."""
    resp = require_roles(request, ["camionero"], "index", "Solo camioneros pueden ver sus entregas.")
    if resp:
        return resp

    hoy = today_chile()
    pedidos_hoy = queryset_actividad_camionero_dia(request.user, hoy)
    pedidos_entregados = pedidos_hoy.filter(estado='entregado')
    stats = stats_ventas_camionero(pedidos_entregados)

    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_entregas_hoy": stats['total_entregas'],
        "total_monto_hoy": stats['total_monto'],
        "total_kilos_hoy": stats['total_kilos'],
        "fecha_hoy": hoy,
    }
    return render(request, "entregas/mis_entregas_camionero.html", context)



@login_required
def mis_entregas_camionero_api(request):
    """Endpoint AJAX: devuelve HTML actualizado de entregas completadas hoy por el camionero."""
    resp = require_roles_api(request, ['camionero'])
    if resp:
        return resp

    hoy = today_chile()
    pedidos_hoy = queryset_actividad_camionero_dia(request.user, hoy)
    pedidos_entregados = pedidos_hoy.filter(estado='entregado')
    stats = stats_ventas_camionero(pedidos_entregados)

    context = {
        'pedidos_hoy': pedidos_hoy,
        'total_entregas_hoy': stats['total_entregas'],
        'total_monto_hoy': stats['total_monto'],
        'total_kilos_hoy': stats['total_kilos'],
    }
    html = render(request, 'partials/_mis_entregas_camionero_cards.html', context).content.decode('utf-8')
    return JsonResponse({'html': html, 'count': stats['total_entregas']})



@login_required
@never_cache
def camionero_historial(request):
    """Resumen mensual de ventas del camionero (kilos por día)."""
    resp = require_roles(request, ['camionero'], 'index', 'Solo camioneros pueden ver su historial.')
    if resp:
        return resp

    anio, mes = parse_mes_param(request)
    _, ultimo_dia = monthrange(anio, mes)
    hoy = today_chile()

    filas_dias = []
    total_kilos_mes = 0
    total_monto_mes = 0
    total_entregas_mes = 0
    dias_con_venta = 0

    for dia_num in range(ultimo_dia, 0, -1):
        fecha_dia = date(anio, mes, dia_num)
        datos = _stats_dia_camionero(request.user, fecha_dia)
        entregas = datos['entregas']
        tiene_venta = entregas > 0

        if tiene_venta:
            dias_con_venta += 1
            total_kilos_mes += datos['kilos']
            total_monto_mes += datos['monto']
            total_entregas_mes += entregas

        filas_dias.append({
            'fecha': fecha_dia,
            'es_hoy': fecha_dia == hoy,
            'tiene_venta': tiene_venta,
            'kilos': datos['kilos'],
            'monto': datos['monto'],
            'entregas': entregas,
            'kilos_domicilio': datos['kilos_domicilio'],
            'kilos_tarreo': datos['kilos_tarreo'],
        })

    mes_anterior, mes_siguiente = navegacion_mes(anio, mes)
    mes_siguiente_habilitado = (
        mes_siguiente[0] < hoy.year
        or (mes_siguiente[0] == hoy.year and mes_siguiente[1] <= hoy.month)
    )

    context = {
        'anio': anio,
        'mes': mes,
        'mes_nombre': MESES_ES_CAMIONERO[mes],
        'mes_param': f'{anio:04d}-{mes:02d}',
        'mes_anterior_param': f'{mes_anterior[0]:04d}-{mes_anterior[1]:02d}',
        'mes_siguiente_param': f'{mes_siguiente[0]:04d}-{mes_siguiente[1]:02d}',
        'mes_siguiente_habilitado': mes_siguiente_habilitado,
        'filas_dias': filas_dias,
        'total_kilos_mes': total_kilos_mes,
        'total_monto_mes': total_monto_mes,
        'total_entregas_mes': total_entregas_mes,
        'dias_con_venta': dias_con_venta,
        'hoy': hoy,
    }
    return render(request, 'entregas/historial_camionero.html', context)



@login_required
@never_cache
def camionero_historial_dia(request, fecha):
    """Detalle resumido de las ventas de un día específico."""
    resp = require_roles(request, ['camionero'], 'index', 'Solo camioneros pueden ver su historial.')
    if resp:
        return resp

    try:
        fecha_dia = datetime.strptime(fecha, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Fecha inválida.')
        return redirect('entregas_historial')

    datos = _stats_dia_camionero(request.user, fecha_dia)
    pedidos_resumen = [
        {'pedido': pedido, 'kilos': _kilos_de_pedido(pedido)}
        for pedido in datos['pedidos_entregados']
    ]

    context = {
        'fecha_dia': fecha_dia,
        'fecha_param': fecha,
        'mes_param': f'{fecha_dia.year:04d}-{fecha_dia.month:02d}',
        'mes_nombre': MESES_ES_CAMIONERO[fecha_dia.month],
        'pedidos_resumen': pedidos_resumen,
        'total_entregas': datos['entregas'],
        'total_monto': datos['monto'],
        'total_kilos': datos['kilos'],
        'es_hoy': fecha_dia == today_chile(),
    }
    return render(request, 'entregas/historial_camionero_dia.html', context)



# Camionero: panel de entregas
@login_required
def camionero_entregas(request):
    """Panel de control: entregas en rúta, pendientes y completadas hoy."""
    resp = require_roles(request, ["camionero", "admin"], "index", "Acceso restringido a camioneros y administradores.")
    if resp:
        return resp

    user = request.user
    hoy = today_chile()
    inicio_dia, fin_dia = rango_dia_chile(hoy)
    ahora = now_chile()

    # 1. Pedidos en ruta (asignados al camionero) → sin límite de fecha (pueden ser de días anteriores)
    pedidos_en_ruta = Pedido.objects.filter(
        estado="en_ruta",
        entregador=user
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("fecha")

    # 2. Pedidos entregados HOY (mismo criterio que mis_entregas_camionero)
    pedidos_entregados_hoy = (
        queryset_actividad_camionero_dia(user, hoy)
        .filter(estado='entregado')
        .select_related('registrador')
        .prefetch_related('detalles__balon')
        .order_by("-fecha")
    )

    # 3. Pedidos pendientes DISPONIBLES HOY (sin asignar, origen telefónico)
    pendientes = Pedido.objects.filter(
        estado="pendiente",
        origen="telefono",
        entregador__isnull=True,
        fecha__gte=inicio_dia,
        fecha__lt=fin_dia,
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("-fecha")

    count_en_ruta = pedidos_en_ruta.count()
    count_pendientes = pendientes.count()
    count_entregados_hoy = pedidos_entregados_hoy.count()

    context = {
        "pedidos_en_ruta": pedidos_en_ruta,
        "pendientes": pendientes,
        "pedidos_entregados_hoy": pedidos_entregados_hoy,

        "count_en_ruta": count_en_ruta,
        "count_pendientes": count_pendientes,
        "count_entregados_hoy": count_entregados_hoy,

        "hay_en_ruta": count_en_ruta > 0,
        "hay_pendientes": count_pendientes > 0,
        "hay_entregados_hoy": count_entregados_hoy > 0,

        # Para mostrar la fecha en la interfaz
        "hoy": hoy,
        "ahora": ahora,
    }

    return render(request, "entregas/camionero_entregas.html", context)



# Camionero: API para actualización dinámica
@login_required
def camionero_entregas_api(request):
    """Endpoint AJAX: devuelve HTML actualizado de entregas en ruta."""
    resp = require_roles_api(request, ["camionero", "admin"])
    if resp:
        return resp

    user = request.user
    hoy = today_chile()
    inicio_dia, fin_dia = rango_dia_chile(hoy)

    pedidos_en_ruta = Pedido.objects.filter(
        estado="en_ruta",
        entregador=user
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("fecha")

    pendientes = Pedido.objects.filter(
        estado="pendiente",
        origen="telefono",
        entregador__isnull=True,
        fecha__gte=inicio_dia,
        fecha__lt=fin_dia,
        bodega=user.bodega,
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("-fecha")

    pedidos_en_ruta_list = list(pedidos_en_ruta)
    pendientes_list = list(pendientes)
    count_en_ruta = len(pedidos_en_ruta_list)
    count_pendientes = len(pendientes_list)

    context = {
        "pedidos_en_ruta": pedidos_en_ruta_list,
        "pendientes": pendientes_list,
        "user": user,
        "count_en_ruta": count_en_ruta,
        "count_pendientes": count_pendientes,
    }

    html = render(request, "partials/_entregas_cards.html", context).content.decode('utf-8')
    
    return JsonResponse({
        'html': html,
        'count_en_ruta': count_en_ruta,
        'count_pendientes': count_pendientes,
    })



# Camionero: tomar pedido asignado
@login_required
@require_POST
def camionero_tomar_pedido(request, pedido_id):
    """Camionero marca pedido como 'en_ruta' (asignado a él)."""
    resp = require_roles(request, ["camionero"], "index", "Solo camioneros pueden tomar pedidos.")
    if resp:
        return resp

    try:
        with transaction.atomic():
            # select_for_update: bloquea la fila hasta que termine la transacción.
            # Si dos camioneros intentan tomar el mismo pedido al mismo tiempo,
            # el segundo espera y luego recibe DoesNotExist (ya fue modificado por el primero).
            pedido = Pedido.objects.select_for_update().get(
                id=pedido_id,
                estado="pendiente",
                entregador__isnull=True,
                origen="telefono"
            )
            estado_anterior = pedido.estado
            pedido.estado = "en_ruta"
            pedido.entregador = request.user
            pedido.save()

            HistorialEstadoPedido.objects.create(
                pedido=pedido,
                estado_anterior=estado_anterior,
                estado_nuevo="en_ruta",
                cambiado_por=request.user,
                fecha_cambio=timezone.now(),
            )

            # Auditoría detallada: registro de quién tomó el pedido y desde qué IP
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
            AuditoriaAccion.registrar(
                request=request,
                tipo='PEDIDO_UPDATE',
                descripcion=f'Camionero {request.user.username} tomó Pedido #{pedido.id} (pendiente → en_ruta)',
                objeto=pedido,
                datos_anteriores={'estado': estado_anterior, 'entregador': None},
                datos_nuevos={'estado': 'en_ruta', 'entregador': request.user.username}
            )
            audit_logger.info(
                f"PEDIDO_TOMAR | #{pedido.id} | Camionero: {request.user.username} | IP: {ip}"
            )

        messages.success(request, f"Pedido #{pedido.id} tomado. Dirígete al domicilio")
    except Pedido.DoesNotExist:
        # Puede ser race condition (otro camionero lo tomó primero) o pedido inexistente
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
        security_logger.warning(
            f"PEDIDO_TOMAR_FAIL | Pedido #{pedido_id} no disponible | "
            f"Camionero: {request.user.username} | IP: {ip} | "
            f"(posible race condition o pedido ya tomado)"
        )
        messages.error(request, "El pedido ya no está disponible o ya fue tomado.")

    return redirect("entregas_lista")



# Camionero: marcar pedido como entregado
@login_required
@require_POST
def camionero_marcar_entregado(request, pedido_id):
    """Camionero marca pedido como 'entregado'."""
    resp = require_roles(request, ["camionero"], "index", "Solo los camioneros pueden marcar entregas.")
    if resp:
        return resp

    try:
        with transaction.atomic():
            pedido = Pedido.objects.select_for_update().get(
                id=pedido_id,
                estado="en_ruta",
                entregador=request.user,
                origen="telefono"
            )
    except Pedido.DoesNotExist:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
        # Log detallado: puede revelar si el entregador del pedido es otro camionero
        try:
            pedido_real = Pedido.objects.get(id=pedido_id)
            security_logger.warning(
                f"PEDIDO_ENTREGAR_FAIL | #{pedido_id} | "
                f"Solicitado por: {request.user.username} | IP: {ip} | "
                f"Estado real: {pedido_real.estado} | "
                f"Entregador real: {pedido_real.entregador}"
            )
        except Pedido.DoesNotExist:
            security_logger.warning(
                f"PEDIDO_ENTREGAR_FAIL | #{pedido_id} no existe | "
                f"Solicitado por: {request.user.username} | IP: {ip}"
            )
        messages.error(request, "El pedido no existe, no está en ruta o no te pertenece.")
        return redirect("entregas_lista")

    estado_anterior = pedido.estado
    pedido.estado = "entregado"
    pedido.save()

    HistorialEstadoPedido.objects.create(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo="entregado",
        cambiado_por=request.user,
        fecha_cambio=timezone.now(),
    )

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
    AuditoriaAccion.registrar(
        request=request,
        tipo='PEDIDO_UPDATE',
        descripcion=f'Camionero {request.user.username} marcó Pedido #{pedido.id} como ENTREGADO',
        objeto=pedido,
        datos_anteriores={'estado': estado_anterior, 'entregador': request.user.username},
        datos_nuevos={'estado': 'entregado', 'entregador': request.user.username}
    )
    audit_logger.info(
        f"PEDIDO_ENTREGAR | #{pedido.id} | Camionero: {request.user.username} | IP: {ip}"
    )
    messages.success(request, f"¡Pedido #{pedido.id} marcado como ENTREGADO exitosamente!")
    return redirect("entregas_lista")



# Camionero: cancelar entrega
@login_required
@require_POST
def camionero_cancelar_entrega(request, pedido_id):
    """Camionero devuelve pedido a 'pendiente' si no puede entregar."""
    resp = require_roles(request, ["camionero"], "camionero_entregas", "Solo camioneros pueden cancelar entregas.")
    if resp:
        return resp

    try:
        pedido = Pedido.objects.get(
            id=pedido_id,
            estado="en_ruta",
            entregador=request.user
        )
        estado_anterior = pedido.estado
        pedido.estado = "cancelado"
        # No se limpia entregador: el camionero sigue asignado para poder ver el pedido en su historial
        pedido.save()

        HistorialEstadoPedido.objects.create(
            pedido=pedido,
            estado_anterior=estado_anterior,
            estado_nuevo="cancelado",
            cambiado_por=request.user,
            fecha_cambio=timezone.now(),
        )

        HistorialCambioPedido.objects.create(
            pedido=pedido,
            usuario=request.user,
            descripcion=f"Entrega cancelada por el camionero {request.user.get_full_name() or request.user.username} (estaba en ruta)."
        )

        AuditoriaAccion.registrar(
            request=request,
            tipo='PEDIDO_CANCEL',
            descripcion=f'Pedido #{pedido.id} cancelado por camionero {request.user.username} (estaba en ruta)',
            objeto=pedido,
            datos_anteriores={'estado': estado_anterior, 'entregador': request.user.username},
            datos_nuevos={'estado': 'cancelado'}
        )
        audit_logger.info(f"PEDIDO_CANCEL | #{pedido.id} | By camionero: {request.user.username}")

        messages.warning(request, f"Pedido #{pedido.id} ha sido cancelado.")
    except Pedido.DoesNotExist:
        messages.error(request, "El pedido no está en ruta o no te pertenece.")

    return redirect("entregas_lista")



# Telefonista: marcar venta como "tarreo" (prepago)
@login_required
@never_cache
def tarreo_pedido(request):
    """Registrar venta prepagada sin entrega (puede ser entregada después)."""
    resp = require_roles(request, ["camionero"], "index", "Acceso solo para camioneros.")
    if resp:
        return resp

    balones = get_balones_activos_ordenados()

    if request.method == 'POST':
        # Verificar token anti-duplicado
        token_enviado = request.POST.get('form_token')
        token_sesion = request.session.pop('form_token_tarreo', None)
        if not token_enviado or token_enviado != token_sesion:
            messages.warning(request, "Esta venta ya fue registrada o el formulario expiró.")
            return redirect('entregas_mias')

        metodo_pago = request.POST.get('metodo_pago')
        direccion_ingresada = request.POST.get('direccion_entrega', '').strip()

        if not metodo_pago:
            messages.error(request, "Selecciona un método de pago.")
            return render(request, 'entregas/tarreo.html', {'balones': balones})

        # Dirección por defecto si está vacía
        direccion_final = direccion_ingresada if direccion_ingresada else "Tarreo / venta directa en camión"

        # Crear cabecera del pedido
        pedido = Pedido(
            origen='tarreo',
            metodo_pago=metodo_pago,
            direccion_entrega=direccion_final,
            registrador=request.user,
            entregador=request.user,
            estado='entregado',  # venta directa → entregado inmediatamente
            fecha=timezone.now(),
        )
        pedido.bodega = request.user.bodega
        pedido.save()

        # Procesar detalles (similar a transaccional_pedido)
        detalles_guardados = 0
        total_monto = 0

        for balon in balones:
            qty_key = f'cantidad_{balon.id}'
            cantidad_str = request.POST.get(qty_key, '0')
            try:
                cantidad = int(cantidad_str)
            except ValueError:
                cantidad = 0

            if cantidad > 0:
                detalle = DetallePedido(
                    pedido=pedido,
                    balon=balon,
                    cantidad=cantidad,
                    precio_venta_unitario=balon.precio_domicilio,
                    precio_compra_unitario=balon.precio_compra,
                    # NO pasamos subtotal aquí — se calcula solo
                )
                detalle.save()

                # Acumular total usando el property subtotal (como en transaccional)
                total_monto += detalle.subtotal
                detalles_guardados += 1

        if detalles_guardados == 0:
            pedido.delete()
            messages.error(request, "Debe agregar al menos un producto.")
            return render(request, 'entregas/tarreo.html', {'balones': balones})

        # Actualizar total en el pedido (si tienes el método calcular_totales)
        pedido.monto_total = total_monto
        pedido.save()

        # Historial
        HistorialEstadoPedido.objects.create(
            pedido=pedido,
            estado_nuevo='entregado',
            cambiado_por=request.user,
            comentario="Venta tarreo registrada y entregada directamente."
        )

        messages.success(request, f"¡Venta tarreo #{pedido.id} guardada correctamente con {detalles_guardados} producto(s)! Total: ${total_monto:,}")
        return redirect('entregas_mias')  # o 'entregas_lista'

    # GET
    form_token = uuid.uuid4().hex
    request.session['form_token_tarreo'] = form_token
    return render(request, 'entregas/tarreo.html', {
        'balones': balones,
        'form_token': form_token,
    })

