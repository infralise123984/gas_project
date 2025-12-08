# mockups/views.py

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum, F
from django.shortcuts import redirect, render
from django.utils import timezone
from datetime import datetime, date

from .forms import PedidoForm
from .models import Pedido, TipoBalon, Usuario


def index(request):
    return render(request, "index.html")


@login_required
def precios_balones(request):
    if request.user.rol not in ["jefe", "admin"]:
        messages.error(request, "No tienes permiso para modificar precios.")
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
            messages.success(request, "Cambios guardados correctamente. Solo se actualizaron los valores modificados.")
        else:
            messages.info(request, "No se detectaron cambios.")

        return redirect("precios_balones")

    return render(request, "precios_balones.html", {"balones": balones})


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
    if request.user.rol not in ["jefe", "admin"]:
        messages.error(request, "No tienes permiso para consultar pedidos.")
        return redirect("index")

    # === FILTROS ===
    busqueda = request.GET.get("busqueda", "").strip()
    fecha_desde_str = request.GET.get("fecha_desde")
    fecha_hasta_str = request.GET.get("fecha_hasta")
    estado = request.GET.get("estado", "todos")

    pedidos = Pedido.objects.select_related("balon", "registrador").all()

    if busqueda:
        pedidos = pedidos.filter(
            Q(sector__icontains=busqueda) | Q(direccion_entrega__icontains=busqueda)
        )
    if fecha_desde_str:
        pedidos = pedidos.filter(fecha__date__gte=fecha_desde_str)
    if fecha_hasta_str:
        pedidos = pedidos.filter(fecha__date__lte=fecha_hasta_str)
    if estado != "todos":
        pedidos = pedidos.filter(estado=estado)

    pedidos = pedidos.order_by("-fecha")

    paginator = Paginator(pedidos, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "pedidos": page_obj,  # ← Esto es para el loop en la tabla
        "page_obj": page_obj,  # Necesario para la paginación personalizada
        "estados_choices": Pedido.ESTADOS,
        "filtros": {
            "busqueda": busqueda,
            "fecha_desde": fecha_desde_str,
            "fecha_hasta": fecha_hasta_str,
            "estado": estado,
        },
    }

    return render(request, "consultas_pedidos.html", context)


@login_required
def transaccional_pedido(request):
    if request.user.rol not in ["telefonista", "bodeguero"]:
        messages.error(request, "No tienes permiso para registrar pedidos.")
        return redirect("index")

    if request.method == "POST":
        form = PedidoForm(request.POST, user=request.user)
        if form.is_valid():
            pedido = form.save(commit=False)
            pedido.registrador = request.user
            pedido.origen = "local" if request.user.rol == "bodeguero" else "telefono"

            # Bodeguero: venta local
            if pedido.origen == "local":
                pedido.estado = "entregado"
                pedido.sector = ""
                pedido.direccion_entrega = "Venta en local"

            pedido.monto = form.cleaned_data["monto"]
            pedido.save()

            messages.success(request, f"Pedido #{pedido.id} registrado correctamente.")
            return redirect("transaccional_pedido")
    else:
        form = PedidoForm(user=request.user)

    return render(
        request,
        "transaccional_pedido.html",
        {
            "form": form,
            "es_bodeguero": request.user.rol == "bodeguero",
            "es_telefonista": request.user.rol == "telefonista",
        },
    )


@login_required
def reporte_ventas(request):
    if request.user.rol not in ["jefe", "admin"]:
        return redirect("index")

    fecha_inicio = request.GET.get("fecha_inicio")
    fecha_fin = request.GET.get("fecha_fin")

    pedidos = Pedido.objects.filter(estado="entregado")
    if fecha_inicio:
        pedidos = pedidos.filter(fecha__date__gte=fecha_inicio)
    if fecha_fin:
        pedidos = pedidos.filter(fecha__date__lte=fecha_fin)

    # Agregación por balón con kilos
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
        pedidos.aggregate(total=Sum(F("cantidad_balon") * F("balon__peso_neto_gas")))[
            "total"
        ]
        or 0
    )

    ultimos_pedidos = pedidos.select_related("balon", "registrador")[:10]

    context = {
        "por_balon": por_balon,
        "total_pedidos": total_pedidos,
        "total_ventas": total_ventas,
        "total_balones": total_balones,
        "total_kilos": total_kilos,
        "ultimos_pedidos": ultimos_pedidos,
        "fecha_inicio": fecha_inicio,
        "fecha_fin": fecha_fin,
    }
    return render(request, "reporte_ventas.html", context)


@login_required
def cliente_pedido(request):
    # El cliente final NO necesita login → lo dejamos sin protección
    return render(request, "cliente_pedido.html")


@login_required
def camionero_entregas(request):
    if not request.user.is_authenticated:
        messages.error(request, "Debes iniciar sesión con tu cuenta de camionero.")
        return redirect("login")

    # Datos simulados (pronto serán reales)
    entrega_activa = {
        "id": 1042,
        "cliente": "Juan Pérez",
        "direccion": "Av. Los Pinos 123, San Miguel",
        "balon": "10 kg",
        "metodo_pago": "Efectivo",
        "monto": "45.00",
        "distancia": "800 m",
        "tiempo_estimado": "3 min",
        "estado": "En ruta",
    }

    return render(
        request,
        "camionero_entregas.html",
        {"entrega_activa": entrega_activa, "camion_id": 7},
    )