# mockups/views.py

# Django core y utilidades
from datetime import date, datetime
from calendar import monthrange
from django.utils import timezone
from zoneinfo import ZoneInfo
from datetime import timedelta
# Django contrib
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

# Modelos, consultas y paginación
from django.db.models import Count, F, Q, Sum
from django.core.paginator import Paginator
from django.shortcuts import redirect, render

# App local
from .forms import PedidoForm
from .models import Pedido, TipoBalon, Usuario
def index(request):
    return render(request, "index.html")


@login_required
def precios_balones(request):
    if request.user.rol not in ["jefe", "admin"]:
        messages.error(request, "Solo telefonista y/o camionero pueden generar pedidos.")
        return redirect("index")

    balones = TipoBalon.objects.all().order_by("peso_neto_gas")

    if request.method == "POST":
        cambios_realizados = False

        for balon in balones:
            precio_key = f"precio_{balon.id}"
            activo_key = f"activo_{balon.id}"

            nuevo_precio_str = request.POST.get(precio_key)
            nuevo_activo = activo_key in request.POST  # checkbox marcado → True

            # Solo procesamos si el campo precio fue enviado y es válido
            if nuevo_precio_str is not None:
                try:
                    nuevo_precio = int(nuevo_precio_str)
                    if nuevo_precio < 0:
                        raise ValueError
                except ValueError:
                    messages.error(request, f"Precio inválido para {balon.nombre}")
                    continue

                # Detectamos cambio real
                if balon.precio != nuevo_precio or balon.activo != nuevo_activo:
                    balon.precio = nuevo_precio
                    balon.activo = nuevo_activo
                    balon.actualizado_por = request.user
                    balon.save()  # El método save() del modelo crea el historial automáticamente
                    cambios_realizados = True

        if cambios_realizados:
            messages.success(
                request,
                "Cambios guardados correctamente. Solo se actualizaron los valores modificados.",
            )
        else:
            messages.info(request, "No se detectaron cambios.")

        return redirect("precios_balones")

    return render(request, "precios_balones.html", {"balones": balones})


@login_required
def crear_usuario(request):
    if request.user.rol not in ["admin"]:
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
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            messages.success(
                request, f"¡Bienvenido, {user.get_full_name() or user.username}!"
            )

            # Redirección según rol
            if user.rol == "camionero":
                return redirect("camionero_entregas")
            elif user.rol in ["jefe", "admin"]:
                return redirect("reporte_ventas")
            else:  # Para telefonista y bodeguero
                return redirect("transaccional_pedido")
        else:
            messages.error(request, "Usuario o contraseña incorrectos")

    return render(request, "login.html")


def logout_view(request):
    logout(request)
    messages.success(request, "Has cerrado sesión correctamente")
    return redirect("login")




@login_required
def consultas_pedidos(request):
    # ── Captura de parámetros GET ───────────────────────────────────────────────
    busqueda    = request.GET.get("busqueda", "").strip()
    fechas_str  = request.GET.get("fechas", "").strip()
    estado      = request.GET.get("estado", "todos")

    # Variables para mantener valores en el formulario
    fechas_display = ""
    fecha_desde_str = ""
    fecha_hasta_str = ""

    # ── Determinar qué puede ver el usuario ─────────────────────────────────────
    es_jefe_o_admin = request.user.rol in ["jefe", "admin"]

    if not es_jefe_o_admin:
        # Roles operativos: solo sus pedidos del día actual en Chile
        chile_tz = ZoneInfo('America/Santiago')
        fechas_str = ""
        estado = "todos"

        # Alternativa más explícita (Opción 2: usando rango) → descomenta si prefieres esta
        inicio_hoy = timezone.now().astimezone(chile_tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        fin_hoy = inicio_hoy + timedelta(days=1) - timedelta(microseconds=1)
        queryset = Pedido.objects.filter(
            registrador=request.user,
            fecha__range=(inicio_hoy, fin_hoy)
        ).select_related("balon", "registrador").order_by("-fecha")

    else:
        # Jefe y admin: todo el historial
        queryset = Pedido.objects.select_related("balon", "registrador").order_by("-fecha")

    # ── Procesar rango de fechas (solo aplica si es jefe/admin y se envió) ──────
    if es_jefe_o_admin and fechas_str:
        fechas_str_clean = fechas_str.replace("+", " ").strip()
        if " a " in fechas_str_clean or " al " in fechas_str_clean:
            try:
                # Flatpickr puede enviar con " a " o " al " dependiendo de configuración
                sep = " a " if " a " in fechas_str_clean else " al "
                desde_str, hasta_str = [x.strip() for x in fechas_str_clean.split(sep, 1)]
                
                desde = datetime.strptime(desde_str, "%Y-%m-%d")
                hasta = datetime.strptime(hasta_str, "%Y-%m-%d")
                
                desde_aware = timezone.make_aware(desde.replace(hour=0, minute=0, second=0, microsecond=0))
                hasta_fin = timezone.make_aware(hasta.replace(hour=23, minute=59, second=59, microsecond=999999))
                
                queryset = queryset.filter(fecha__range=(desde_aware, hasta_fin))
                
                fecha_desde_str = desde.strftime("%Y-%m-%d")
                fecha_hasta_str = hasta.strftime("%Y-%m-%d")
                fechas_display = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
            except ValueError:
                messages.warning(request, "Formato de fechas inválido. Usa el selector de fechas.")
                fechas_str = ""

    # ── Filtro por texto (sector, dirección o nombre del registrador) ───────────
    if busqueda:
        queryset = queryset.filter(
            Q(sector__icontains=busqueda) |
            Q(direccion_entrega__icontains=busqueda) |
            Q(registrador__first_name__icontains=busqueda) |
            Q(registrador__last_name__icontains=busqueda) |
            Q(registrador__username__icontains=busqueda)
        )

    # ── Filtro por estado (solo jefe/admin) ─────────────────────────────────────
    if es_jefe_o_admin and estado != "todos":
        queryset = queryset.filter(estado=estado)

    # ── Paginación ──────────────────────────────────────────────────────────────
    paginator = Paginator(queryset, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # ── Contexto para la plantilla ──────────────────────────────────────────────
    context = {
        "page_obj": page_obj,
        "estados_choices": Pedido.ESTADOS,
        "es_jefe_o_admin": es_jefe_o_admin,
        "hoy": timezone.now().astimezone(ZoneInfo('America/Santiago')).date(),  # actualizado para mostrar la fecha correcta
        "filtros": {
            "busqueda": busqueda,
            "fechas": fechas_display,
            "estado": estado,
            "fecha_desde": fecha_desde_str,
            "fecha_hasta": fecha_hasta_str,
        },
    }

    return render(request, "consultas_pedidos.html", context)



@login_required
def transaccional_pedido(request):
    user = request.user
    rol = user.rol

    # ────────────────────────────────────────────────────────────────
    # Permisos básicos
    # ────────────────────────────────────────────────────────────────
    if rol not in ["telefonista", "bodeguero", "camionero", "admin"]:
        messages.error(request, "No tienes permiso para registrar pedidos.")
        return redirect("index")

    if request.method == "POST":
        form = PedidoForm(request.POST, user=user)

        if form.is_valid():
            pedido = form.save(commit=False)
            pedido.registrador = user

            # Determinar origen según rol (con flexibilidad para admin y camionero)
            if rol == "bodeguero":
                pedido.origen = "local"
            elif rol == "telefonista":
                pedido.origen = "telefono"
            elif rol == "camionero":
                # Camionero puede elegir tarreo o venta_extra desde el formulario
                pedido.origen = form.cleaned_data.get("origen", "tarreo")  # fallback por seguridad
            else:  # admin
                pedido.origen = form.cleaned_data.get("origen", "telefono")

            # Lógica específica según origen
            if pedido.origen == "local":
                pedido.estado = "entregado"
                pedido.sector = ""
                pedido.direccion_entrega = "Venta en local"
                # Opcional: pedido.entregador = user  # si quieres que bodeguero sea entregador

            elif pedido.origen in ["tarreo", "venta_extra"]:
                # Camionero o admin → asignamos automáticamente
                pedido.entregador = user
                pedido.estado = "entregado"  # se considera entregado en el momento
                # sector y direccion_entrega pueden quedar vacíos (validado en model.clean())

            elif pedido.origen == "telefono":
                pedido.estado = "pendiente"
                # entregador se asignará después por camionero

            # Monto ya viene calculado del form (cleaned_data)
            pedido.monto = form.cleaned_data["monto"]

            try:
                pedido.save()  # Aquí se ejecuta full_clean() y clean() del modelo
                messages.success(request, f"{pedido.get_origen_display()} #{pedido.id} registrado correctamente.")
                return redirect("transaccional_pedido")
            except Exception as e:
                messages.error(request, f"Error al guardar: {str(e)}")

    else:
        form = PedidoForm(user=user)

    # ────────────────────────────────────────────────────────────────
    # Contexto para la plantilla
    # ────────────────────────────────────────────────────────────────
    context = {
        "form": form,
        "es_bodeguero": rol == "bodeguero",
        "es_telefonista": rol == "telefonista",
        "es_camionero": rol == "camionero",
        "es_admin": rol == "admin",
        "titulo": {
            "bodeguero": "Registrar Venta en Local",
            "telefonista": "Registrar Pedido a Domicilio",
            "camionero": "Registrar Venta por Tarreo o Extra",
            "admin": "Registrar Pedido (Admin)",
        }.get(rol, "Registrar Pedido"),
    }

    return render(request, "transaccional_pedido.html", context)

@login_required
def reporte_ventas(request):
    if request.user.rol not in ["jefe", "admin"]:
        messages.error(request, "Solo jefes y administradores pueden acceder a los reportes.")
        return redirect("index")

    fechas_str = request.GET.get("fechas", "").strip()

    fecha_inicio = None
    fecha_fin = None
    fechas_display = ""

    # Caso 1: Hay filtro explícito del usuario
    if fechas_str:
        fechas_clean = fechas_str.replace("+", " ").strip()
        if " a " in fechas_clean:
            try:
                desde_str, hasta_str = fechas_clean.split(" a ", 1)
                desde_str = desde_str.strip()
                hasta_str = hasta_str.strip()

                desde = datetime.strptime(desde_str, "%Y-%m-%d")
                hasta = datetime.strptime(hasta_str, "%Y-%m-%d")

                fecha_inicio = timezone.make_aware(desde.replace(hour=0, minute=0, second=0, microsecond=0))
                hasta_fin = hasta.replace(hour=23, minute=59, second=59, microsecond=999999)
                fecha_fin = timezone.make_aware(hasta_fin)

                fechas_display = f"{desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
            except ValueError:
                messages.warning(request, "Rango de fechas inválido. Se muestra el mes actual por defecto.")
                fechas_str = ""  # fallback al mes actual

    # Caso 2: No hay filtro → usar mes actual por defecto
    if not fecha_inicio or not fecha_fin:
        hoy = timezone.now().date()
        primer_dia = date(hoy.year, hoy.month, 1)
        ultimo_dia = date(hoy.year, hoy.month, monthrange(hoy.year, hoy.month)[1])

        fecha_inicio = timezone.make_aware(datetime.combine(primer_dia, datetime.min.time()))
        fecha_fin = timezone.make_aware(datetime.combine(ultimo_dia, datetime.max.time()))

        fechas_display = f"1 al {ultimo_dia.day} de {hoy.strftime('%B %Y')}"  # ej: "1 al 31 de enero 2026"

    # Consulta base
    pedidos = Pedido.objects.filter(
        estado="entregado",
        fecha__gte=fecha_inicio,
        fecha__lte=fecha_fin
    ).select_related("balon", "registrador")

    # Agregaciones
    por_balon = (
        pedidos.values("balon__nombre", "balon__peso_neto_gas")
        .annotate(
            cantidad_vendida=Sum("cantidad_balon"),
            kilos_vendidos=Sum(F("cantidad_balon") * F("balon__peso_neto_gas")),
            total_monto=Sum("monto"),
        )
        .order_by("-kilos_vendidos")
    )

    total_pedidos = pedidos.count()
    total_ventas = pedidos.aggregate(total=Sum("monto"))["total"] or 0
    total_balones = pedidos.aggregate(total=Sum("cantidad_balon"))["total"] or 0
    total_kilos = (
        pedidos.aggregate(total=Sum(F("cantidad_balon") * F("balon__peso_neto_gas")))["total"]
        or 0
    )

    ultimos_pedidos = pedidos.order_by("-fecha")[:10]

    context = {
        "por_balon": por_balon,
        "total_pedidos": total_pedidos,
        "total_ventas": total_ventas,
        "total_balones": total_balones,
        "total_kilos": total_kilos,
        "ultimos_pedidos": ultimos_pedidos,
        "filtros": {
            "fechas": fechas_display,
            "fecha_inicio": fecha_inicio.date(),
            "fecha_fin": fecha_fin.date(),
        },
    }

    return render(request, "reporte_ventas.html", context)
@login_required
def cliente_pedido(request):
    # El cliente final NO necesita login → lo dejamos sin protección
    return render(request, "cliente_pedido.html")

@login_required
def camionero_entregas(request):
    if request.user.rol not in ["camionero", "admin"]:
        messages.error(request, "Acceso restringido, únicamente camioneros pueden acceder.")
        return redirect("index")

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
    if request.user.rol != "camionero":
        messages.error(request, "Solo camioneros pueden tomar pedidos.")
        return redirect("index")

    try:
        pedido = Pedido.objects.get(
            id=pedido_id,
            estado="pendiente",
            entregador__isnull=True,           # ← seguridad extra
            origen="telefono"                  # solo permite tomar pedidos telefónicos
        )
        pedido.estado = "en_ruta"
        pedido.entregador = request.user       # ← asignamos al camionero que lo toma
        pedido.save()

        messages.success(
            request,
            f"Pedido #{pedido.id} tomado. Dirígete al domicilio"
        )
    except Pedido.DoesNotExist:
        messages.error(request, "El pedido ya no está disponible o ya fue tomado.")

    return redirect("camionero_entregas")


@login_required
def camionero_marcar_entregado(request, pedido_id):
    if request.user.rol != "camionero":
        messages.error(request, "Solo camioneros pueden marcar entregas.")
        return redirect("index")

    try:
        pedido = Pedido.objects.get(
            id=pedido_id,
            estado="en_ruta",
            entregador=request.user            # ← solo puede marcar los suyos
        )
        pedido.estado = "entregado"
        # entregador ya está asignado desde que lo tomó, no es necesario volver a ponerlo
        pedido.save()

        messages.success(request, f"Pedido #{pedido.id} marcado como entregado!")
    except Pedido.DoesNotExist:
        messages.error(request, "El pedido no está en ruta, no existe o no te pertenece.")

    return redirect("camionero_entregas")