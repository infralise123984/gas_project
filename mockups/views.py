# mockups/views.py

# Django core y utilidades
from datetime import date, datetime
from calendar import monthrange
from django.utils import timezone
from zoneinfo import ZoneInfo
# Django contrib
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

# Modelos, consultas y paginación
from django.db.models import Count, F, Q, Sum
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

from django.http import HttpResponse
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side



# App local
from .forms import DetallePedidoForm, PedidoCabeceraForm ,DetalleFormSet
from .models import Pedido, TipoBalon, Usuario, DetallePedido,HistorialEstadoPedido


# ──────────────────────────────────────────────────────────────
# Funciones auxiliares reutilizables
# ──────────────────────────────────────────────────────────────
def parse_fecha_rango(fechas_str):
    """
    Parsea un rango de fechas en formato 'YYYY-MM-DD to YYYY-MM-DD'.
    Retorna tupla: (fecha_inicio, fecha_fin, display_str, desde_str, hasta_str)
    Si el formato es inválido, retorna (None, None, "", "", "")
    """
    if not fechas_str:
        return None, None, "", "", ""
    
    fechas_clean = fechas_str.replace("+", " ").strip()
    if " to " not in fechas_clean:
        return None, None, "", "", ""
    
    try:
        desde_str, hasta_str = fechas_clean.split(" to ", 1)
        desde_str = desde_str.strip()
        hasta_str = hasta_str.strip()
        desde = datetime.strptime(desde_str, "%Y-%m-%d")
        hasta = datetime.strptime(hasta_str, "%Y-%m-%d")
        
        fecha_inicio = timezone.make_aware(desde.replace(hour=0, minute=0, second=0, microsecond=0))
        fecha_fin = timezone.make_aware(hasta.replace(hour=23, minute=59, second=59, microsecond=999999))
        
        fecha_display = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
        return fecha_inicio, fecha_fin, fecha_display, desde_str, hasta_str
    except ValueError:
        return None, None, "", "", ""


# Utilidades comunes
def require_roles(request, roles, redirect_to="index", message="No tienes permiso para acceder a esta sección."):
    """Comprueba el rol del usuario y retorna una HttpResponse de redirección si no tiene permiso.
    Retorna None cuando el usuario tiene permiso.
    """
    if request.user.rol not in roles:
        messages.error(request, message)
        return redirect(redirect_to)
    return None


def get_display_name(user):
    """Devuelve el nombre legible de un usuario o '—' si es None."""
    if not user:
        return "—"
    nombre = (user.get_full_name() or "").strip()
    return nombre if nombre else user.username


def index(request):
    return render(request, "index.html")




@login_required
def precios_balones(request):
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para gestionar precios.")
    if resp:
        return resp

    balones = TipoBalon.objects.all().order_by("peso_neto_gas")

    if request.method == "POST":
        cambios_realizados = False

        for balon in balones:
            compra_key = f"precio_compra_{balon.id}"
            local_key  = f"precio_local_{balon.id}"
            dom_key    = f"precio_domicilio_{balon.id}"
            activo_key = f"activo_{balon.id}"

            nuevo_compra_str = request.POST.get(compra_key)
            nuevo_local_str  = request.POST.get(local_key)
            nuevo_dom_str    = request.POST.get(dom_key)
            nuevo_activo     = activo_key in request.POST

            try:
                nuevo_compra     = int(nuevo_compra_str) if nuevo_compra_str else balon.precio_compra
                nuevo_local      = int(nuevo_local_str)  if nuevo_local_str  else balon.precio_local
                nuevo_domicilio  = int(nuevo_dom_str)    if nuevo_dom_str    else balon.precio_domicilio

                if nuevo_compra < 0 or nuevo_local < 0 or nuevo_domicilio < 0:
                    raise ValueError("Precios no pueden ser negativos")
            except ValueError:
                messages.error(request, f"Precio inválido para {balon.nombre}. Se ignoraron cambios en esta fila.")
                continue

            # Detectar si hubo cambio real
            if (balon.precio_compra != nuevo_compra or
                balon.precio_local != nuevo_local or
                balon.precio_domicilio != nuevo_domicilio or
                balon.activo != nuevo_activo):

                balon.precio_compra    = nuevo_compra
                balon.precio_local     = nuevo_local
                balon.precio_domicilio = nuevo_domicilio
                balon.activo           = nuevo_activo
                balon.actualizado_por   = request.user
                balon.save()  # ← Esto genera el registro en HistorialPrecioBalon automáticamente

                cambios_realizados = True

        if cambios_realizados:
            messages.success(request, "Precios y disponibilidad actualizados correctamente.")
        else:
            messages.info(request, "No se detectaron cambios válidos.")

        return redirect("precios_balones")

    return render(request, "precios_balones.html", {"balones": balones})




@login_required
def crear_usuario(request):
    # Permitir solo admin o superuser
    if not (request.user.rol == "admin" or request.user.is_superuser):
        messages.error(request, "No tienes permiso para crear usuarios.")
        return redirect("index")

    # Obtenemos las choices del modelo para el select
    roles_choices = Usuario.ROLES

    if request.method == "POST":
        username = request.POST.get("username")
        first_name = request.POST.get("first_name", "")
        last_name = request.POST.get("last_name", "")
        telefono = request.POST.get("telefono", "")
        rol = request.POST.get("rol")
        password1 = request.POST.get("password1")
        password2 = request.POST.get("password2")

        # Validaciones básicas
        if not all([username, rol, password1, password2]):
            messages.error(
                request, "Todos los campos obligatorios deben estar completos."
            )
        elif password1 != password2:
            messages.error(request, "Las contraseñas no coinciden.")
        elif len(password1) < 8:
            messages.error(request, "La contraseña debe tener al menos 8 caracteres.")
        else:
            if Usuario.objects.filter(username=username).exists():
                messages.error(
                    request, "Ya existe un usuario con ese nombre de usuario."
                )
            else:
                # Crear el usuario
                user = Usuario.objects.create_user(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    telefono=telefono or None,
                    rol=rol,
                    password=password1,
                )
                user.is_active = True
                user.save()

                messages.success(
                    request,
                    f"Usuario '{user.get_full_name() or user.username}' creado correctamente con rol {user.get_rol_display()}.",
                )
                return redirect("reporte_ventas")

    return render(request, "crear_usuario.html", {"roles_choices": roles_choices})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("index")  # mejor ir al index bonito

    if request.method == "POST":
        # Verificación adicional: si ya está autenticado al momento del POST, redirigir
        if request.user.is_authenticated:
            return redirect("index")
        
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            messages.success(
                request, f"¡Bienvenido, {user.get_full_name() or user.username}!"
            )
            return redirect("index")
        else:
            messages.error(request, "Usuario o contraseña incorrectos")

    return render(request, "login.html")


def logout_view(request):
    logout(request)
    messages.success(request, "Has cerrado sesión correctamente")
    return redirect("login")




# ────────────────────────────────────────────────────────────────
# 1. Vista completa: Consultas avanzadas (solo Jefe y Administrador)
# ────────────────────────────────────────────────────────────────
# Agregar estos imports al inicio del archivo views.py

# ... (mantener los demás imports)


@login_required
def consultas_pedidos(request):
    """Vista mejorada con más detalles y opción de exportar a Excel"""
    resp = require_roles(request, ["jefe", "admin"], "index")
    if resp:
        return resp

    # Captura de parámetros GET
    busqueda = request.GET.get("busqueda", "").strip()
    fechas_str = request.GET.get("fechas", "").strip()
    estado = request.GET.get("estado", "todos")
    origen = request.GET.get("origen", "todos")
    exportar = request.GET.get("exportar", "")

    # Parsear fechas
    fecha_inicio, fecha_fin, fechas_display, _, _ = parse_fecha_rango(fechas_str)
    if fechas_str and not fecha_inicio:
        messages.warning(request, "Formato de fechas inválido. Usa el selector de fechas.")

    # Base queryset con todos los detalles
    queryset = Pedido.objects.select_related(
        "registrador", "entregador"
    ).prefetch_related(
        "detalles__balon"
    ).order_by("-fecha")

    # Filtro por rango de fechas
    if fecha_inicio and fecha_fin:
        queryset = queryset.filter(fecha__range=(fecha_inicio, fecha_fin))

    # Filtro por texto (búsqueda)
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

    # Filtro por estado
    if estado != "todos":
        queryset = queryset.filter(estado=estado)

    # Filtro por origen
    if origen != "todos":
        queryset = queryset.filter(origen=origen)

    # Si se solicita exportar a Excel
    if exportar == "excel":
        return exportar_pedidos_excel(queryset, fechas_display)

    # Paginación
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Estadísticas rápidas
    total_pedidos = queryset.count()
    total_ventas = queryset.aggregate(total=Sum('monto_total'))['total'] or 0
    total_ganancias = queryset.aggregate(total=Sum('ganancia_total'))['total'] or 0
    context = {
    'page_obj': page_obj,                    # del Paginator
    'estadisticas': {
        'total_pedidos': total_pedidos,
        'total_ventas': total_ventas,
        'total_ganancias': total_ganancias,
    },
    'estados_choices': Pedido.ESTADOS,       # lista de tuplas
    'origenes_choices': Pedido.ORIGENES,
    'filtros': request.GET,                  # para mantener valores
}
    return render(request, "consultas_pedidos.html", context)


def exportar_pedidos_excel(queryset, rango_fechas):
    """Exporta los pedidos filtrados a un archivo Excel"""
    
    # Crear workbook y hoja
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pedidos"

    # Estilos
    header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=12)
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # Título del reporte
    ws.merge_cells('A1:L1')
    title_cell = ws['A1']
    title_cell.value = f"Reporte de Pedidos - KIM GAS Rancagua"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal='center', vertical='center')
    
    if rango_fechas:
        ws.merge_cells('A2:L2')
        date_cell = ws['A2']
        date_cell.value = f"Período: {rango_fechas}"
        date_cell.alignment = Alignment(horizontal='center')

    # Encabezados (fila 4)
    headers = [
        'ID', 'Fecha', 'Hora', 'Estado', 'Origen', 
        'Productos', 'Cantidad Total', 'Monto', 'Ganancia',
        'Registrado por', 'Entregado por', 'Sector/Dirección'
    ]
    
    header_row = 4
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_num)
        cell.value = header
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    # Datos
    row_num = header_row + 1
    for pedido in queryset:
        # Resumen de productos
        productos_list = []
        cantidad_total = 0
        for detalle in pedido.detalles.all():
            productos_list.append(f"{detalle.cantidad}×{detalle.balon.nombre}")
            cantidad_total += detalle.cantidad
        productos_str = " + ".join(productos_list) if productos_list else "—"

        # Registrador
        registrador = get_display_name(pedido.registrador)
        entregador = get_display_name(pedido.entregador)
        
        # Sector/Dirección
        ubicacion = f"{pedido.sector or '—'} / {pedido.direccion_entrega or '—'}"

        # Escribir fila
        data = [
            pedido.id,
            pedido.fecha.strftime("%d/%m/%Y"),
            pedido.fecha.strftime("%H:%M"),
            pedido.get_estado_display(),
            pedido.get_origen_display(),
            productos_str,
            cantidad_total,
            float(pedido.monto_total),
            float(pedido.ganancia_total),
            registrador,
            entregador,
            ubicacion
        ]

        for col_num, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col_num)
            cell.value = value
            cell.border = border
            
            # Formato de moneda para columnas de monto
            if col_num in [8, 9]:
                cell.number_format = '"$"#,##0'
            
            # Alineación
            if col_num in [1, 2, 3, 7, 8, 9]:  # Números y montos centrados
                cell.alignment = Alignment(horizontal='center')
            else:
                cell.alignment = Alignment(horizontal='left')

        row_num += 1

    # Totales
    row_num += 1
    ws.cell(row=row_num, column=1).value = "TOTALES:"
    ws.cell(row=row_num, column=1).font = Font(bold=True)
    
    total_monto = sum(float(p.monto_total) for p in queryset)
    total_ganancia = sum(float(p.ganancia_total) for p in queryset)
    
    ws.cell(row=row_num, column=8).value = total_monto
    ws.cell(row=row_num, column=8).number_format = '"$"#,##0'
    ws.cell(row=row_num, column=8).font = Font(bold=True)
    
    ws.cell(row=row_num, column=9).value = total_ganancia
    ws.cell(row=row_num, column=9).number_format = '"$"#,##0'
    ws.cell(row=row_num, column=9).font = Font(bold=True)

    # Ajustar ancho de columnas
    column_widths = {
        'A': 8, 'B': 12, 'C': 10, 'D': 12, 'E': 18,
        'F': 25, 'G': 12, 'H': 12, 'I': 12, 'J': 20, 'K': 20, 'L': 35
    }
    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width

    # Preparar respuesta HTTP
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f"pedidos_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    wb.save(response)
    return response


@login_required
def detalle_pedido(request, pedido_id):
    """Vista detallada de un pedido específico"""
    resp = require_roles(request, ["jefe", "admin", "telefonista", "bodeguero", "camionero"], "index", "No tienes permiso para ver este pedido.")
    if resp:
        return resp

    try:
        pedido = Pedido.objects.select_related(
            'registrador', 'entregador'
        ).prefetch_related(
            'detalles__balon'
        ).get(id=pedido_id)

        # Verificar permisos: si no es jefe/admin, solo puede ver sus propios pedidos
        if request.user.rol not in ["jefe", "admin"]:
            if pedido.registrador != request.user and pedido.entregador != request.user:
                messages.error(request, "No tienes permiso para ver este pedido.")
                return redirect("index")

    except Pedido.DoesNotExist:
        messages.error(request, "El pedido solicitado no existe.")
        return redirect("consultas_pedidos" if request.user.rol in ["jefe", "admin"] else "index")

    context = {
        "pedido": pedido,
        "puede_editar": request.user.rol in ["jefe", "admin"],
    }
    
    return render(request, "detalle_pedido.html", context)

# ────────────────────────────────────────────────────────────────
# 2. Vista camionero: Mis Entregas Realizadas
# ────────────────────────────────────────────────────────────────
@login_required
def mis_entregas_camionero(request):
    resp = require_roles(request, ["camionero"], "index", "Solo camioneros pueden ver sus entregas.")
    if resp:
        return resp

    # Quitamos el filtro de fecha para probar
    pedidos_hoy = Pedido.objects.filter(
        entregador=request.user,
        estado="entregado"
    ).order_by("-fecha").prefetch_related("detalles__balon")

    return render(request, "mis_entregas_camionero.html", {
        "pedidos_hoy": pedidos_hoy,
    })


# ────────────────────────────────────────────────────────────────
# 3. Vista telefonista / bodeguero: Mis pedidos / ventas de hoy
# ────────────────────────────────────────────────────────────────
@login_required
def mis_pedidos_hoy(request):
    """
    Muestra los pedidos/ventas registrados por el usuario actual en el día de hoy.
    Para telefonistas: pedidos telefónicos
    Para bodegueros: ventas en local
    """
    # Obtener la zona horaria de Chile
    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()
    
    # Crear el rango de fechas para hoy (desde las 00:00:00 hasta las 23:59:59)
    inicio_dia = timezone.make_aware(
        datetime.combine(hoy, datetime.min.time()),
        timezone=tz_chile
    )
    fin_dia = timezone.make_aware(
        datetime.combine(hoy, datetime.max.time()),
        timezone=tz_chile
    )
    
    # Filtrar pedidos del día
    pedidos_hoy = Pedido.objects.filter(
        registrador=request.user,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).select_related(
        'registrador', 
        'entregador'
    ).prefetch_related(
        'detalles__balon'
    ).order_by('-fecha')

    # Calcular total del día
    total_monto_hoy = pedidos_hoy.aggregate(total=Sum('monto_total'))['total'] or 0

    es_telefonista = request.user.rol == "telefonista"

    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_monto_hoy": total_monto_hoy,
        "es_telefonista": es_telefonista,
        "fecha_hoy": hoy,  # Para debug
        "total_pedidos": pedidos_hoy.count(),  # Para debug
    }
    
    return render(request, "mis_pedidos_hoy.html", context)





@login_required
def transaccional_pedido(request):
    resp = require_roles(request, ["telefonista", "bodeguero"], "index", "Solo telefonista y/o bodeguero pueden generar pedidos.")
    if resp:
        return resp

    es_bodeguero = request.user.rol == "bodeguero"

    if request.method == "POST":
        form_cabecera = PedidoCabeceraForm(request.POST)
        formset = DetalleFormSet(
            request.POST,
            instance=Pedido(),
            form_kwargs={'user': request.user}
        )

        if form_cabecera.is_valid() and formset.is_valid():
            pedido = form_cabecera.save(commit=False)
            pedido.registrador = request.user
            pedido.origen = "local" if es_bodeguero else "telefono"
            # Bodeguero: venta entregada al instante. Telefonista: pendiente de entrega
            pedido.estado = "entregado" if es_bodeguero else "pendiente"
            pedido.save()

            # Guardar los detalles válidos
            detalles_guardados = 0
            for detalle_form in formset:
                if detalle_form.cleaned_data and not detalle_form.cleaned_data.get('DELETE', False):
                    balon = detalle_form.cleaned_data.get('balon')
                    cantidad = detalle_form.cleaned_data.get('cantidad')
                    
                    if balon and cantidad:
                        detalle = detalle_form.save(commit=False)
                        detalle.pedido = pedido
                        detalle.precio_venta_unitario = balon.precio_local if es_bodeguero else balon.precio_domicilio
                        detalle.precio_compra_unitario = balon.precio_compra
                        detalle.save()
                        detalles_guardados += 1

            if detalles_guardados == 0:
                pedido.delete()
                messages.error(request, "Debes agregar al menos un producto válido.")
                return render(request, "transaccional_pedido.html", {
                    "form_cabecera": form_cabecera,
                    "formset": formset,
                    "es_bodeguero": es_bodeguero,
                })

            # Registrar cambio de estado
            HistorialEstadoPedido.objects.create(
                pedido=pedido,
                estado_anterior="pendiente",
                estado_nuevo=pedido.estado,
                cambiado_por=request.user,
                fecha_cambio=timezone.now(),
            )
            pedido.calcular_totales()
            messages.success(request, f"¡Pedido #{pedido.id} registrado correctamente con {detalles_guardados} producto(s)!")
            return redirect("index")
        else:
            messages.error(request, "Hay errores en el formulario. Revisa los campos marcados.")

    else:
        form_cabecera = PedidoCabeceraForm()
        formset = DetalleFormSet(
            instance=Pedido(),
            form_kwargs={'user': request.user}
        )

    return render(request, "transaccional_pedido.html", {
        "form_cabecera": form_cabecera,
        "formset": formset,
        "es_bodeguero": es_bodeguero,
    })


@login_required
def reporte_ventas(request):
    resp = require_roles(request, ["jefe", "admin"], "index", "Solo jefes y administradores pueden acceder a los reportes.")
    if resp:
        return resp

    fechas_str = request.GET.get("fechas", "").strip()
    
    # Intentar parsear fechas explícitas
    fecha_inicio, fecha_fin, fechas_display, _, _ = parse_fecha_rango(fechas_str)
    if fechas_str and not fecha_inicio:
        messages.warning(request, "Rango de fechas inválido. Se muestra el mes actual.")
    
    # Si no hay fechas válidas, usar mes actual por defecto
    if not fecha_inicio or not fecha_fin:
        hoy = timezone.now().date()
        primer_dia = date(hoy.year, hoy.month, 1)
        ultimo_dia = date(hoy.year, hoy.month, monthrange(hoy.year, hoy.month)[1])
        fecha_inicio = timezone.make_aware(datetime.combine(primer_dia, datetime.min.time()))
        fecha_fin = timezone.make_aware(datetime.combine(ultimo_dia, datetime.max.time()))
        fechas_display = f"1 al {ultimo_dia.day} de {hoy.strftime('%B %Y')}"

    # Pedidos entregados en el rango
    pedidos = Pedido.objects.filter(
        estado="entregado",
        fecha__gte=fecha_inicio,
        fecha__lte=fecha_fin
    ).prefetch_related('detalles__balon')

    # Cálculos
    total_ventas = pedidos.aggregate(total=Sum('monto_total'))['total'] or 0
    total_ganancias = pedidos.aggregate(total=Sum('ganancia_total'))['total'] or 0
    total_pedidos = pedidos.count()
    promedio_pedido = total_ventas / total_pedidos if total_pedidos > 0 else 0

    # Top tipos de gas
    por_balon = DetallePedido.objects.filter(
        pedido__in=pedidos
    ).values(
        'balon__nombre'
    ).annotate(
        total_vendido=Sum(F('cantidad') * F('precio_venta_unitario')),
        kilos_vendidos=Sum(F('cantidad') * F('balon__peso_neto_gas'))
    ).order_by('-kilos_vendidos')[:5]

    tipos_gas_labels = [item['balon__nombre'] for item in por_balon]
    tipos_gas_data = [float(item['total_vendido']) for item in por_balon]

    # Top sectores
    sectores = pedidos.values('sector').annotate(
        total_vendido=Sum('monto_total')
    ).order_by('-total_vendido')[:5]

    sectores_labels = [item['sector'] or "Sin sector" for item in sectores]
    sectores_data = [float(item['total_vendido']) for item in sectores]

    context = {
        "por_balon": por_balon,
        "total_pedidos": total_pedidos,
        "total_ventas": total_ventas,
        "total_ganancias": total_ganancias,
        "promedio_pedido": promedio_pedido,
        "tipos_gas_labels": tipos_gas_labels,
        "tipos_gas_data": tipos_gas_data,
        "sectores_labels": sectores_labels,
        "sectores_data": sectores_data,
        "rango_actual": fechas_display,
    }

    return render(request, "reporte_ventas.html", context)



@login_required
def cliente_pedido(request):
    # El cliente final NO necesita login → lo dejamos sin protección
    return render(request, "cliente_pedido.html")

@login_required
def camionero_entregas(request):
    resp = require_roles(request, ["camionero", "admin"], "index", "Acceso restringido, únicamente camioneros pueden acceder.")
    if resp:
        return resp

    user = request.user

    # Pedidos que este camionero ya tomó y están en ruta
    pedidos_en_ruta = Pedido.objects.filter(
        estado="en_ruta",
        entregador=user
    ).order_by("fecha")

    # Pedidos entregados por este camionero (hoy o últimos días, opcional limitar)
    pedidos_entregados_hoy = Pedido.objects.filter(
        estado="entregado",
        entregador=user,
        fecha__date=timezone.now().date()
    ).order_by("-fecha")

    # Pedidos pendientes que aún no tienen camionero asignado
    # (solo origen telefono, porque tarreo/venta_extra se registran directamente)
    pendientes = Pedido.objects.filter(
        estado="pendiente",
        origen="telefono",
        entregador__isnull=True
    ).order_by("fecha")

    context = {
        "pedidos_en_ruta": pedidos_en_ruta,
        "pendientes": pendientes,
        "pedidos_entregados_hoy": pedidos_entregados_hoy,  # opcional, pero útil para ver lo del día
        "hay_en_ruta": pedidos_en_ruta.exists(),
        "hay_pendientes": pendientes.exists(),
        "hay_entregados_hoy": pedidos_entregados_hoy.exists(),
    }

    return render(request, "camionero_entregas.html", context)


@login_required
def camionero_tomar_pedido(request, pedido_id):
    resp = require_roles(request, ["camionero"], "index", "Solo camioneros pueden tomar pedidos.")
    if resp:
        return resp

    try:
        pedido = Pedido.objects.get(
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

        messages.success(request, f"Pedido #{pedido.id} tomado. Dirígete al domicilio")
    except Pedido.DoesNotExist:
        messages.error(request, "El pedido ya no está disponible o ya fue tomado.")

    return redirect("camionero_entregas")


@login_required
def camionero_marcar_entregado(request, pedido_id):
    """
    Marca un pedido como ENTREGADO.
    - Solo el camionero que lo tomó puede marcarlo.
    - Registra el cambio en el historial de estados.
    """
    resp = require_roles(request, ["camionero"], "index", "Solo los camioneros pueden marcar entregas como completadas.")
    if resp:
        return resp

    try:
        pedido = Pedido.objects.get(
            id=pedido_id,
            estado="en_ruta",
            entregador=request.user,
            origen="telefono"
        )
    except Pedido.DoesNotExist:
        messages.error(request, "El pedido no existe, ya no está en ruta, no te pertenece o ya fue procesado.")
        return redirect("camionero_entregas")

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

    messages.success(request, f"¡Pedido #{pedido.id} marcado como ENTREGADO exitosamente!")
    return redirect("camionero_entregas")
    
    
@login_required
def camionero_cancelar_entrega(request, pedido_id):
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
        pedido.entregador = None
        pedido.save()

        HistorialEstadoPedido.objects.create(
            pedido=pedido,
            estado_anterior=estado_anterior,
            estado_nuevo="cancelado",
            cambiado_por=request.user,
            fecha_cambio=timezone.now(),
        )
        messages.warning(request, f"Pedido #{pedido.id} ha sido cancelado.")
    except Pedido.DoesNotExist:
        messages.error(request, "El pedido no está en ruta o no te pertenece.")

    return redirect("camionero_entregas")