"""Vistas HTTP — pedidos."""

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from django_ratelimit.decorators import ratelimit

from mockups.forms import (
    DetalleFormSet,
    DetalleFormSetEdit,
    PedidoCabeceraForm,
)
from mockups.models import (
    AuditoriaAccion,
    HistorialCambioPedido,
    HistorialEstadoPedido,
    Pedido,
)
from mockups.services.exports import exportar_pedidos_excel
from mockups.utils.fechas import (
    now_chile,
    parse_fecha_rango,
)
from mockups.utils.permisos import require_roles, require_roles_api, filtrar_por_bodega, get_bodega_actual

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')




# ══════════════════════════════════════════════════════════════
# 5. OPERACIONES TRANSACCIONALES (Registro de Ventas/Pedidos)
# ══════════════════════════════════════════════════════════════

# Telefonista/Bodeguero: crear nuevo pedido
@login_required
@never_cache
@ratelimit(key='user', rate='30/h', method='POST', block=True)
def transaccional_pedido(request):
    """Registrar nueva venta o pedido según rol del usuario.

    Vista principal para Telefonistas y Bodegueros con detalles inline.
    - Telefonistas: pedidos 'pendientes' con precio domicilio.
    - Bodegueros: pedidos 'entregados' con precio local.
    """
    resp = require_roles(request, ["telefonista", "bodeguero"], "index", "Solo telefonista y/o bodeguero pueden generar pedidos.")
    if resp:
        return resp

    es_bodeguero = request.user.rol == "bodeguero"

    if request.method == "POST":
        # Verificar token anti-duplicado
        token_enviado = request.POST.get('form_token')
        token_sesion = request.session.pop('form_token_pedido', None)
        if not token_enviado or token_enviado != token_sesion:
            messages.warning(request, "Este pedido ya fue registrado o el formulario expiró.")
            return redirect("index")

        form_cabecera = PedidoCabeceraForm(request.POST, bodega=request.user.bodega)
        formset = DetalleFormSet(
            request.POST,
            instance=Pedido(),
            form_kwargs={'user': request.user}
        )

        if form_cabecera.is_valid() and formset.is_valid():
            pedido = form_cabecera.save(commit=False)
            pedido.registrador = request.user
            # Lógica de negocio según rol
            pedido.origen = "local" if es_bodeguero else "telefono"
            pedido.estado = "entregado" if es_bodeguero else "pendiente"
            pedido.fecha = now_chile()
            pedido.bodega = request.user.bodega
            pedido.save()

            # Guardar detalles del pedido
            detalles_guardados = 0
            for detalle_form in formset:
                if detalle_form.cleaned_data and not detalle_form.cleaned_data.get('DELETE', False):
                    balon = detalle_form.cleaned_data.get('balon')
                    cantidad = detalle_form.cleaned_data.get('cantidad')
                    
                    if balon and cantidad and cantidad > 0:
                        detalle = detalle_form.save(commit=False)
                        detalle.pedido = pedido
                        # Precio al momento de la venta
                        detalle.precio_venta_unitario = balon.precio_local if es_bodeguero else balon.precio_domicilio
                        detalle.precio_compra_unitario = balon.precio_compra
                        detalle.save()
                        detalles_guardados += 1

            if detalles_guardados == 0:
                pedido.delete() # Deshacer cabecera si no hay detalles
                messages.error(request, "Debes agregar al menos un producto válido.")
                return render(request, "pedidos/transaccional_pedido.html", {
                    "form_cabecera": form_cabecera,
                    "formset": formset,
                    "es_bodeguero": es_bodeguero,
                })

            # Registrar historial y recalcular totales
            HistorialEstadoPedido.objects.create(
                pedido=pedido,
                estado_anterior="pendiente",
                estado_nuevo=pedido.estado,
                cambiado_por=request.user,
                fecha_cambio=timezone.now(),
            )
            pedido.calcular_totales()
            
            # Auditoría: Pedido creado
            AuditoriaAccion.registrar(
                request=request,
                tipo='PEDIDO_CREATE',
                descripcion=f'Pedido #{pedido.id} creado - {pedido.get_origen_display()}',
                objeto=pedido,
                datos_nuevos={
                    'sector': pedido.sector,
                    'direccion': pedido.direccion_entrega,
                    'origen': pedido.origen,
                    'estado': pedido.estado,
                    'total': str(pedido.monto_total),
                    'detalles': detalles_guardados
                }
            )
            audit_logger.info(f"PEDIDO_CREATE | #{pedido.id} | By: {request.user.username}")
            
            # ══════════════════════════════════════════════════════
            # NOTIFICACIONES PUSH A CAMIONEROS
            # Envío directo (evita perder pushes si Gunicorn recicla el worker)
            # ══════════════════════════════════════════════════════
            if pedido.estado == 'pendiente' and pedido.origen == 'telefono':
                try:
                    from mockups.push_notifications import notificar_nuevo_pedido
                    notificados = notificar_nuevo_pedido(pedido)
                    if notificados > 0:
                        audit_logger.info(
                            f"PUSH_SENT | Pedido #{pedido.id} -> {notificados} camioneros notificados"
                        )
                except Exception as e:
                    audit_logger.warning(f"PUSH_ERROR | Pedido #{pedido.id} | Error: {str(e)}")
            
            messages.success(request, f"¡Pedido #{pedido.id} registrado correctamente con {detalles_guardados} producto(s)!")
            return redirect("index")
        else:
            messages.error(request, "Hay errores en el formulario. Revisa los campos marcados.")

    else:
        form_cabecera = PedidoCabeceraForm(bodega=request.user.bodega)
        formset = DetalleFormSet(
            instance=Pedido(),
            form_kwargs={'user': request.user}
        )

    form_token = uuid.uuid4().hex
    request.session['form_token_pedido'] = form_token
    return render(request, "pedidos/transaccional_pedido.html", {
        "form_cabecera": form_cabecera,
        "formset": formset,
        "es_bodeguero": es_bodeguero,
        "form_token": form_token,
    })



# Telefonista/Bodeguero: editar pedido existente
@login_required
def editar_pedido(request, pedido_id):
    """Modificar pedido pendiente o en ruta con reglas por rol.

    - Telefonista: solo sus propios pedidos.
    - Camionero: solo los que tiene en ruta (estado 'en_ruta').
    - Bodeguero: pedidos propios + pedidos de telefonistas.
    - Jefe/Admin: cualquier pedido.
    Solo permite editar si está pendiente o en ruta.
    Registra cambios en HistorialCambioPedido.
    """
    pedido = get_object_or_404(Pedido, id=pedido_id)

    # Validación de bodega: el pedido debe pertenecer a la bodega del usuario
    bodega_usuario = get_bodega_actual(request)
    if bodega_usuario and pedido.bodega != bodega_usuario:
        messages.error(request, "Este pedido no pertenece a tu bodega.")
        return redirect('index')

    user_rol = request.user.rol

    # 1. Validación por rol y propiedad del pedido
    if user_rol == 'telefonista':
        if pedido.registrador != request.user:
            messages.error(request, "Como telefonista solo puedes editar los pedidos que tú registraste.")
            return redirect('pedidos_mios')

    elif user_rol == 'camionero':
        if pedido.entregador != request.user or pedido.estado != 'en_ruta':
            messages.error(request, "Como camionero solo puedes editar pedidos que estén en tu ruta actual (estado 'en ruta').")
            return redirect('entregas_lista')

    elif user_rol == 'bodeguero':
        # Puede editar propios o de telefonistas
        if pedido.registrador != request.user and pedido.registrador.rol != 'telefonista':
            messages.error(request, "Como bodeguero solo puedes editar tus pedidos o los registrados por telefonistas.")
            return redirect('pedidos_mios')

    # Jefe y admin pueden editar cualquier pedido → no hay restricción adicional aquí
    elif user_rol not in ('telefonista', 'camionero', 'bodeguero', 'jefe', 'admin'):
        messages.error(request, "Tu rol no tiene permisos para editar pedidos.")
        return redirect('index')

    # URL de retorno según rol (usada en redirect al guardar y en botón Cancelar del template)
    _redirect_map = {
        'telefonista': 'pedidos_mios',
        'bodeguero':   'pedidos_mios',
        'camionero':   'entregas_lista',
        'jefe':        'pedidos_consulta',
        'admin':       'pedidos_consulta',
    }
    url_volver = _redirect_map.get(user_rol, 'index')

    # 2. Bloqueo general por estado (independiente del rol)
    if pedido.estado not in ['pendiente', 'en_ruta']:
        if pedido.estado == 'entregado':
            messages.error(request, "No se puede editar un pedido que ya fue entregado.")
        elif pedido.estado == 'cancelado':
            messages.error(request, "No se puede editar un pedido que fue cancelado.")
        else:
            messages.error(request, f"No se puede editar un pedido en estado '{pedido.get_estado_display()}'.")
        return redirect('pedidos_detalle', pedido_id=pedido.id)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Procesamiento del formulario
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if request.method == 'POST':
        old_data = {
            'metodo_pago': pedido.metodo_pago,
            'sector': pedido.sector,
            'direccion_entrega': pedido.direccion_entrega,
            'detalles': list(pedido.detalles.values('balon_id', 'cantidad')),
            'estado': pedido.estado,
        }

        form_cabecera = PedidoCabeceraForm(request.POST, instance=pedido, bodega=request.user.bodega)
        formset = DetalleFormSetEdit(
            request.POST, 
            instance=pedido,
            form_kwargs={'user': request.user}
        )

        if form_cabecera.is_valid() and formset.is_valid():
            form_cabecera.save()

            # Eliminar filas marcadas con DELETE (deleted_forms está disponible tras is_valid())
            for form in formset.deleted_forms:
                if form.instance.pk:
                    form.instance.delete()

            # Guardar detalles con precios actualizados
            es_bodeguero = request.user.rol == "bodeguero"
            detalles_guardados = 0
            instances = formset.save(commit=False)
            for detalle in instances:
                detalle.pedido = pedido
                detalle.precio_venta_unitario = detalle.balon.precio_local if es_bodeguero else detalle.balon.precio_domicilio
                detalle.precio_compra_unitario = detalle.balon.precio_compra
                detalle.save()
                detalles_guardados += 1

            # Detectar qué cambió (para historial claro)
            cambios = []
            if pedido.metodo_pago != old_data['metodo_pago']:
                cambios.append(f"Método pago: {old_data['metodo_pago']} → {pedido.metodo_pago}")
            if pedido.sector != old_data['sector']:
                cambios.append(f"Sector: {old_data['sector'] or '—'} → {pedido.sector or '—'}")
            if pedido.direccion_entrega != old_data['direccion_entrega']:
                cambios.append("Dirección modificada")
            if list(pedido.detalles.values('balon_id', 'cantidad')) != old_data['detalles']:
                cambios.append("Productos/cantidades modificados")
            if pedido.estado != old_data['estado']:
                cambios.append(f"Estado: {old_data['estado']} → {pedido.estado}")

            if cambios:
                HistorialCambioPedido.objects.create(
                    pedido=pedido,
                    usuario=request.user,
                    descripcion="; ".join(cambios)
                )
                
                # Auditoría: Pedido modificado
                AuditoriaAccion.registrar(
                    request=request,
                    tipo='PEDIDO_UPDATE',
                    descripcion=f'Pedido #{pedido.id} modificado: {"; ".join(cambios)}',
                    objeto=pedido,
                    datos_anteriores=old_data,
                    datos_nuevos={
                        'metodo_pago': pedido.metodo_pago,
                        'sector': pedido.sector,
                        'direccion': pedido.direccion_entrega,
                        'estado': pedido.estado
                    }
                )
                audit_logger.info(f"PEDIDO_UPDATE | #{pedido.id} | By: {request.user.username} | {'; '.join(cambios)}")

            # Recalcular totales
            pedido.calcular_totales()
            messages.success(request, f"Pedido #{pedido.id} actualizado correctamente.")
            return redirect(url_volver)

        else:
            messages.error(request, "Por favor corrige los errores en el formulario.")
    else:
        form_cabecera = PedidoCabeceraForm(instance=pedido, bodega=request.user.bodega)
        formset = DetalleFormSetEdit(
            instance=pedido,
            form_kwargs={'user': request.user}
        )

    return render(request, 'pedidos/editar_pedido.html', {
        'pedido': pedido,
        'form_cabecera': form_cabecera,
        'formset': formset,
        'url_volver': url_volver,
    })



# ══════════════════════════════════════════════════════════════
# 6. VISTAS DE PEDIDOS POR ROL DE USUARIO
# ══════════════════════════════════════════════════════════════

# --- TELEFONISTA / BODEGUERO ---

# Telefonista/Bodeguero: ver sus pedidos de hoy
@login_required
def mis_pedidos_hoy(request):
    """Historial de ventas del día actual de este usuario. Excluye cancelados."""
    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()
    
    # Rango de "Hoy"
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), timezone=tz_chile)
    fin_dia = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), timezone=tz_chile)
    
    pedidos_hoy = Pedido.objects.filter(
        registrador=request.user,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).select_related('registrador', 'entregador').prefetch_related('detalles__balon').order_by('-fecha')

    pedidos_activos = pedidos_hoy.exclude(estado='cancelado')
    total_monto_hoy = pedidos_activos.aggregate(total=Sum('monto_total'))['total'] or 0
    total_pedidos = pedidos_activos.count()

    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_monto_hoy": total_monto_hoy,
        "es_telefonista": request.user.rol == "telefonista",
        "fecha_hoy": hoy,
        "total_pedidos": total_pedidos,
        "total_monto_hoy_int": int(total_monto_hoy),
    }
    return render(request, "pedidos/mis_pedidos_hoy.html", context)



@login_required
@ratelimit(key='user', rate='120/m', method='GET', block=True)
def mis_pedidos_hoy_api(request):
    """Endpoint AJAX: devuelve HTML actualizado de los pedidos del telefonista/bodeguero de hoy."""
    resp = require_roles_api(request, ['telefonista', 'bodeguero'])
    if resp:
        return resp

    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), timezone=tz_chile)
    fin_dia = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), timezone=tz_chile)

    pedidos_hoy = Pedido.objects.filter(
        registrador=request.user,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).select_related('registrador', 'entregador').prefetch_related('detalles__balon').order_by('-fecha')

    pedidos_activos = pedidos_hoy.exclude(estado='cancelado')
    total_monto_hoy = pedidos_activos.aggregate(total=Sum('monto_total'))['total'] or 0
    total_pedidos = pedidos_activos.count()

    context = {
        'pedidos_hoy': pedidos_hoy,
        'total_monto_hoy': total_monto_hoy,
        'total_pedidos': total_pedidos,
        'es_telefonista': request.user.rol == 'telefonista',
        'user': request.user,
    }
    html = render(request, 'partials/_mis_pedidos_cards.html', context).content.decode('utf-8')
    return JsonResponse({
        'html': html,
        'count': total_pedidos,
        'total_monto_hoy': int(total_monto_hoy),
    })



# Telefonista: cancelar pedido pendiente (antes de que un camionero lo tome)
@login_required
def telefonista_cancelar_pedido(request, pedido_id):
    """Cancela un pedido en estado 'pendiente' registrado por el telefonista."""
    resp = require_roles(request, ["telefonista"], "pedidos_mios", "Solo telefonistas pueden usar esta acción.")
    if resp:
        return resp

    if request.method != "POST":
        return redirect("pedidos_mios")

    pedido = get_object_or_404(Pedido, id=pedido_id)

    # Validación de bodega: el pedido debe pertenecer a la bodega del usuario
    bodega_usuario = get_bodega_actual(request)
    if bodega_usuario and pedido.bodega != bodega_usuario:
        messages.error(request, "Este pedido no pertenece a tu bodega.")
        return redirect("pedidos_mios")

    # Solo puede cancelar sus propios pedidos
    if pedido.registrador != request.user:
        messages.error(request, "Solo puedes cancelar pedidos que tú registraste.")
        return redirect("pedidos_mios")

    # Solo se puede cancelar si aún no lo tomó un camionero
    if pedido.estado != "pendiente":
        messages.error(
            request,
            f"El pedido #{pedido.id} ya no está pendiente (estado: {pedido.get_estado_display()}). "
            "Solo puedes cancelar pedidos que aún no hayan sido tomados por un camionero."
        )
        return redirect("pedidos_mios")

    estado_anterior = pedido.estado
    pedido.estado = "cancelado"
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
        descripcion="Pedido cancelado por el telefonista (cliente desistió antes de la entrega)."
    )

    AuditoriaAccion.registrar(
        request=request,
        tipo='PEDIDO_CANCEL',
        descripcion=f'Pedido #{pedido.id} cancelado por telefonista {request.user.username}',
        objeto=pedido,
        datos_anteriores={'estado': estado_anterior},
        datos_nuevos={'estado': 'cancelado'}
    )
    audit_logger.info(f"PEDIDO_CANCEL | #{pedido.id} | By: {request.user.username}")

    messages.warning(request, f"Pedido #{pedido.id} cancelado correctamente.")
    return redirect("pedidos_mios")

# ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
# 7. REPORTES Y CONSULTAS DE PEDIDOS (Admin/Jefe)
# ══════════════════════════════════════════════════════════════

# Admin/Jefe: búsqueda avanzada de pedidos
@login_required
def consultas_pedidos(request):
    """Búsqueda avanzada con filtros por estado, origen, fecha. Soporta exportación Excel."""
    resp = require_roles(request, ["jefe", "admin"], "index")
    if resp:
        return resp

    # Captura de filtros
    busqueda = request.GET.get("busqueda", "").strip()
    fechas_str = request.GET.get("fechas", "").strip()
    estado = request.GET.get("estado", "todos")
    origen = request.GET.get("origen", "todos")
    exportar = request.GET.get("exportar", "")

    # Parseo de fechas
    fecha_inicio, fecha_fin, fechas_display, _, _ = parse_fecha_rango(fechas_str)
    if fechas_str and not fecha_inicio:
        messages.warning(request, "Formato de fechas inválido. Usa el selector de fechas.")

    # Queryset base optimizado
    queryset = filtrar_por_bodega(Pedido.objects.select_related(
        "registrador", "entregador"
    ).prefetch_related(
        "detalles__balon"
    ).order_by("-fecha"), request)

    # Aplicación de filtros
    if fecha_inicio and fecha_fin:
        queryset = queryset.filter(fecha__range=(fecha_inicio, fecha_fin))

    if busqueda:
        queryset = queryset.filter(
            Q(sector__icontains=busqueda) |
            Q(direccion_entrega__icontains=busqueda) |
            Q(registrador__first_name__icontains=busqueda) |
            Q(registrador__last_name__icontains=busqueda) |
            Q(registrador__username__icontains=busqueda) |
            Q(entregador__first_name__icontains=busqueda) |
            Q(entregador__last_name__icontains=busqueda) |
            Q(entregador__username__icontains=busqueda) |
            Q(detalles__balon__nombre__icontains=busqueda)
        ).distinct()

    if estado != "todos":
        queryset = queryset.filter(estado=estado)
    else:
        # Por defecto, excluir cancelados (usuario puede verlos si selecciona explícitamente)
        queryset = queryset.exclude(estado='cancelado')

    if origen != "todos":
        queryset = queryset.filter(origen=origen)

    # Lógica de exportación
    if exportar == "excel":
        return exportar_pedidos_excel(queryset, fechas_display)

    # Paginación
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Estadísticas para el panel (una sola consulta agregada)
    stats = queryset.aggregate(
        total_pedidos=Count('id'),
        total_ventas=Sum('monto_total'),
        total_ganancias=Sum('ganancia_total'),
    )

    context = {
        'page_obj': page_obj,
        'estadisticas': {
            'total_pedidos': stats['total_pedidos'] or 0,
            'total_ventas': stats['total_ventas'] or 0,
            'total_ganancias': stats['total_ganancias'] or 0,
        },
        'estados_choices': Pedido.ESTADOS,
        'origenes_choices': Pedido.ORIGENES,
        'filtros': request.GET,
    }
    return render(request, 'pedidos/consultas_pedidos.html', context)



# Admin/Jefe: ver detalle de un pedido específico
@login_required
def detalle_pedido(request, pedido_id):
    """Detalle completo de un pedido: productos, estado, historial de cambios."""
    resp = require_roles(request, ["jefe", "admin", "telefonista", "bodeguero", "camionero"], "index", "No tienes permiso para ver este pedido.")
    if resp:
        return resp

    try:
        pedido = Pedido.objects.select_related('registrador', 'entregador').prefetch_related('detalles__balon').get(id=pedido_id)

        # Validación de bodega
        bodega_usuario = get_bodega_actual(request)
        if bodega_usuario and pedido.bodega != bodega_usuario:
            messages.error(request, "Este pedido no pertenece a tu bodega.")
            return redirect('index')

        # Validación de propiedad: si no es jefe/admin, verificar relación
        if request.user.rol not in ["jefe", "admin"]:
            if pedido.registrador != request.user and pedido.entregador != request.user:
                messages.error(request, "No tienes permiso para ver este pedido.")
                return redirect("index")

    except Pedido.DoesNotExist:
        messages.error(request, "El pedido solicitado no existe.")
        return redirect("pedidos_consulta" if request.user.rol in ["jefe", "admin"] else "index")

    context = {
        "pedido": pedido,
        "puede_editar": request.user.rol in ["jefe", "admin"],
    }
    return render(request, "pedidos/detalle_pedido.html", context)

