# mockups/views.py

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

import csv
from datetime import date

from .forms import PedidoForm
from .models import Pedido, TipoBalon, Usuario


def index(request):
    return render(request, "index.html")


# mockups/views.py → vista completa y definitiva


@login_required
def precios_balones(request):
    """
    Vista completa para gestión de precios de balones.
    Permite:
      • Actualizar precios y disponibilidad (jefe/admin)
      • Crear nuevos tipos de balón (solo superusuario o DEBUG)
      • Eliminar balones (solo superusuario o DEBUG - ideal para testing)
    """
    # === 1. PERMISOS: Solo jefe o admin ===
    if request.user.rol not in ["jefe", "admin"] and not request.user.is_superuser:
        messages.error(request, "No tienes permiso para modificar precios.")
        return redirect("index")

    # === 2. POST: Procesar diferentes acciones ===
    if request.method == "POST":
        accion = request.POST.get("accion")

        # ——— ACCIÓN: Crear nuevo balón (solo testing o superusuario) ———
        if accion == "crear_balon" and (request.user.is_superuser or settings.DEBUG):
            tamaño = request.POST.get("nuevo_tamaño", "").strip()
            precio_str = request.POST.get("nuevo_precio", "0")

            if not tamaño:
                messages.error(request, "El tamaño del balón es obligatorio.")
            else:
                try:
                    precio = int(precio_str.replace(".", ""))
                    if precio < 0:
                        raise ValueError
                    TipoBalon.objects.create(
                        tamaño=tamaño,
                        precio=precio,
                        activo=True,
                        actualizado_por=request.user,
                    )
                    messages.success(
                        request,
                        f"Balón {tamaño.upper()} creado con precio ${precio:,}".replace(
                            ",", "."
                        ),
                    )
                except ValueError:
                    messages.error(request, "El precio debe ser un número válido.")
                except Exception as e:
                    messages.error(request, "Error al crear el balón.")
            return redirect("precios_balones")

        # ——— ACCIÓN: Eliminar balón (solo testing o superusuario) ———
        elif accion == "eliminar_balon" and (
            request.user.is_superuser or settings.DEBUG
        ):
            balon_id = request.POST.get("balon_id")
            try:
                balon = TipoBalon.objects.get(id=balon_id)
                nombre = balon.tamaño
                balon.delete()
                messages.warning(
                    request, f"Balón {nombre.upper()} eliminado permanentemente."
                )
            except TipoBalon.DoesNotExist:
                messages.error(request, "El balón no existe.")
            except Exception:
                messages.error(request, "Error al eliminar el balón.")
            return redirect("precios_balones")

        # ——— ACCIÓN PRINCIPAL: Actualizar precios existentes ———
        else:
            balones = TipoBalon.objects.all()
            cambios_realizados = False

            for balon in balones:
                precio_key = f"precio_{balon.id}"
                activo_key = f"activo_{balon.id}"

                nuevo_precio_str = request.POST.get(precio_key, "").strip()
                activo = activo_key in request.POST

                if not nuevo_precio_str:
                    messages.error(
                        request,
                        f"El precio del balón {balon.tamaño} no puede estar vacío.",
                    )
                    continue

                # Limpiar formato chileno: 32.800 → 32800
                nuevo_precio_str = nuevo_precio_str.replace(".", "")

                try:
                    nuevo_precio = int(nuevo_precio_str)
                    if nuevo_precio < 0:
                        raise ValueError
                except ValueError:
                    messages.error(
                        request,
                        f"Precio inválido para {balon.tamaño}: {nuevo_precio_str}",
                    )
                    continue

                # Solo guardar si hubo cambios
                if balon.precio != nuevo_precio or balon.activo != activo:
                    balon.precio = nuevo_precio
                    balon.activo = activo
                    balon.actualizado_por = request.user
                    balon.save()  # ← aquí se crea el HistorialPrecioBalon automáticamente
                    cambios_realizados = True

            if cambios_realizados:
                messages.success(request, "Precios actualizados correctamente.")
            else:
                messages.info(request, "No se realizaron cambios.")

            return redirect("precios_balones")

    # === 3. GET: Mostrar formulario ===
    balones = TipoBalon.objects.all().order_by("tamaño")

    return render(
        request,
        "precios_balones.html",
        {
            "balones": balones,
        },
    )


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
                return redirect("camionero")
            elif user.rol in ["jefe", "admin"]:
                return redirect("reporte_ventas")
            elif user.rol == "telefonista":
                return redirect("transaccional_pedido")
            else:
                return redirect("mantenedor_clientes")
        else:
            messages.error(request, "Usuario o contraseña incorrectos")

    return render(request, "login.html")


def logout_view(request):
    logout(request)
    messages.success(request, "Has cerrado sesión correctamente")
    return redirect("login")


# ==================== VISTAS PROTEGIDAS ====================


@login_required
def mantenedor_clientes(request):
    if not request.user.is_authenticated:
        messages.error(request, "Debes iniciar sesión para acceder a esta sección.")
        return redirect("login")

    clientes = [
        {
            "id": 1,
            "nombre": "Juan Pérez",
            "telefono": "987654321",
            "direccion": "Av. Siempre Viva 123",
        },
        {
            "id": 2,
            "nombre": "María López",
            "telefono": "912345678",
            "direccion": "Calle Falsa 456",
        },
    ]
    return render(request, "mantenedor_clientes.html", {"clientes": clientes})


@login_required
def consultas_pedidos(request):
    """
    Consulta de pedidos con filtros y paginación para mejor usabilidad.
    """
    # Filtros
    cliente_query = request.GET.get("cliente", "").strip()
    fecha_desde = request.GET.get("fecha_desde")
    fecha_hasta = request.GET.get("fecha_hasta")
    estado = request.GET.get("estado", "")

    pedidos = Pedido.objects.select_related("cliente", "telefonista").all()

    if cliente_query:
        pedidos = pedidos.filter(
            Q(cliente__nombre__icontains=cliente_query)
            | Q(cliente__telefono__icontains=cliente_query)
        )

    if fecha_desde:
        pedidos = pedidos.filter(fecha__date__gte=fecha_desde)
    if fecha_hasta:
        pedidos = pedidos.filter(fecha__date__lte=fecha_hasta)
    if estado and estado != "todos":
        pedidos = pedidos.filter(estado=estado)

    pedidos = pedidos.order_by("-fecha")

    # Paginación: 20 pedidos por página (ajustable)
    paginator = Paginator(pedidos, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "pedidos": page_obj,  # Ahora es paginado
        "estados_choices": Pedido.ESTADOS,
        "filtros": {
            "cliente": cliente_query,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
            "estado": estado or "todos",
        },
        "page_obj": page_obj,  # Para el paginador en el template
    }

    return render(request, "consultas_pedidos.html", context)


# mockups/views.py → función completa
# mockups/views.py
@login_required
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

    return render(request, "transaccional_pedido.html", {
        "form": form,
        "es_bodeguero": request.user.rol == "bodeguero",
        "es_telefonista": request.user.rol == "telefonista",
    })


@login_required
def reporte_ventas(request):
    if request.user.rol not in ["jefe", "admin"]:
        messages.error(request, "Acceso denegado. Solo para jefes y administradores.")
        return redirect("index")

    # Filtros por fecha (por defecto: hoy)
    hoy = date.today()
    fecha_inicio = request.GET.get("fecha_inicio", hoy.strftime("%Y-%m-%d"))
    fecha_fin = request.GET.get("fecha_fin", hoy.strftime("%Y-%m-%d"))

    try:
        fecha_inicio = date.fromisoformat(fecha_inicio)
        fecha_fin = date.fromisoformat(fecha_fin)
    except ValueError:
        fecha_inicio = fecha_fin = hoy

    # Pedidos en el rango
    pedidos = Pedido.objects.filter(fecha__date__range=(fecha_inicio, fecha_fin))

    total_pedidos = pedidos.count()
    total_ventas = pedidos.aggregate(total=Sum("monto"))["total"] or 0

    # Por tipo de balón
    por_balon = (
        pedidos.values("balon")
        .annotate(cantidad_vendida=Sum("cantidad_balon"), total_monto=Sum("monto"))
        .order_by("-cantidad_vendida")
    )

    # Datos para gráfico (etiquetas y valores)
    labels = [item["balon"] for item in por_balon]
    data = [float(item["total_monto"]) for item in por_balon]

    # Últimos pedidos
    ultimos_pedidos = pedidos.order_by("-fecha")[:10]

    context = {
        "total_pedidos": total_pedidos,
        "total_ventas": total_ventas,
        "por_balon": por_balon,
        "ultimos_pedidos": ultimos_pedidos,
        "fecha_inicio": fecha_inicio.strftime("%Y-%m-%d"),
        "fecha_fin": fecha_fin.strftime("%Y-%m-%d"),
        "labels": labels,
        "data": data,
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
