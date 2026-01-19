# mockups/views.py

# ──────────────────────────────────────────────────────────────
# 1. IMPORTACIONES
# ──────────────────────────────────────────────────────────────
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
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse

# Librerías de terceros
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

# App local
from .forms import (
    DetallePedidoForm, 
    PedidoCabeceraForm, 
    DetalleFormSet, 
    LineaSobreFormSet
)
from .models import (
    Pedido, 
    TipoBalon, 
    Usuario, 
    DetallePedido, 
    HistorialEstadoPedido, 
    LineaSobre, 
    SobreDiario
)


# ──────────────────────────────────────────────────────────────
# 2. FUNCIONES AUXILIARES (Utils)
# ──────────────────────────────────────────────────────────────
def parse_fecha_rango(fechas_str):
    """
    Convierte un string de rango 'YYYY-MM-DD to YYYY-MM-DD' en objetos datetime aware.
    Retorna: (fecha_inicio, fecha_fin, display_str, desde_str, hasta_str)
    Si falla, retorna valores vacíos/None.
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
        
        # Convertir strings a objetos datetime naive
        desde = datetime.strptime(desde_str, "%Y-%m-%d")
        hasta = datetime.strptime(hasta_str, "%Y-%m-%d")
        
        # Hacerlos aware (zona horaria configurada en Django)
        fecha_inicio = timezone.make_aware(desde.replace(hour=0, minute=0, second=0, microsecond=0))
        fecha_fin = timezone.make_aware(hasta.replace(hour=23, minute=59, second=59, microsecond=999999))
        
        fecha_display = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
        return fecha_inicio, fecha_fin, fecha_display, desde_str, hasta_str
    except ValueError:
        return None, None, "", "", ""


def require_roles(request, roles, redirect_to="index", message="No tienes permiso para acceder a esta sección."):
    """
    Middleware a nivel de vista. 
    Redirige si el usuario no tiene uno de los roles especificados en la lista 'roles'.
    Retorna None si tiene permiso.
    """
    if request.user.rol not in roles:
        messages.error(request, message)
        return redirect(redirect_to)
    return None


def get_display_name(user):
    """Retorna el nombre completo del usuario o '—' si es nulo."""
    if not user:
        return "—"
    nombre = (user.get_full_name() or "").strip()
    return nombre if nombre else user.username


# ──────────────────────────────────────────────────────────────
# 3. AUTENTICACIÓN Y VISTAS GENERALES
# ──────────────────────────────────────────────────────────────
def index(request):
    """Página de inicio / Dashboard principal."""
    return render(request, "index.html")


def login_view(request):
    """Gestiona el inicio de sesión de usuarios."""
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        # Doble chequeo de autenticación por seguridad
        if request.user.is_authenticated:
            return redirect("index")
        
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            messages.success(request, f"¡Bienvenido, {user.get_full_name() or user.username}!")
            return redirect("index")
        else:
            messages.error(request, "Usuario o contraseña incorrectos")

    return render(request, "login.html")


def logout_view(request):
    """Cierra la sesión del usuario."""
    logout(request)
    messages.success(request, "Has cerrado sesión correctamente")
    return redirect("login")


# ──────────────────────────────────────────────────────────────
# 4. GESTIÓN ADMINISTRATIVA (Usuarios y Precios)
# ──────────────────────────────────────────────────────────────
@login_required
def crear_usuario(request):
    """
    Vista exclusiva para Admins.
    Crea nuevos usuarios en el sistema con roles específicos.
    """
    if not (request.user.rol == "admin" or request.user.is_superuser):
        messages.error(request, "No tienes permiso para crear usuarios.")
        return redirect("index")

    roles_choices = Usuario.ROLES

    if request.method == "POST":
        username = request.POST.get("username")
        first_name = request.POST.get("first_name", "")
        last_name = request.POST.get("last_name", "")
        telefono = request.POST.get("telefono", "")
        rol = request.POST.get("rol")
        password1 = request.POST.get("password1")
        password2 = request.POST.get("password2")

        # Validaciones
        if not all([username, rol, password1, password2]):
            messages.error(request, "Todos los campos obligatorios deben estar completos.")
        elif password1 != password2:
            messages.error(request, "Las contraseñas no coinciden.")
        elif len(password1) < 8:
            messages.error(request, "La contraseña debe tener al menos 8 caracteres.")
        elif Usuario.objects.filter(username=username).exists():
            messages.error(request, "Ya existe un usuario con ese nombre de usuario.")
        else:
            # Creación del usuario
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
            messages.success(request, f"Usuario '{user.get_full_name() or user.username}' creado correctamente con rol {user.get_rol_display()}.")
            return redirect("reporte_ventas")

    return render(request, "crear_usuario.html", {"roles_choices": roles_choices})


@login_required
def precios_balones(request):
    """
    Vista para Jefes y Admins.
    Permite actualizar masivamente precios de compra, venta y estado de balones.
    """
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para gestionar precios.")
    if resp:
        return resp

    balones = TipoBalon.objects.all().order_by("peso_neto_gas")

    if request.method == "POST":
        cambios_realizados = False

        for balon in balones:
            # Mapeo de campos del formulario
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

            # Verificar si hubo cambios reales antes de guardar
            if (balon.precio_compra != nuevo_compra or
                balon.precio_local != nuevo_local or
                balon.precio_domicilio != nuevo_domicilio or
                balon.activo != nuevo_activo):

                balon.precio_compra    = nuevo_compra
                balon.precio_local     = nuevo_local
                balon.precio_domicilio = nuevo_domicilio
                balon.activo           = nuevo_activo
                balon.actualizado_por   = request.user
                balon.save()  # Disparará señal automática de historial si existe

                cambios_realizados = True

        if cambios_realizados:
            messages.success(request, "Precios y disponibilidad actualizados correctamente.")
        else:
            messages.info(request, "No se detectaron cambios válidos.")

        return redirect("precios_balones")

    return render(request, "precios_balones.html", {"balones": balones})


# ──────────────────────────────────────────────────────────────
# 5. OPERACIONES TRANSACCIONALES (Registro de Ventas)
# ──────────────────────────────────────────────────────────────
@login_required
def transaccional_pedido(request):
    """
    Vista principal para Telefonistas y Bodegueros.
    Gestiona la creación de pedidos con sus detalles (inline formsets).
    - Telefonistas: Crea pedidos 'pendientes' con precio domicilio.
    - Bodegueros: Crea pedidos 'entregados' con precio local.
    """
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
            # Lógica de negocio según rol
            pedido.origen = "local" if es_bodeguero else "telefono"
            pedido.estado = "entregado" if es_bodeguero else "pendiente"
            pedido.fecha = timezone.now()
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
                return render(request, "transaccional_pedido.html", {
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
def cliente_pedido(request):
    """Vista pública para que el cliente simule un pedido (sin login requerido)."""
    # El login_required está en la url, pero aquí permitimos acceso anónimo si se configurara así
    return render(request, "cliente_pedido.html")


# ──────────────────────────────────────────────────────────────
# 6. GESTIÓN DE PEDIDOS POR ROL
# ──────────────────────────────────────────────────────────────

# --- Telefonista / Bodeguero ---
@login_required
def mis_pedidos_hoy(request):
    """
    Muestra el historial de ventas del día actual para el usuario logueado.
    Utiliza zona horaria de Chile para definir 'hoy'.
    """
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

    total_monto_hoy = pedidos_hoy.aggregate(total=Sum('monto_total'))['total'] or 0

    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_monto_hoy": total_monto_hoy,
        "es_telefonista": request.user.rol == "telefonista",
        "fecha_hoy": hoy,
        "total_pedidos": pedidos_hoy.count(),
    }
    return render(request, "mis_pedidos_hoy.html", context)


# --- Camionero ---
@login_required
def mis_entregas_camionero(request):
    """Historial simplificado de entregas realizadas por el camionero, limitado al día actual."""
    resp = require_roles(request, ["camionero"], "index", "Solo camioneros pueden ver sus entregas.")
    if resp:
        return resp
    
    # Zona horaria de Chile (America/Santiago)
    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()
    
    # Rango de "Hoy" (inicio y fin del día)
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), timezone=tz_chile)
    fin_dia = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), timezone=tz_chile)
    
    # Filtrar solo entregas del día actual, entregadas por el usuario logueado
    pedidos_hoy = Pedido.objects.filter(
        entregador=request.user,
        estado="entregado",
        fecha__gte=inicio_dia,  # ← Filtro clave: desde inicio del día
        fecha__lte=fin_dia      # ← Filtro clave: hasta fin del día
    ).order_by("-fecha").prefetch_related("detalles__balon")
    
    # Métricas diarias simples (opcional, para enriquecer la vista sin complejidad)
    total_entregas_hoy = pedidos_hoy.count()
    total_monto_hoy = pedidos_hoy.aggregate(total=Sum('monto_total'))['total'] or 0
    
    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_entregas_hoy": total_entregas_hoy,
        "total_monto_hoy": total_monto_hoy,
        "fecha_hoy": hoy,
    }
    return render(request, "mis_entregas_camionero.html", context)


@login_required
def camionero_entregas(request):
    """
    Panel de control del camionero.
    Muestra:
    - Pedidos pendientes de HOY (para tomar)
    - Pedidos en ruta (asignados al usuario, sin límite de fecha por ahora)
    - Pedidos entregados HOY
    """
    resp = require_roles(request, ["camionero", "admin"], "index", "Acceso restringido a camioneros.")
    if resp:
        return resp

    user = request.user

    # Zona horaria Chile
    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()

    # Rango preciso para "hoy"
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), tz_chile)
    fin_dia   = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), tz_chile)

    # 1. Pedidos en ruta (asignados al camionero) → sin límite de fecha (pueden ser de días anteriores)
    pedidos_en_ruta = Pedido.objects.filter(
        estado="en_ruta",
        entregador=user
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("fecha")

    # 2. Pedidos entregados HOY por este camionero
    pedidos_entregados_hoy = Pedido.objects.filter(
        estado="entregado",
        entregador=user,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("-fecha")

    # 3. Pedidos pendientes DISPONIBLES HOY (sin asignar, origen telefónico)
    # Solo mostramos los creados HOY para evitar que el camionero vea pedidos muy antiguos
    pendientes = Pedido.objects.filter(
        estado="pendiente",
        origen="telefono",
        entregador__isnull=True,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("-fecha")

    # Conteos para mejorar UX (badges, mensajes)
    context = {
        "pedidos_en_ruta": pedidos_en_ruta,
        "pendientes": pendientes,
        "pedidos_entregados_hoy": pedidos_entregados_hoy,

        "count_en_ruta": pedidos_en_ruta.count(),
        "count_pendientes": pendientes.count(),
        "count_entregados_hoy": pedidos_entregados_hoy.count(),

        "hay_en_ruta": pedidos_en_ruta.exists(),
        "hay_pendientes": pendientes.exists(),
        "hay_entregados_hoy": pedidos_entregados_hoy.exists(),

        # Para mostrar la fecha en la interfaz
        "hoy": hoy,
        "ahora": ahora,
    }

    return render(request, "camionero_entregas.html", context)

@login_required
def camionero_tomar_pedido(request, pedido_id):
    """Camionero toma posesión de un pedido pendiente y lo marca 'en_ruta'."""
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
    """Camionero marca un pedido 'en_ruta' como 'entregado'."""
    resp = require_roles(request, ["camionero"], "index", "Solo los camioneros pueden marcar entregas.")
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
        messages.error(request, "El pedido no existe, no está en ruta o no te pertenece.")
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
    """Camionero cancela una entrega que tenía en ruta."""
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
        pedido.entregador = None # Libera el camionero para que otro pueda tomarlo si fuera necesario (o quede cancelado)
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


# ──────────────────────────────────────────────────────────────
# 7. REPORTES Y CONSULTAS (Admin/Jefe)
# ──────────────────────────────────────────────────────────────
@login_required
def consultas_pedidos(request):
    """
    Vista avanzada con filtros múltiples para buscar pedidos.
    Incluye exportación a Excel y estadísticas rápidas.
    """
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
    queryset = Pedido.objects.select_related(
        "registrador", "entregador"
    ).prefetch_related(
        "detalles__balon"
    ).order_by("-fecha")

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

    if origen != "todos":
        queryset = queryset.filter(origen=origen)

    # Lógica de exportación
    if exportar == "excel":
        return exportar_pedidos_excel(queryset, fechas_display)

    # Paginación
    paginator = Paginator(queryset, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Estadísticas para el panel
    total_pedidos = queryset.count()
    total_ventas = queryset.aggregate(total=Sum('monto_total'))['total'] or 0
    total_ganancias = queryset.aggregate(total=Sum('ganancia_total'))['total'] or 0

    context = {
        'page_obj': page_obj,
        'estadisticas': {
            'total_pedidos': total_pedidos,
            'total_ventas': total_ventas,
            'total_ganancias': total_ganancias,
        },
        'estados_choices': Pedido.ESTADOS,
        'origenes_choices': Pedido.ORIGENES,
        'filtros': request.GET,
    }
    return render(request, "consultas_pedidos.html", context)


def exportar_pedidos_excel(queryset, rango_fechas):
    """Genera un archivo Excel .xlsx basado en el queryset filtrado."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pedidos"

    # Configuración de estilos
    header_fill = PatternFill(start_color="0066CC", end_color="0066CC", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=12)
    border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    # Títulos
    ws.merge_cells('A1:L1')
    ws['A1'] = f"Reporte de Pedidos - KIM GAS Rancagua"
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    
    if rango_fechas:
        ws.merge_cells('A2:L2')
        ws['A2'] = f"Período: {rango_fechas}"
        ws['A2'].alignment = Alignment(horizontal='center')

    # Cabeceras de tabla
    headers = ['ID', 'Fecha', 'Hora', 'Estado', 'Origen', 'Productos', 'Cantidad Total', 'Monto', 'Ganancia', 'Registrado por', 'Entregado por', 'Sector/Dirección']
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col_num)
        cell.value = header
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border

    # Llenado de datos
    row_num = 5
    for pedido in queryset:
        productos_list = [f"{d.cantidad}×{d.balon.nombre}" for d in pedido.detalles.all()]
        cantidad_total = sum(d.cantidad for d in pedido.detalles.all())
        productos_str = " + ".join(productos_list) if productos_list else "—"
        ubicacion = f"{pedido.sector or '—'} / {pedido.direccion_entrega or '—'}"

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
            get_display_name(pedido.registrador),
            get_display_name(pedido.entregador),
            ubicacion
        ]

        for col_num, value in enumerate(data, 1):
            cell = ws.cell(row=row_num, column=col_num)
            cell.value = value
            cell.border = border
            if col_num in [8, 9]:
                cell.number_format = '"$"#,##0'
            cell.alignment = Alignment(horizontal='center') if col_num in [1, 2, 3, 7, 8, 9] else Alignment(horizontal='left')
        row_num += 1

    # Totales finales
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

    # Ajuste de columnas
    column_widths = {'A': 8, 'B': 12, 'C': 10, 'D': 12, 'E': 18, 'F': 25, 'G': 12, 'H': 12, 'I': 12, 'J': 20, 'K': 20, 'L': 35}
    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f"pedidos_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@login_required
def reporte_ventas(request):
    """
    Dashboard de ventas para Jefes y Admins.
    Muestra métricas clave, top productos y top sectores en un rango de fechas.
    """
    resp = require_roles(request, ["jefe", "admin"], "index", "Solo jefes y administradores pueden acceder a los reportes.")
    if resp:
        return resp

    fechas_str = request.GET.get("fechas", "").strip()
    
    # Determinar rango de fechas
    fecha_inicio, fecha_fin, fechas_display, _, _ = parse_fecha_rango(fechas_str)
    if fechas_str and not fecha_inicio:
        messages.warning(request, "Rango de fechas inválido. Se muestra el mes actual.")
    
    if not fecha_inicio or not fecha_fin:
        hoy = timezone.now().date()
        primer_dia = date(hoy.year, hoy.month, 1)
        ultimo_dia = date(hoy.year, hoy.month, monthrange(hoy.year, hoy.month)[1])
        fecha_inicio = timezone.make_aware(datetime.combine(primer_dia, datetime.min.time()))
        fecha_fin = timezone.make_aware(datetime.combine(ultimo_dia, datetime.max.time()))
        fechas_display = f"1 al {ultimo_dia.day} de {hoy.strftime('%B %Y')}"

    # Datos base
    pedidos = Pedido.objects.filter(
        estado="entregado",
        fecha__gte=fecha_inicio,
        fecha__lte=fecha_fin
    ).prefetch_related('detalles__balon')

    # Métricas
    total_ventas = pedidos.aggregate(total=Sum('monto_total'))['total'] or 0
    total_ganancias = pedidos.aggregate(total=Sum('ganancia_total'))['total'] or 0
    total_pedidos = pedidos.count()
    promedio_pedido = total_ventas / total_pedidos if total_pedidos > 0 else 0

    # Datos para gráficas/tablas
    por_balon = DetallePedido.objects.filter(pedido__in=pedidos).values(
        'balon__nombre'
    ).annotate(
        total_vendido=Sum(F('cantidad') * F('precio_venta_unitario')),
        kilos_vendidos=Sum(F('cantidad') * F('balon__peso_neto_gas'))
    ).order_by('-kilos_vendidos')[:5]

    sectores = pedidos.values('sector').annotate(
        total_vendido=Sum('monto_total')
    ).order_by('-total_vendido')[:5]

    context = {
        "por_balon": por_balon,
        "total_pedidos": total_pedidos,
        "total_ventas": total_ventas,
        "total_ganancias": total_ganancias,
        "promedio_pedido": promedio_pedido,
        "tipos_gas_labels": [item['balon__nombre'] for item in por_balon],
        "tipos_gas_data": [float(item['total_vendido']) for item in por_balon],
        "sectores_labels": [item['sector'] or "Sin sector" for item in sectores],
        "sectores_data": [float(item['total_vendido']) for item in sectores],
        "rango_actual": fechas_display,
    }

    return render(request, "reporte_ventas.html", context)


@login_required
def detalle_pedido(request, pedido_id):
    """Vista de detalle individual de un pedido."""
    resp = require_roles(request, ["jefe", "admin", "telefonista", "bodeguero", "camionero"], "index", "No tienes permiso para ver este pedido.")
    if resp:
        return resp

    try:
        pedido = Pedido.objects.select_related('registrador', 'entregador').prefetch_related('detalles__balon').get(id=pedido_id)

        # Validación de propiedad: si no es jefe/admin, verificar relación
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


# ──────────────────────────────────────────────────────────────
# 8. GESTIÓN DE SOBRES DIARIOS (Cierre de Caja)
# ──────────────────────────────────────────────────────────────
@login_required
def lista_sobres_diarios(request):
    """
    Listado para seleccionar qué sobre abrir/editar (Bodega o Camionero).
    """
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        messages.error(request, "Acceso no permitido.")
        return redirect('index')

    hoy = timezone.now().date()
    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    return render(request, 'lista_sobres.html', {
        'hoy': hoy,
        'camioneros': camioneros,
    })


@login_required
def editar_sobre_diario(request, sobre_id=None):
    """
    Vista para editar o crear el sobre de un día específico.
    Calcula automáticamente las cantidades vendidas contra las declaradas.
    """
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        messages.error(request, "Acceso no permitido.")
        return redirect('lista_sobres_diarios')

    # 1. Determinar fecha correcta (Zona horaria Chile)
    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), timezone=tz_chile)
    fin_dia = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), timezone=tz_chile)

    # 2. Obtener o crear el objeto Sobre
    if sobre_id:
        sobre = get_object_or_404(SobreDiario, id=sobre_id)
    else:
        trabajador_id = request.GET.get('camionero')
        es_bodega = request.GET.get('bodega') == '1'
        trabajador = None
        tipo_sobre = 'bodega' if es_bodega else 'camion'
        
        if not es_bodega and trabajador_id:
            trabajador = get_object_or_404(Usuario, id=trabajador_id, rol='camionero')
            
        sobre, creado = SobreDiario.objects.get_or_create(
            fecha=hoy,
            trabajador=trabajador,
            tipo=tipo_sobre,
            defaults={'creado_por': request.user}
        )

    # 3. Recalcular líneas (Poblamiento robusto)
    balones_activos = TipoBalon.objects.filter(activo=True).order_by('peso_neto_gas')

    # Definir queryset de pedidos según tipo de sobre
    qs_pedidos = Pedido.objects.filter(fecha__gte=inicio_dia, fecha__lte=fin_dia, estado='entregado')
    if sobre.tipo == 'bodega':
        qs_pedidos = qs_pedidos.filter(origen='local')
    else:
        qs_pedidos = qs_pedidos.filter(entregador=sobre.trabajador)

    # Obtener resumen de ventas por balón
    resumen_dict = dict(
        qs_pedidos.values('detalles__balon')
          .annotate(total=Sum('detalles__cantidad'))
          .values_list('detalles__balon', 'total')
    )

    # Crear o actualizar líneas para cada balón activo
    for balon in balones_activos:
        qty_calc = resumen_dict.get(balon.id, 0) or 0

        # ✅ CORRECCIÓN: Solo establecer cantidad_declarada en CREACIÓN
        linea, creada = LineaSobre.objects.get_or_create(
            sobre=sobre,
            balon=balon,
            defaults={
                'cantidad_calculada': qty_calc,
                'cantidad_declarada': qty_calc,  # Solo al crear
                'precio_venta_unitario': balon.precio_local if sobre.tipo == 'bodega' else balon.precio_domicilio
            }
        )
        
        # Si la línea ya existía, solo actualizar cantidad_calculada y precio
        if not creada:
            linea.cantidad_calculada = qty_calc
            linea.precio_venta_unitario = balon.precio_local if sobre.tipo == 'bodega' else balon.precio_domicilio
            linea.save(update_fields=['cantidad_calculada', 'precio_venta_unitario'])

    # 4. Manejo del Formset
    formset = LineaSobreFormSet(request.POST or None, instance=sobre)

    if request.method == "POST":
        if formset.is_valid():
            formset.save()
            sobre.creado_por = request.user
            sobre.save()
            messages.success(request, f"Sobre guardado correctamente ({sobre}).")
            return redirect('lista_sobres_diarios')
        else:
            messages.error(request, "Revisa los datos ingresados.")

    return render(request, 'sobres.html', {
        'sobre': sobre,
        'formset': formset,
        'hoy': hoy,
    })