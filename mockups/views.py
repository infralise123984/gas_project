# mockups/views.py

# ──────────────────────────────────────────────────────────────
# 1. IMPORTACIONES
# ──────────────────────────────────────────────────────────────
# Django core y utilidades
from datetime import date, datetime, time, timedelta
from json import dumps
from calendar import monthrange
from django.utils import timezone
import uuid
from django.views.decorators.cache import never_cache
from zoneinfo import ZoneInfo
from django.urls import reverse

# Django contrib
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction

# Modelos, consultas y paginación
from django.db.models import Count, F, Q, Sum, Case, When, Value, IntegerField
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse, JsonResponse

# Seguridad adicional
from django.core.cache import cache
from django.utils.http import url_has_allowed_host_and_scheme

# Librerías de terceros
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side 
from openpyxl.utils import get_column_letter   # ← AGREGAR ESTA LÍNEA
import pyotp
import segno

# App local
from .forms import (
    CrearUsuarioSeguroForm,
    EditarPerfilForm,
    CambiarPasswordForm,
    Verificar2FAForm,
    Activar2FAConfirmForm,
    Desactivar2FAForm,
    DetallePedidoForm, 
    PedidoCabeceraForm, 
    DetalleFormSet, 
    LineaSobreFormSet,
    DetalleFormSetEdit,
    LineaPagoFormSet,
    LineaGastoFormSet,
    TipoBalonForm,
    SectorForm,
)
from .models import (
    Pedido, 
    Sector,
    TipoBalon, 
    Usuario, 
    DetallePedido, 
    HistorialEstadoPedido, 
    LineaSobre, 
    SobreDiario,
    HistorialCambioPedido,
    HistorialPrecioBalon,
    AuditoriaAccion
)

# Logging de seguridad y auditoría
import logging
security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')


# ══════════════════════════════════════════════════════════════
# 2. FUNCIONES AUXILIARES Y UTILIDADES
# ══════════════════════════════════════════════════════════════

# Zona horaria: convierte hora actual a Chile (America/Santiago)
def now_chile():
    """Retorna hora actual en zona horaria de Chile con ajuste automático de horario."""
    tz_chile = ZoneInfo('America/Santiago')
    return timezone.now().astimezone(tz_chile)


# Zona horaria: obtiene fecha del día actual en Chile
def today_chile():
    """Retorna fecha actual en Chile (evita problemas de UTC)."""
    return now_chile().date()

def get_balones_activos_ordenados():
    """Retorna los balones activos en el mismo orden usado por los sobres."""
    return TipoBalon.objects.filter(activo=True).annotate(
        tipo_orden=Case(
            When(tipo_gas='normal', then=Value(0)),
            When(tipo_gas='catalitico', then=Value(1)),
            When(tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by('tipo_orden', '-peso_neto_gas')

def get_pedidos_queryset_para_sobre(sobre):
    """Construye el queryset de pedidos que alimenta el sobre."""
    fecha_para_calcular = sobre.fecha_correspondiente or sobre.fecha.astimezone(ZoneInfo('America/Santiago')).date()
    tz_chile = ZoneInfo('America/Santiago')
    tz_utc = ZoneInfo('UTC')

    inicio_dia = datetime.combine(fecha_para_calcular, time.min)
    inicio_dia = timezone.make_aware(inicio_dia, tz_chile).astimezone(tz_utc)
    fin_dia = datetime.combine(fecha_para_calcular, time.max)
    fin_dia = timezone.make_aware(fin_dia, tz_chile).astimezone(tz_utc)

    if sobre.tipo == 'bodega':
        return Pedido.objects.filter(
            fecha__gte=inicio_dia,
            fecha__lte=fin_dia,
            origen='local',
            estado='entregado'
        )

    return Pedido.objects.filter(
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia,
        estado='entregado',
        entregador=sobre.trabajador
    )

def sincronizar_sobre_desde_pedidos(sobre, crear_lineas_faltantes=True):
    """
    Refresca cantidades calculadas del sobre abierto usando los pedidos vigentes.
    Si la cantidad declarada seguía igual a la calculada anterior, también la actualiza.
    """
    if not sobre.pk or sobre.cerrado:
        return False

    balones_activos = list(get_balones_activos_ordenados())
    pedidos_qs = get_pedidos_queryset_para_sobre(sobre)
    cantidades_por_balon = {
        item['detalles__balon']: int(item['total'] or 0)
        for item in pedidos_qs.values('detalles__balon').annotate(total=Sum('detalles__cantidad'))
        if item['detalles__balon']
    }

    cambios = False
    lineas_existentes = {
        linea.balon_id: linea
        for linea in sobre.lineas.select_related('balon')
    }

    with transaction.atomic():
        for balon in balones_activos:
            qty_calc = cantidades_por_balon.get(balon.id, 0)
            linea = lineas_existentes.get(balon.id)

            if not linea:
                if not crear_lineas_faltantes:
                    continue
                precio = balon.precio_local if sobre.tipo == 'bodega' else balon.precio_domicilio
                LineaSobre.objects.create(
                    sobre=sobre,
                    balon=balon,
                    cantidad_calculada=qty_calc,
                    cantidad_declarada=qty_calc,
                    precio_venta_unitario=precio,
                )
                cambios = True
                continue

            old_calc = int(linea.cantidad_calculada or 0)
            update_fields = []

            if old_calc != qty_calc:
                linea.cantidad_calculada = qty_calc
                update_fields.append('cantidad_calculada')

                if int(linea.cantidad_declarada or 0) == old_calc:
                    linea.cantidad_declarada = qty_calc
                    update_fields.append('cantidad_declarada')

            if update_fields:
                linea.save(update_fields=update_fields)
                cambios = True

        sobre.refresh_from_db()
        monto_calculado_app = pedidos_qs.aggregate(total=Sum('monto_total'))['total'] or 0
        monto_declarado = sum(
            (linea.cantidad_declarada or 0) * (linea.precio_venta_unitario or 0)
            for linea in sobre.lineas.all()
        )
        diferencia = monto_declarado - monto_calculado_app

        sobre_update_fields = []
        if sobre.monto_calculado_app != monto_calculado_app:
            sobre.monto_calculado_app = monto_calculado_app
            sobre_update_fields.append('monto_calculado_app')
        if sobre.monto_declarado != monto_declarado:
            sobre.monto_declarado = monto_declarado
            sobre_update_fields.append('monto_declarado')
        if sobre.diferencia != diferencia:
            sobre.diferencia = diferencia
            sobre_update_fields.append('diferencia')

        if sobre_update_fields:
            sobre.save(update_fields=sobre_update_fields)
            cambios = True

    return cambios

def parse_fecha_rango(fechas_str):
    """
    Convierte un string de fecha o rango en objetos date simples.
    Soporta múltiples formatos de separador:
      - "2026-02-01 to 2026-02-18" (inglés)
      - "2026-02-01 a 2026-02-18"  (español - Flatpickr locale es)
      - "2026-02-01 - 2026-02-18"  (guión)
      - "2026-02-01"               (fecha única)
    
    Retorna: (fecha_inicio, fecha_fin, display_str, desde_str, hasta_str)
    donde fecha_inicio y fecha_fin son objetos date (no datetime).
    Si falla, retorna None, None, "", "", ""
    """
    if not fechas_str or not fechas_str.strip():
        return None, None, "", "", ""
    
    # Decodificar URL: + → espacio
    fechas_clean = fechas_str.replace("+", " ").strip()
    
    # Detectar separador de rango (orden importa: probar " to " y " a " antes de " - ")
    separador = None
    for sep in [" to ", " a ", " - ", ",", " -", "- "]:
        if sep in fechas_clean:
            separador = sep
            break
    
    try:
        if separador:
            # Es un rango: dividir por el separador detectado
            desde_str, hasta_str = fechas_clean.split(separador, 1)
            desde_str = desde_str.strip()
            hasta_str = hasta_str.strip()
            
            # Parsear a date simple (sin hora)
            fecha_inicio = datetime.strptime(desde_str, "%Y-%m-%d").date()
            fecha_fin = datetime.strptime(hasta_str, "%Y-%m-%d").date()
            
            fecha_display = f"{fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')}"
            return fecha_inicio, fecha_fin, fecha_display, desde_str, hasta_str
            
        else:
            # Es una sola fecha
            fecha_inicio = datetime.strptime(fechas_clean, "%Y-%m-%d").date()
            fecha_fin = fecha_inicio
            
            fecha_display = fecha_inicio.strftime('%d/%m/%Y')
            return fecha_inicio, fecha_fin, fecha_display, fechas_clean, fechas_clean
            
    except (ValueError, IndexError, AttributeError):
        return None, None, "", "", ""  
    
# Control de acceso: valida que usuario tenga rol requerido
def require_roles(request, roles, redirect_to="index", message="No tienes permiso para acceder a esta sección."):
    """Validar roles. Retorna None si está autorizado, sino redirige. Registra acceso denegado."""
    if request.user.rol not in roles:
        messages.error(request, message)
        
        # Auditoría: Acceso denegado
        AuditoriaAccion.registrar(
            request=request,
            tipo='PERM_DENIED',
            descripcion=f'Acceso denegado a {request.path}. Rol requerido: {roles}. Rol actual: {request.user.rol}'
        )
        security_logger.warning(
            f"PERM_DENIED | User: {request.user.username} | Path: {request.path} | "
            f"Required: {roles} | Has: {request.user.rol}"
        )
        
        return redirect(redirect_to)
    return None


# Formateo: obtiene nombre a mostrar
def get_display_name(user):
    """Obtiene nombre completo o username. Retorna '—' si no existe."""
    if not user:
        return "—"
    nombre = (user.get_full_name() or "").strip()
    return nombre if nombre else user.username


# ══════════════════════════════════════════════════════════════
# 3. AUTENTICACIÓN Y VISTAS PRINCIPALES
# ══════════════════════════════════════════════════════════════

# Página de inicio del sistema
def index(request):
    """Dashboard principal. Muestra opciones según rol del usuario."""
    context = {
        'hora_servidor': now_chile().isoformat(),  # ← Usando tu función local
    }
    return render(request, "index.html", context)


# Autenticación: inicio de sesión
def login_view(request):
    """Autentica usuario, registra intento en auditoría."""
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        # Doble chequeo de autenticación por seguridad
        if request.user.is_authenticated:
            return redirect("index")
        
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")

        # Obtener IP para logging (antes del rate limit)
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')

        # Rate limiting: máximo 10 intentos fallidos por IP+usuario en 15 minutos
        rl_key = f"login_attempts_{ip}_{username[:50]}"
        intentos_login = cache.get(rl_key, 0)
        if intentos_login >= 10:
            messages.error(request, "Demasiados intentos fallidos. Espera 15 minutos antes de intentarlo de nuevo.")
            security_logger.warning(f"LOGIN_BLOCKED | User: {username} | IP: {ip} | intentos: {intentos_login}")
            return render(request, "auth/login.html")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.totp_activo:
                # 2FA requerido: guardar en sesión y redirigir a verificación
                request.session['2fa_pending_user_id'] = user.pk
                # Guardar el backend para poder llamar login() luego sin authenticate()
                request.session['2fa_pending_backend'] = user.backend
                request.session['2fa_next'] = request.POST.get('next', '')
                audit_logger.info(f"2FA_REQUIRED | User: {username} | IP: {ip}")
                return redirect("auth_verificar_2fa")

            # Login exitoso: limpiar contador de intentos fallidos
            cache.delete(rl_key)
            login(request, user)
            messages.success(request, f"¡Bienvenido, {user.get_full_name() or user.username}!")
            
            # Auditoría: Login exitoso
            AuditoriaAccion.registrar(
                request=request,
                tipo='LOGIN_OK',
                descripcion=f'Inicio de sesión exitoso para {username}',
                objeto=user
            )
            audit_logger.info(f"LOGIN_OK | User: {username} | IP: {ip}")
            
            return redirect("index")
        else:
            # Incrementar contador de intentos fallidos (expira en 15 min)
            cache.set(rl_key, intentos_login + 1, timeout=900)
            messages.error(request, "Usuario o contraseña incorrectos")
            
            # Auditoría: Login fallido
            AuditoriaAccion.registrar(
                request=request,
                tipo='LOGIN_FAIL',
                descripcion=f'Intento de login fallido para usuario: {username}'
            )
            security_logger.warning(f"LOGIN_FAIL | User: {username} | IP: {ip} | intentos: {intentos_login + 1}")

    return render(request, "auth/login.html")


# Autenticación: cierre de sesión
def logout_view(request):
    """Cierra sesión e registra acción en auditoría."""
    # Requerir POST para evitar CSRF logout via GET
    if request.method != "POST":
        return redirect("index")

    # Guardar datos antes del logout para auditoría
    username = request.user.username if request.user.is_authenticated else 'Anónimo'
    user_obj = request.user if request.user.is_authenticated else None
    
    # Auditoría: Logout
    if user_obj:
        AuditoriaAccion.registrar(
            request=request,
            tipo='LOGOUT',
            descripcion=f'Cierre de sesión para {username}',
            objeto=user_obj
        )
        audit_logger.info(f"LOGOUT | User: {username}")
    
    logout(request)
    messages.success(request, "Has cerrado sesión correctamente")
    return redirect("auth_login")


# Autenticación: perfil de usuario propio
@login_required
def perfil_view(request):
    """Permite al usuario ver y editar sus datos personales y cambiar contraseña."""
    user = request.user
    active_tab = 'perfil'

    form_perfil = EditarPerfilForm(user=user, initial={
        'first_name': user.first_name,
        'last_name':  user.last_name,
        'email':      user.email,
        'telefono':   user.telefono,
    })
    form_password = CambiarPasswordForm(user=user)

    if request.method == "POST":
        accion = request.POST.get('accion')

        if accion == 'perfil':
            form_perfil = EditarPerfilForm(request.POST, user=user)
            if form_perfil.is_valid():
                datos = form_perfil.cleaned_data
                campos_cambiados = {}

                for campo in ('first_name', 'last_name', 'email'):
                    nuevo = datos.get(campo, '')
                    if getattr(user, campo) != nuevo:
                        campos_cambiados[campo] = {'antes': getattr(user, campo), 'despues': nuevo}
                        setattr(user, campo, nuevo)

                nuevo_telefono = datos.get('telefono') or None
                if user.telefono != nuevo_telefono:
                    campos_cambiados['telefono'] = {'antes': user.telefono, 'despues': nuevo_telefono}
                    user.telefono = nuevo_telefono

                user.save(update_fields=['first_name', 'last_name', 'email', 'telefono'])

                if campos_cambiados:
                    AuditoriaAccion.registrar(
                        request=request,
                        tipo='USER_UPDATE',
                        descripcion=f"Perfil actualizado por {user.username}",
                        objeto=user,
                        datos_nuevos=campos_cambiados,
                    )
                    audit_logger.info(f"PROFILE_UPDATE | User: {user.username} | Fields: {list(campos_cambiados)}")

                messages.success(request, "Perfil actualizado correctamente.")
                return redirect("auth_perfil")
            else:
                active_tab = 'perfil'

        elif accion == 'password':
            form_password = CambiarPasswordForm(request.POST, user=user)
            if form_password.is_valid():
                user.set_password(form_password.cleaned_data['password_nueva'])
                user.save(update_fields=['password'])

                AuditoriaAccion.registrar(
                    request=request,
                    tipo='USER_UPDATE',
                    descripcion=f"Contraseña cambiada por {user.username}",
                    objeto=user,
                )
                audit_logger.info(f"PASSWORD_CHANGE | User: {user.username}")

                # Re-autenticar para no perder la sesión tras cambiar contraseña
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, user)

                messages.success(request, "Contraseña actualizada correctamente.")
                return redirect("auth_perfil")
            else:
                active_tab = 'seguridad'

    return render(request, "auth/perfil.html", {
        'form_perfil':   form_perfil,
        'form_password': form_password,
        'active_tab':    active_tab,
    })


# ══════════════════════════════════════════════════════════════
# 3b. 2FA — VERIFICACIÓN, ACTIVACIÓN Y DESACTIVACIÓN
# ══════════════════════════════════════════════════════════════

def verificar_2fa_view(request):
    """Segunda etapa del login: verificar código TOTP cuando 2FA está activo."""
    user_id = request.session.get('2fa_pending_user_id')
    if not user_id:
        return redirect("auth_login")

    try:
        user = Usuario.objects.get(pk=user_id)
    except Usuario.DoesNotExist:
        del request.session['2fa_pending_user_id']
        return redirect("auth_login")

    form = Verificar2FAForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        # Rate limiting: máximo 5 intentos por sesión antes de revocar el flujo 2FA
        intentos_2fa = request.session.get('2fa_intentos', 0)
        if intentos_2fa >= 5:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            ip_2fa = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
            security_logger.warning(f"2FA_BLOCKED | User: {user.username} | IP: {ip_2fa} | Demasiados intentos")
            for k in ('2fa_pending_user_id', '2fa_pending_backend', '2fa_next', '2fa_intentos'):
                request.session.pop(k, None)
            messages.error(request, "Demasiados intentos fallidos. Por seguridad, debes iniciar sesión nuevamente.")
            return redirect("auth_login")

        codigo = form.cleaned_data['codigo']
        totp = pyotp.TOTP(user.totp_secret)

        # Verificar código y proteger contra replay (ventana ±1 intervalo de 30s)
        ahora = timezone.now()
        valido = totp.verify(codigo, valid_window=1)

        # Anti-replay: rechazar si el mismo intervalo ya fue verificado
        if valido and user.totp_ultimo_verificado:
            intervalo_actual = int(ahora.timestamp()) // 30
            intervalo_ultimo = int(user.totp_ultimo_verificado.timestamp()) // 30
            if intervalo_actual == intervalo_ultimo:
                valido = False

        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')

        if valido:
            user.totp_ultimo_verificado = ahora
            user.save(update_fields=['totp_ultimo_verificado'])

            backend = request.session.pop('2fa_pending_backend', None)
            del request.session['2fa_pending_user_id']
            request.session.pop('2fa_intentos', None)  # Limpiar contador al autenticar
            login(request, user, backend=backend)
            messages.success(request, f"¡Bienvenido, {user.get_full_name() or user.username}!")

            AuditoriaAccion.registrar(
                request=request,
                tipo='LOGIN_OK',
                descripcion=f'Inicio de sesión con 2FA exitoso para {user.username}',
                objeto=user,
            )
            audit_logger.info(f"2FA_OK | User: {user.username} | IP: {ip}")

            # Validar next_url para prevenir Open Redirect
            next_url = request.session.pop('2fa_next', '') or ''
            if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                next_url = 'index'
            return redirect(next_url)
        else:
            AuditoriaAccion.registrar(
                request=request,
                tipo='LOGIN_FAIL',
                descripcion=f'Código 2FA incorrecto para {user.username}',
            )
            request.session['2fa_intentos'] = request.session.get('2fa_intentos', 0) + 1
            security_logger.warning(f"2FA_FAIL | User: {user.username} | IP: {ip} | intentos: {request.session['2fa_intentos']}")
            form.add_error('codigo', "Código incorrecto o expirado. Inténtalo de nuevo.")

    return render(request, "auth/verificar_2fa.html", {'form': form})


@login_required
def activar_2fa_view(request):
    """Genera un secreto TOTP temporal, muestra el QR y confirma la activación."""
    user = request.user

    if user.totp_activo:
        messages.info(request, "La verificación en dos pasos ya está activa.")
        return redirect("auth_perfil")

    # Generar o reutilizar secreto temporal guardado en sesión
    if request.method == "GET" or '2fa_setup_secret' not in request.session:
        secret = pyotp.random_base32()
        request.session['2fa_setup_secret'] = secret
    else:
        secret = request.session['2fa_setup_secret']

    # Generar URI otpauth:// y QR como SVG inline
    nombre_app = "ValGas"
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.username,
        issuer_name=nombre_app,
    )
    qr = segno.make(uri, error='M')
    import io
    buf = io.BytesIO()
    qr.save(buf, kind='svg', scale=4, border=2)
    qr_svg = buf.getvalue().decode('utf-8')

    form = Activar2FAConfirmForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        codigo = form.cleaned_data['codigo']
        totp = pyotp.TOTP(secret)

        if totp.verify(codigo, valid_window=1):
            user.totp_secret = secret
            user.totp_activo = True
            user.totp_ultimo_verificado = timezone.now()
            user.save(update_fields=['totp_secret', 'totp_activo', 'totp_ultimo_verificado'])

            del request.session['2fa_setup_secret']

            AuditoriaAccion.registrar(
                request=request,
                tipo='USER_UPDATE',
                descripcion=f'2FA activado por {user.username}',
                objeto=user,
            )
            audit_logger.info(f"2FA_ACTIVATED | User: {user.username}")
            messages.success(request, "¡Verificación en dos pasos activada correctamente!")
            return redirect("auth_perfil")
        else:
            form.add_error('codigo', "Código incorrecto. Verifica que la hora de tu dispositivo sea correcta.")

    return render(request, "auth/activar_2fa.html", {
        'form':   form,
        'qr_svg': qr_svg,
        'secret': secret,
    })


@login_required
def desactivar_2fa_view(request):
    """Desactiva 2FA del usuario tras confirmar contraseña."""
    user = request.user

    if not user.totp_activo:
        messages.info(request, "La verificación en dos pasos no está activa.")
        return redirect("auth_perfil")

    if request.method != "POST":
        return redirect("auth_perfil")

    form = Desactivar2FAForm(request.POST, user=user)
    if form.is_valid():
        user.totp_secret = None
        user.totp_activo = False
        user.totp_ultimo_verificado = None
        user.save(update_fields=['totp_secret', 'totp_activo', 'totp_ultimo_verificado'])

        AuditoriaAccion.registrar(
            request=request,
            tipo='USER_UPDATE',
            descripcion=f'2FA desactivado por {user.username}',
            objeto=user,
        )
        audit_logger.info(f"2FA_DEACTIVATED | User: {user.username}")
        messages.success(request, "Verificación en dos pasos desactivada.")
    else:
        messages.error(request, "Contraseña incorrecta. No se pudo desactivar el 2FA.")

    return redirect("auth_perfil")


# ══════════════════════════════════════════════════════════════
# 4. GESTIÓN ADMINISTRATIVA (Usuarios y Precios de Balones)
# ══════════════════════════════════════════════════════════════

# Admin: crear usuarios del sistema
@login_required
def crear_usuario(request):
    """Crea nuevos usuarios con roles específicos. Solo Admin."""
    if not request.user.is_superuser:
        resp = require_roles(request, ["admin"], "index", "No tienes permiso para crear usuarios.")
        if resp:
            return resp

    form = CrearUsuarioSeguroForm(request.POST or None, user=request.user)

    if request.method == "POST":
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = Usuario.objects.create_user(
                    username=data['username'],
                    first_name=data.get('first_name', ''),
                    last_name=data.get('last_name', ''),
                    telefono=data.get('telefono') or None,
                    rol=data['rol'],
                    password=data['password1'],
                )
                user.is_active = True
                user.save(update_fields=['is_active'])

            AuditoriaAccion.registrar(
                request=request,
                tipo='USER_CREATE',
                descripcion=f"Usuario creado: {user.username} con rol {user.rol}",
                objeto=user,
                datos_nuevos={
                    'username': user.username,
                    'nombre': user.get_full_name(),
                    'rol': user.rol,
                    'telefono': user.telefono,
                }
            )
            audit_logger.info(f"USER_CREATE | New: {user.username} | By: {request.user.username}")

            messages.success(request, f"Usuario '{user.get_full_name() or user.username}' creado correctamente con rol {user.get_rol_display()}.")
            return redirect("reportes_ventas")

        attempted_username = (request.POST.get('username') or '').strip()[:150]
        attempted_role = (request.POST.get('rol') or '').strip()[:20]
        allowed_roles = {role for role, _ in Usuario.ROLES}
        invalid_admin_confirmation = 'admin_password' in form.errors
        invalid_role_attempt = bool(attempted_role) and attempted_role not in allowed_roles

        if invalid_admin_confirmation or invalid_role_attempt:
            AuditoriaAccion.registrar(
                request=request,
                tipo='SUSPICIOUS',
                descripcion='Intento sensible rechazado durante creación de usuario.',
                datos_nuevos={
                    'username': attempted_username,
                    'rol': attempted_role,
                    'errores': form.errors.get_json_data(),
                }
            )
        security_logger.warning(
            f"USER_CREATE_REJECTED | By: {request.user.username} | Username: {attempted_username or '-'} | Errors: {form.errors.as_json()}"
        )

    return render(request, "auth/crear_usuario.html", {"form": form})

@login_required
def precios_balones(request):
    """
    Vista para Jefes, admin y bodegueros.
    Permite actualizar masivamente precios de compra, venta y estado de balones.
    Registra historial automático de los valores ANTERIORES cuando hay cambios.
    """
    # Verificación de rol (tu función existente)
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para gestionar precios.")
    if resp:
        return resp

    balones = TipoBalon.objects.all().annotate(
        tipo_orden=Case(
            When(tipo_gas='normal', then=Value(0)),
            When(tipo_gas='catalitico', then=Value(1)),
            When(tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by("tipo_orden", "-peso_neto_gas")

    if request.method == "POST":
        cambios_realizados = False

        for balon in balones:
            # Claves de los campos del formulario
            compra_key    = f"precio_compra_{balon.id}"
            local_key     = f"precio_local_{balon.id}"
            dom_key       = f"precio_domicilio_{balon.id}"
            activo_key    = f"activo_{balon.id}"

            # Valores enviados (o los actuales si no se enviaron)
            nuevo_compra_str    = request.POST.get(compra_key)
            nuevo_local_str     = request.POST.get(local_key)
            nuevo_dom_str       = request.POST.get(dom_key)
            nuevo_activo        = activo_key in request.POST

            try:
                nuevo_compra     = int(nuevo_compra_str) if nuevo_compra_str else balon.precio_compra
                nuevo_local      = int(nuevo_local_str)  if nuevo_local_str  else balon.precio_local
                nuevo_domicilio  = int(nuevo_dom_str)    if nuevo_dom_str    else balon.precio_domicilio

                if nuevo_compra < 0 or nuevo_local < 0 or nuevo_domicilio < 0:
                    raise ValueError("Precios no pueden ser negativos")
            except ValueError:
                messages.error(request, f"Precio inválido para {balon.nombre}. Se ignoraron cambios en esta fila.")
                continue

            # Detectar si realmente hay algún cambio
            hubo_cambio = (
                balon.precio_compra     != nuevo_compra or
                balon.precio_local      != nuevo_local or
                balon.precio_domicilio  != nuevo_domicilio or
                balon.activo            != nuevo_activo
            )

            if hubo_cambio:
                # ────────────────────────────────────────────────
                # GUARDAR HISTORIAL ANTES de aplicar los cambios
                # ────────────────────────────────────────────────
                datos_anteriores = {
                    'precio_compra': int(balon.precio_compra),
                    'precio_local': int(balon.precio_local),
                    'precio_domicilio': int(balon.precio_domicilio),
                    'activo': balon.activo
                }
                
                HistorialPrecioBalon.objects.create(
                    nombre_balon       = balon.nombre,                # snapshot actual (antes del cambio)
                    precio_compra_anterior     = balon.precio_compra,
                    precio_local_anterior      = balon.precio_local,
                    precio_domicilio_anterior  = balon.precio_domicilio,
                    activo_anterior            = balon.activo,
                    actualizado_por            = request.user,
                    # fecha_cambio se autogenera con default=timezone.now
                )

                # Ahora sí aplicar los nuevos valores
                balon.precio_compra     = nuevo_compra
                balon.precio_local      = nuevo_local
                balon.precio_domicilio  = nuevo_domicilio
                balon.activo            = nuevo_activo
                balon.actualizado_por   = request.user
                balon.save()
                
                # Auditoría: Precio actualizado
                AuditoriaAccion.registrar(
                    request=request,
                    tipo='PRECIO_UPDATE',
                    descripcion=f'Precio actualizado para {balon.nombre}',
                    objeto=balon,
                    datos_anteriores=datos_anteriores,
                    datos_nuevos={
                        'precio_compra': nuevo_compra,
                        'precio_local': nuevo_local,
                        'precio_domicilio': nuevo_domicilio,
                        'activo': nuevo_activo
                    }
                )
                audit_logger.info(f"PRECIO_UPDATE | {balon.nombre} | By: {request.user.username}")

                cambios_realizados = True

        if cambios_realizados:
            messages.success(request, "Precios y disponibilidad actualizados correctamente. Historial registrado.")
        else:
            messages.info(request, "No se detectaron cambios válidos.")

        return redirect("precios_lista")

    # GET → mostrar formulario
    return render(request, "balones/precios_balones.html", {"balones": balones})

@login_required
def historial_precios(request):
    if request.user.rol not in ['jefe', 'admin', 'bodeguero']:
        messages.error(request, "No tienes permiso para ver el historial de precios.")
        return redirect('index')

    balones = TipoBalon.objects.all().order_by('nombre')
    balon_id = request.GET.get('balon')
    balon_seleccionado = None
    historial = []
    precios_actuales = None  # ← nuevo

    if balon_id:
        try:
            balon_seleccionado = TipoBalon.objects.get(id=balon_id)
            historial = HistorialPrecioBalon.objects.filter(
                nombre_balon=balon_seleccionado.nombre
            ).order_by('-fecha_cambio')

            # Precios actuales del balón seleccionado
            precios_actuales = {
                'compra': balon_seleccionado.precio_compra,
                'local': balon_seleccionado.precio_local,
                'domicilio': balon_seleccionado.precio_domicilio,
                'activo': balon_seleccionado.activo,
            }

        except TipoBalon.DoesNotExist:
            messages.warning(request, "Balón no encontrado.")
    else:
        historial = HistorialPrecioBalon.objects.all().order_by('-fecha_cambio')[:50]

    # Preparar datos para Chart.js (igual que antes)
    chart_data = None
    if balon_seleccionado and historial:
        labels = []
        compra_data = []
        local_data = []
        domicilio_data = []

        for reg in historial.order_by('fecha_cambio'):  # cronológico
            labels.append(reg.fecha_cambio.strftime('%d/%m/%Y %H:%M'))
            compra_data.append(float(reg.precio_compra_anterior))
            local_data.append(float(reg.precio_local_anterior))
            domicilio_data.append(float(reg.precio_domicilio_anterior))

        chart_data = {
            'labels': labels,
            'compra': compra_data,
            'local': local_data,
            'domicilio': domicilio_data,
            'nombre_balon': balon_seleccionado.nombre
        }

    context = {
        'balones': balones,
        'balon_seleccionado': balon_seleccionado,
        'historial': historial,
        'chart_data': chart_data,
        'precios_actuales': precios_actuales,  # ← nuevo
        'title': 'Historial de Cambios de Precios' + (f' - {balon_seleccionado.nombre}' if balon_seleccionado else '')
    }

    return render(request, 'balones/historial_precios.html', context)


# ══════════════════════════════════════════════════════════════
# 4B. GESTIÓN DE TIPOS DE BALÓN
# ══════════════════════════════════════════════════════════════

# Admin/Jefe/Bodeguero: ver lista de balones
@login_required
def gestionar_balones_lista(request):
    """Listar todos los tipos de balones disponibles. Acceso: Admin/Jefe/Bodeguero."""
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para gestionar balones.")
    if resp:
        return resp
    
    balones = TipoBalon.objects.all().annotate(
        tipo_orden=Case(
            When(tipo_gas='normal', then=Value(0)),
            When(tipo_gas='catalitico', then=Value(1)),
            When(tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by("tipo_orden", "-peso_neto_gas")
    
    # Procesar POST para edición masiva de precios
    if request.method == "POST":
        cambios_realizados = False
        
        # Verificar si el formulario incluye campos de disponibilidad (checkboxes activo_*)
        # Si no hay ningún checkbox en el form, mantener valores actuales de 'activo'
        form_incluye_activo = any(k.startswith('activo_') for k in request.POST)

        for balon in balones:
            # Claves de los campos del formulario
            compra_key    = f"precio_compra_{balon.id}"
            local_key     = f"precio_local_{balon.id}"
            dom_key       = f"precio_domicilio_{balon.id}"
            activo_key    = f"activo_{balon.id}"

            # Valores enviados
            nuevo_compra_str    = request.POST.get(compra_key)
            nuevo_local_str     = request.POST.get(local_key)
            nuevo_dom_str       = request.POST.get(dom_key)
            # Solo procesar 'activo' si el formulario incluye esos campos, sino mantener valor actual
            nuevo_activo        = activo_key in request.POST if form_incluye_activo else balon.activo

            try:
                nuevo_compra     = int(nuevo_compra_str) if nuevo_compra_str else balon.precio_compra
                nuevo_local      = int(nuevo_local_str)  if nuevo_local_str  else balon.precio_local
                nuevo_domicilio  = int(nuevo_dom_str)    if nuevo_dom_str    else balon.precio_domicilio

                if nuevo_compra < 0 or nuevo_local < 0 or nuevo_domicilio < 0:
                    raise ValueError("Precios no pueden ser negativos")
            except ValueError:
                messages.error(request, f"Precio inválido para {balon.nombre}. Se ignoraron cambios en esta fila.")
                continue

            # Detectar si realmente hay algún cambio
            hubo_cambio = (
                balon.precio_compra     != nuevo_compra or
                balon.precio_local      != nuevo_local or
                balon.precio_domicilio  != nuevo_domicilio or
                balon.activo            != nuevo_activo
            )

            if hubo_cambio:
                # Guardar historial antes de aplicar cambios
                HistorialPrecioBalon.objects.create(
                    nombre_balon       = balon.nombre,
                    precio_compra_anterior     = balon.precio_compra,
                    precio_local_anterior      = balon.precio_local,
                    precio_domicilio_anterior  = balon.precio_domicilio,
                    activo_anterior            = balon.activo,
                    actualizado_por            = request.user,
                )

                # Aplicar nuevos valores
                balon.precio_compra     = nuevo_compra
                balon.precio_local      = nuevo_local
                balon.precio_domicilio  = nuevo_domicilio
                balon.activo            = nuevo_activo
                balon.actualizado_por   = request.user
                balon.save()

                cambios_realizados = True

        if cambios_realizados:
            messages.success(request, "Precios y disponibilidad actualizados correctamente. Historial registrado.")
        else:
            messages.info(request, "No se detectaron cambios válidos.")

        return redirect("balones_lista")
    
    context = {
        'balones': balones,
        'title': 'Gestión de Balones',
        'puede_eliminar': request.user.rol in ['jefe', 'admin']
    }
    
    return render(request, 'balones/gestionar_balones.html', context)


# Admin/Jefe/Bodeguero: crear nuevo tipo de balón
@login_required
def gestionar_balones_crear(request):
    """Crear nuevo tipo de balón en el sistema."""
    """
    Crea un nuevo tipo de balón desde la web.
    Acceso: admin, jefe, bodeguero.
    """
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para crear balones.")
    if resp:
        return resp
    
    if request.method == "POST":
        form = TipoBalonForm(request.POST)
        
        if form.is_valid():
            balon = form.save(commit=False)
            balon.actualizado_por = request.user
            balon.save()

            AuditoriaAccion.registrar(
                request=request,
                tipo='BALON_CREATE',
                descripcion=f"Creación de balón: {balon.nombre}",
                objeto=balon,
                datos_nuevos={
                    'nombre': balon.nombre,
                    'peso_neto_gas': balon.peso_neto_gas,
                    'tipo_gas': balon.tipo_gas,
                    'precio_compra': int(balon.precio_compra),
                    'precio_local': int(balon.precio_local),
                    'precio_domicilio': int(balon.precio_domicilio),
                    'activo': balon.activo,
                }
            )

            messages.success(request, f"Balón '{balon.nombre}' creado correctamente.")
            return redirect("balones_lista")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = TipoBalonForm()
    
    context = {
        'form': form,
        'title': 'Crear Nuevo Balón',
        'accion': 'Crear'
    }
    
    return render(request, 'balones/gestionar_balon_form.html', context)


# Admin/Jefe/Bodeguero: editar tipo de balón existente
@login_required
def gestionar_balones_editar(request, balon_id):
    """Modificar datos de un tipo de balón."""
    """
    Edita un tipo de balón existente desde la web.
    Acceso: admin, jefe, bodeguero.
    """
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para editar balones.")
    if resp:
        return resp
    
    balon = get_object_or_404(TipoBalon, id=balon_id)
    
    if request.method == "POST":
        form = TipoBalonForm(request.POST, instance=balon)
        
        if form.is_valid():
            # Guardar historial del cambio anterior
            balon_anterior = TipoBalon.objects.get(id=balon_id)
            
            balon_actualizado = form.save(commit=False)
            balon_actualizado.actualizado_por = request.user
            
            # Verificar si hay cambios reales
            hubo_cambio = (
                balon_anterior.precio_compra != balon_actualizado.precio_compra or
                balon_anterior.precio_local != balon_actualizado.precio_local or
                balon_anterior.precio_domicilio != balon_actualizado.precio_domicilio or
                balon_anterior.activo != balon_actualizado.activo
            )
            
            if hubo_cambio:
                HistorialPrecioBalon.objects.create(
                    nombre_balon=balon_anterior.nombre,
                    precio_compra_anterior=balon_anterior.precio_compra,
                    precio_local_anterior=balon_anterior.precio_local,
                    precio_domicilio_anterior=balon_anterior.precio_domicilio,
                    activo_anterior=balon_anterior.activo,
                    actualizado_por=request.user,
                )

            datos_anteriores = {
                'nombre': balon_anterior.nombre,
                'peso_neto_gas': balon_anterior.peso_neto_gas,
                'tipo_gas': balon_anterior.tipo_gas,
                'precio_compra': int(balon_anterior.precio_compra),
                'precio_local': int(balon_anterior.precio_local),
                'precio_domicilio': int(balon_anterior.precio_domicilio),
                'activo': balon_anterior.activo,
            }
            
            balon_actualizado.save()

            datos_nuevos = {
                'nombre': balon_actualizado.nombre,
                'peso_neto_gas': balon_actualizado.peso_neto_gas,
                'tipo_gas': balon_actualizado.tipo_gas,
                'precio_compra': int(balon_actualizado.precio_compra),
                'precio_local': int(balon_actualizado.precio_local),
                'precio_domicilio': int(balon_actualizado.precio_domicilio),
                'activo': balon_actualizado.activo,
            }

            if form.changed_data:
                AuditoriaAccion.registrar(
                    request=request,
                    tipo='BALON_UPDATE',
                    descripcion=f"Edición de balón: {balon_actualizado.nombre}",
                    objeto=balon_actualizado,
                    datos_anteriores=datos_anteriores,
                    datos_nuevos=datos_nuevos,
                )

            messages.success(request, f"Balón '{balon_actualizado.nombre}' actualizado correctamente.")
            return redirect("balones_lista")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = TipoBalonForm(instance=balon)
    
    context = {
        'form': form,
        'balon': balon,
        'title': f'Editar Balón - {balon.nombre}',
        'accion': 'Editar'
    }
    
    return render(request, 'balones/gestionar_balon_form.html', context)


# Admin/Jefe: eliminar tipo de balón
@login_required
def gestionar_balones_eliminar(request, balon_id):
    """Eliminar un tipo de balón (confirmar primero)."""
    """
    Elimina un tipo de balón (solo si no tiene pedidos asociados).
    Solo para admin y jefe.
    """
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para eliminar balones.")
    if resp:
        return resp
    
    balon = get_object_or_404(TipoBalon, id=balon_id)
    
    # Verificar si el balón tiene detalles de pedidos
    detalles = DetallePedido.objects.filter(balon=balon)
    
    if detalles.exists():
        messages.error(
            request,
            f"No se puede eliminar '{balon.nombre}' porque tiene {detalles.count()} registro(s) de venta asociado(s). "
            "Desactívalo en lugar de eliminarlo."
        )
        return redirect("balones_lista")
    
    nombre_balon = balon.nombre

    AuditoriaAccion.registrar(
        request=request,
        tipo='BALON_DELETE',
        descripcion=f"Eliminación de balón: {nombre_balon}",
        objeto=balon,
        datos_anteriores={
            'nombre': balon.nombre,
            'peso_neto_gas': balon.peso_neto_gas,
            'tipo_gas': balon.tipo_gas,
            'precio_compra': int(balon.precio_compra),
            'precio_local': int(balon.precio_local),
            'precio_domicilio': int(balon.precio_domicilio),
            'activo': balon.activo,
        }
    )

    balon.delete()
    messages.success(request, f"Balón '{nombre_balon}' eliminado correctamente.")
    
    return redirect("balones_lista")


@login_required
def auditoria_lista(request):
    """Listado simple de todos los registros de auditoría. Solo admin."""
    if not request.user.is_superuser:
        resp = require_roles(request, ["admin"], "index", "No tienes permiso para ver la auditoría del sistema.")
        if resp:
            return resp

    registros = AuditoriaAccion.objects.select_related('usuario').all().order_by('-fecha')
    paginator = Paginator(registros, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'total_registros': registros.count(),
    }
    return render(request, 'auditoria/lista_auditoria.html', context)


@login_required
def gestionar_sectores_lista(request):
    """Listar sectores del catálogo administrable."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para gestionar sectores.")
    if resp:
        return resp

    sectores = list(Sector.objects.all().order_by('zona', 'nombre'))
    conteos_pedidos = {
        item['sector']: item['total']
        for item in Pedido.objects.exclude(sector='').values('sector').annotate(total=Count('id'))
    }

    for sector in sectores:
        sector.pedidos_existentes = conteos_pedidos.get(sector.nombre, 0)

    context = {
        'sectores': sectores,
        'title': 'Gestión de Sectores',
        'puede_eliminar': request.user.rol == 'admin',
    }
    return render(request, 'sectores/gestionar_sectores.html', context)


@login_required
def gestionar_sectores_crear(request):
    """Crear un sector en el catálogo administrable."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para crear sectores.")
    if resp:
        return resp

    if request.method == 'POST':
        form = SectorForm(request.POST)
        if form.is_valid():
            sector = form.save()
            messages.success(request, f"Sector '{sector.nombre}' creado correctamente.")
            return redirect('sectores_lista')
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    else:
        form = SectorForm()

    context = {
        'form': form,
        'title': 'Crear Sector',
        'accion': 'Crear',
    }
    return render(request, 'sectores/gestionar_sector_form.html', context)


@login_required
def gestionar_sectores_editar(request, sector_id):
    """Editar un sector del catálogo administrable."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para editar sectores.")
    if resp:
        return resp

    sector = get_object_or_404(Sector, id=sector_id)

    if request.method == 'POST':
        form = SectorForm(request.POST, instance=sector)
        if form.is_valid():
            sector = form.save()
            messages.success(request, f"Sector '{sector.nombre}' actualizado correctamente.")
            return redirect('sectores_lista')
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    else:
        form = SectorForm(instance=sector)

    context = {
        'form': form,
        'sector': sector,
        'title': f'Editar Sector - {sector.nombre}',
        'accion': 'Editar',
    }
    return render(request, 'sectores/gestionar_sector_form.html', context)


@login_required
def gestionar_sectores_eliminar(request, sector_id):
    """Eliminar un sector del catálogo administrable."""
    resp = require_roles(request, ["admin"], "index", "No tienes permiso para eliminar sectores.")
    if resp:
        return resp

    sector = get_object_or_404(Sector, id=sector_id)
    nombre = sector.nombre
    pedidos_asociados = Pedido.objects.filter(sector=nombre).count()
    sector.delete()

    if pedidos_asociados:
        messages.warning(
            request,
            f"Sector '{nombre}' eliminado del catálogo. Los {pedidos_asociados} pedido(s) históricos con ese texto no fueron modificados."
        )
    else:
        messages.success(request, f"Sector '{nombre}' eliminado correctamente.")

    return redirect('sectores_lista')


# ══════════════════════════════════════════════════════════════
# 5. OPERACIONES TRANSACCIONALES (Registro de Ventas/Pedidos)
# ══════════════════════════════════════════════════════════════

# Telefonista/Bodeguero: crear nuevo pedido
@login_required
@never_cache
def transaccional_pedido(request):
    """Registrar nueva venta o pedido. Registra usuario, origen, estado inicial."""
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
        # Verificar token anti-duplicado
        token_enviado = request.POST.get('form_token')
        token_sesion = request.session.pop('form_token_pedido', None)
        if not token_enviado or token_enviado != token_sesion:
            messages.warning(request, "Este pedido ya fue registrado o el formulario expiró.")
            return redirect("index")

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
            pedido.fecha = now_chile()
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
            # NOTIFICACIONES PUSH A CAMIONEROS (ASÍNCRONO)
            # Se ejecuta en segundo plano para no bloquear la respuesta
            # ══════════════════════════════════════════════════════
            if pedido.estado == 'pendiente' and pedido.origen == 'telefono':
                import threading
                
                def enviar_notificaciones_async(pedido_id):
                    try:
                        from .push_notifications import notificar_nuevo_pedido
                        from .models import Pedido
                        pedido_obj = Pedido.objects.get(id=pedido_id)
                        notificados = notificar_nuevo_pedido(pedido_obj)
                        if notificados > 0:
                            audit_logger.info(f"PUSH_SENT | Pedido #{pedido_id} -> {notificados} camioneros notificados")
                    except Exception as e:
                        audit_logger.warning(f"PUSH_ERROR | Pedido #{pedido_id} | Error: {str(e)}")
                
                # Ejecutar en hilo separado (no bloquea la respuesta)
                thread = threading.Thread(target=enviar_notificaciones_async, args=(pedido.id,))
                thread.daemon = True
                thread.start()
            
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
    """Modificar pedido pendiente/en_ruta. Solo registrador puede editar."""
    """
    Edición de pedidos con reglas específicas por rol:
    - Telefonista: solo sus propios pedidos
    - Camionero: solo los que tiene en ruta (estado 'en_ruta')
    - Bodeguero: pedidos propios + pedidos de telefonistas
    - Jefe/Admin: cualquier pedido
    Solo permite editar si está pendiente o en ruta.
    Registra todo cambio en HistorialCambioPedido.
    """
    pedido = get_object_or_404(Pedido, id=pedido_id)

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

        form_cabecera = PedidoCabeceraForm(request.POST, instance=pedido)
        formset = DetalleFormSetEdit(
            request.POST, 
            instance=pedido,
            form_kwargs={'user': request.user}  # ← Pasar usuario para precios correctos
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
        form_cabecera = PedidoCabeceraForm(instance=pedido)
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

    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_monto_hoy": total_monto_hoy,
        "es_telefonista": request.user.rol == "telefonista",
        "fecha_hoy": hoy,
        "total_pedidos": pedidos_activos.count(),
    }
    return render(request, "pedidos/mis_pedidos_hoy.html", context)


@login_required
def mis_pedidos_hoy_api(request):
    """Endpoint AJAX: devuelve HTML actualizado de los pedidos del telefonista/bodeguero de hoy."""
    if request.user.rol not in ['telefonista', 'bodeguero', 'admin', 'jefe']:
        return JsonResponse({'error': 'No autorizado'}, status=403)

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

    context = {
        'pedidos_hoy': pedidos_hoy,
        'total_monto_hoy': total_monto_hoy,
        'total_pedidos': pedidos_activos.count(),
        'es_telefonista': request.user.rol == 'telefonista',
        'user': request.user,
    }
    html = render(request, 'partials/_mis_pedidos_cards.html', context).content.decode('utf-8')
    return JsonResponse({'html': html, 'count': pedidos_activos.count()})


# --- CAMIONERO ---

# Camionero: ver sus entregas completadas hoy
@login_required
def mis_entregas_camionero(request):
    """Ver entregas finalizadas por este camionero hoy."""
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
    
    # Todos los pedidos asignados a este camionero hoy (incluye cancelados para visibilidad)
    pedidos_hoy = Pedido.objects.filter(
        entregador=request.user,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).order_by("-fecha").prefetch_related("detalles__balon")

    # Solo los entregados para cálculos y estadísticas
    pedidos_entregados = pedidos_hoy.filter(estado='entregado')
    total_entregas_hoy = pedidos_entregados.count()
    total_monto_hoy = pedidos_entregados.aggregate(total=Sum('monto_total'))['total'] or 0
    total_kilos_hoy = DetallePedido.objects.filter(
        pedido__in=pedidos_entregados
    ).aggregate(total=Sum(F('cantidad') * F('balon__peso_neto_gas')))['total'] or 0

    context = {
        "pedidos_hoy": pedidos_hoy,
        "total_entregas_hoy": total_entregas_hoy,
        "total_monto_hoy": total_monto_hoy,
        "total_kilos_hoy": total_kilos_hoy,
        "fecha_hoy": hoy,
    }
    return render(request, "entregas/mis_entregas_camionero.html", context)


@login_required
def mis_entregas_camionero_api(request):
    """Endpoint AJAX: devuelve HTML actualizado de entregas completadas hoy por el camionero."""
    resp = require_roles(request, ['camionero'], 'index', 'Acceso restringido.')
    if resp:
        return JsonResponse({'error': 'No autorizado'}, status=403)

    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), timezone=tz_chile)
    fin_dia = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), timezone=tz_chile)

    pedidos_hoy = Pedido.objects.filter(
        entregador=request.user,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).order_by('-fecha').prefetch_related('detalles__balon')

    pedidos_entregados = pedidos_hoy.filter(estado='entregado')
    total_entregas_hoy = pedidos_entregados.count()
    total_monto_hoy = pedidos_entregados.aggregate(total=Sum('monto_total'))['total'] or 0
    total_kilos_hoy = DetallePedido.objects.filter(
        pedido__in=pedidos_entregados
    ).aggregate(total=Sum(F('cantidad') * F('balon__peso_neto_gas')))['total'] or 0

    context = {
        'pedidos_hoy': pedidos_hoy,
        'total_entregas_hoy': total_entregas_hoy,
        'total_monto_hoy': total_monto_hoy,
        'total_kilos_hoy': total_kilos_hoy,
    }
    html = render(request, 'partials/_mis_entregas_camionero_cards.html', context).content.decode('utf-8')
    return JsonResponse({'html': html, 'count': total_entregas_hoy})


# Camionero: panel de entregas
@login_required
def camionero_entregas(request):
    """Panel de control: entregas en rúta, pendientes y completadas hoy."""
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

    return render(request, "entregas/camionero_entregas.html", context)


# Camionero: API para actualización dinámica
@login_required
def camionero_entregas_api(request):
    """Endpoint AJAX: devuelve HTML actualizado de entregas en ruta."""
    resp = require_roles(request, ["camionero", "admin"], "index", "Acceso restringido.")
    if resp:
        return JsonResponse({'error': 'No autorizado'}, status=403)

    user = request.user

    # Zona horaria Chile
    tz_chile = ZoneInfo('America/Santiago')
    ahora = timezone.now().astimezone(tz_chile)
    hoy = ahora.date()

    # Rango para "hoy"
    inicio_dia = timezone.make_aware(datetime.combine(hoy, datetime.min.time()), tz_chile)
    fin_dia = timezone.make_aware(datetime.combine(hoy, datetime.max.time()), tz_chile)

    # Pedidos en ruta del camionero
    pedidos_en_ruta = Pedido.objects.filter(
        estado="en_ruta",
        entregador=user
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("fecha")

    # Pedidos pendientes disponibles hoy
    pendientes = Pedido.objects.filter(
        estado="pendiente",
        origen="telefono",
        entregador__isnull=True,
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia
    ).select_related('registrador').prefetch_related('detalles__balon').order_by("-fecha")

    context = {
        "pedidos_en_ruta": pedidos_en_ruta,
        "pendientes": pendientes,
        "user": user,
    }

    # Renderizar template parcial
    html = render(request, "partials/_entregas_cards.html", context).content.decode('utf-8')
    
    return JsonResponse({
        'html': html,
        'count_en_ruta': pedidos_en_ruta.count(),
        'count_pendientes': pendientes.count(),
    })


# Camionero: tomar pedido asignado
@login_required
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


# Camionero: cancelar entrega
@login_required
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
    if request.user.rol != 'camionero':
        messages.error(request, "Acceso solo para camioneros.")
        return redirect('index')

    balones = TipoBalon.objects.filter(activo=True).annotate(
        tipo_orden=Case(
            When(tipo_gas='normal', then=Value(0)),
            When(tipo_gas='catalitico', then=Value(1)),
            When(tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by('tipo_orden', '-peso_neto_gas')

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
                detalle.save()  # ← guarda sin tocar subtotal

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
    return render(request, 'pedidos/consultas_pedidos.html', context)


# Utilidad auxiliar: exportar pedidos a archivo Excel
def exportar_pedidos_excel(queryset, rango_fechas):
    """Genera archivo .xlsx con detalles de pedidos filtrados."""
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
    timestamp = now_chile().strftime('%Y%m%d_%H%M')
    filename = f"pedidos_{timestamp}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# Admin/Jefe: dashboard de ventas completo
@login_required
def reporte_ventas(request):
    """Dashboard mensal: métricas, gráficos, análisis por balón, origen y trabajador."""
    resp = require_roles(request, ["jefe", "admin"], "index",
                         "Solo jefes y administradores pueden acceder a los reportes.")
    if resp:
        return resp
    
    # ═══════════════════════════════════════════════════════════
    # 1. PROCESAR FILTROS
    # ═══════════════════════════════════════════════════════════
    tz_chile = ZoneInfo('America/Santiago')

    # Navegación por mes
    hoy = today_chile()
    mes_param = request.GET.get("mes", "").strip()
    
    try:
        if mes_param:
            mes_parts = mes_param.split("-")
            año = int(mes_parts[0])
            mes = int(mes_parts[1])
        else:
            año = hoy.year
            mes = hoy.month
    except (ValueError, IndexError):
        año = hoy.year
        mes = hoy.month
    
    # Calcular fechas del mes
    fecha_inicio = date(año, mes, 1)
    fecha_fin = date(año, mes, monthrange(año, mes)[1])
    mes_display = fecha_inicio.strftime('%B %Y').capitalize()
    
    # Mes anterior y siguiente
    if mes == 1:
        mes_anterior = date(año - 1, 12, 1).strftime('%Y-%m')
    else:
        mes_anterior = date(año, mes - 1, 1).strftime('%Y-%m')
    
    if mes == 12:
        mes_siguiente = date(año + 1, 1, 1).strftime('%Y-%m')
    else:
        mes_siguiente = date(año, mes + 1, 1).strftime('%Y-%m')
    
    mes_actual = date(año, mes, 1).strftime('%Y-%m')
    es_mes_actual = (año == hoy.year and mes == hoy.month)
    
    # Base para comparar fechas
    desde_str = fecha_inicio.strftime("%Y-%m-%d")
    hasta_str = fecha_fin.strftime("%Y-%m-%d")

    dt_inicio = datetime(fecha_inicio.year, fecha_inicio.month, fecha_inicio.day,
                         0, 0, 0, tzinfo=tz_chile)
    dt_fin    = datetime(fecha_fin.year, fecha_fin.month, fecha_fin.day,
                         23, 59, 59, 999999, tzinfo=tz_chile)

    # Otros filtros
    filtro_origen = request.GET.get("origen", "")
    filtro_metodo = request.GET.get("metodo_pago", "")
    filtro_sector = request.GET.get("sector", "").strip()
    filtro_registrador = request.GET.get("registrador", "")
    filtro_trabajador = request.GET.get("trabajador", "")  # Para filtrar por camionero
    
    # ═══════════════════════════════════════════════════════════
    # 2. CONSULTA BASE
    # ═══════════════════════════════════════════════════════════
    pedidos = Pedido.objects.filter(
        estado="entregado",
        fecha__gte=dt_inicio,
        fecha__lte=dt_fin,
    ).select_related('registrador', 'entregador').prefetch_related('detalles__balon')
    
    if filtro_origen:
        pedidos = pedidos.filter(origen=filtro_origen)
    if filtro_metodo:
        pedidos = pedidos.filter(metodo_pago=filtro_metodo)
    if filtro_sector:
        pedidos = pedidos.filter(sector=filtro_sector)
    if filtro_registrador:
        pedidos = pedidos.filter(registrador_id=filtro_registrador)
    if filtro_trabajador:
        # Filtrar por camionero/entregador
        pedidos = pedidos.filter(entregador_id=filtro_trabajador)
    
    # ═══════════════════════════════════════════════════════════
    # 3. MÉTRICAS PRINCIPALES
    # ═══════════════════════════════════════════════════════════
    
    total_ventas = pedidos.aggregate(total=Sum('monto_total'))['total'] or 0
    total_ganancias = pedidos.aggregate(total=Sum('ganancia_total'))['total'] or 0
    total_pedidos = pedidos.count()
    promedio_pedido = total_ventas / total_pedidos if total_pedidos > 0 else 0
    margen_promedio = (total_ganancias / total_ventas * 100) if total_ventas > 0 else 0
    
    # Calcular total de kilos vendidos
    total_kilos = DetallePedido.objects.filter(
        pedido__in=pedidos
    ).aggregate(
        total=Sum(F('cantidad') * F('balon__peso_neto_gas'))
    )['total'] or 0
    
    # ═══════════════════════════════════════════════════════════
    # 4. ANÁLISIS POR BALÓN
    # ═══════════════════════════════════════════════════════════
    
    por_balon = DetallePedido.objects.filter(
        pedido__in=pedidos
    ).values(
        'balon__nombre',
        'balon__peso_neto_gas'
    ).annotate(
        unidades_vendidas=Sum('cantidad'),
        kilos_vendidos=Sum(F('cantidad') * F('balon__peso_neto_gas')),
        monto_vendido=Sum(F('cantidad') * F('precio_venta_unitario')),
        ganancia_total=Sum(F('cantidad') * (F('precio_venta_unitario') - F('precio_compra_unitario')))
    ).order_by('-monto_vendido')
    
    # ═══════════════════════════════════════════════════════════
    # 5. ANÁLISIS POR ORIGEN
    # ═══════════════════════════════════════════════════════════
    
    por_origen = pedidos.values('origen').annotate(
        cantidad=Count('id'),
        total_vendido=Sum('monto_total'),
        total_ganancia=Sum('ganancia_total')
    ).order_by('-total_vendido')
    
    # ═══════════════════════════════════════════════════════════
    # 6. ANÁLISIS POR MÉTODO DE PAGO
    # ═══════════════════════════════════════════════════════════
    
    por_metodo = pedidos.values('metodo_pago').annotate(
        cantidad=Count('id'),
        total_vendido=Sum('monto_total')
    ).order_by('-total_vendido')
    
    # ═══════════════════════════════════════════════════════════
    # 7. TOP SECTORES
    # ═══════════════════════════════════════════════════════════
    
    top_sectores = pedidos.exclude(
        Q(sector='') | Q(sector__isnull=True)
    ).values('sector').annotate(
        cantidad_pedidos=Count('id'),
        total_vendido=Sum('monto_total')
    ).order_by('-total_vendido')[:10]
    
    # ═══════════════════════════════════════════════════════════
    # 8. RENDIMIENTO CONSOLIDADO POR TRABAJADOR (EN KILOS)
    #
    # Calcula kilos vendidos por cada trabajador, desglosado por tipo:
    #  - Kilos registrados (telefonista/bodeguero)
    #  - Kilos entregados (camionero en domicilios)
    #  - Kilos tarreo/extra (camionero en tarreo)
    # ═══════════════════════════════════════════════════════════
    
    rendimiento_trabajadores = {}
    
    for pedido in pedidos:
        # Calcular kilos del pedido
        kilos_pedido = sum(
            float(det.cantidad) * float(det.balon.peso_neto_gas or 0)
            for det in pedido.detalles.all()
        )
        
        # --- Opción 1: Registrador (telefonista/bodeguero) ---
        if pedido.registrador_id:
            registrador = pedido.registrador
            if registrador.id not in rendimiento_trabajadores:
                rendimiento_trabajadores[registrador.id] = {
                    'id': registrador.id,
                    'nombre_trabajador': registrador.get_full_name() or registrador.username,
                    'rol': registrador.rol,
                    'kilos_registrados': 0,
                    'kilos_entregados': 0,
                    'kilos_tarreo': 0,
                    'total_pedidos': 0,
                    'total_kilos': 0,
                }
            
            # Distinto destino según origen
            if pedido.origen in ['telefono', 'local']:
                rendimiento_trabajadores[registrador.id]['kilos_registrados'] += kilos_pedido
            elif pedido.origen in ['tarreo', 'venta_extra']:
                # Si es tarreo/extra, el registrador es el dueño de la venta
                rendimiento_trabajadores[registrador.id]['kilos_tarreo'] += kilos_pedido
        
        # --- Opción 2: Entregador (camionero) ---
        if pedido.entregador_id and pedido.origen not in ['tarreo', 'venta_extra']:
            entregador = pedido.entregador
            if entregador.id not in rendimiento_trabajadores:
                rendimiento_trabajadores[entregador.id] = {
                    'id': entregador.id,
                    'nombre_trabajador': entregador.get_full_name() or entregador.username,
                    'rol': entregador.rol,
                    'kilos_registrados': 0,
                    'kilos_entregados': 0,
                    'kilos_tarreo': 0,
                    'total_pedidos': 0,
                    'total_kilos': 0,
                }
            
            rendimiento_trabajadores[entregador.id]['kilos_entregados'] += kilos_pedido
    
    # Consolidar y contar pedidos
    for uid, w in rendimiento_trabajadores.items():
        # Contar cuántos pedidos participó
        pedidos_registrador = pedidos.filter(registrador_id=uid).count() if uid else 0
        pedidos_entregador = pedidos.filter(entregador_id=uid).count() if uid else 0
        w['total_pedidos'] = pedidos_registrador + pedidos_entregador
        w['total_kilos'] = w['kilos_registrados'] + w['kilos_entregados'] + w['kilos_tarreo']
    
    rendimiento_trabajadores_lista = sorted(
        rendimiento_trabajadores.values(),
        key=lambda x: x['total_kilos'],
        reverse=True
    )

    # ═══════════════════════════════════════════════════════════
    # 9. ANÁLISIS TEMPORAL (ventas por día)
    # ═══════════════════════════════════════════════════════════
    
    ventas_por_dia = {}
    
    for pedido in pedidos:
        fecha_local = pedido.fecha.astimezone(tz_chile).date()
        fecha_str = fecha_local.strftime('%Y-%m-%d')
        
        if fecha_str not in ventas_por_dia:
            ventas_por_dia[fecha_str] = {
                'fecha': fecha_local,
                'pedidos': 0,
                'monto': 0,
                'ganancia': 0
            }
        
        ventas_por_dia[fecha_str]['pedidos'] += 1
        ventas_por_dia[fecha_str]['monto'] += float(pedido.monto_total or 0)
        ventas_por_dia[fecha_str]['ganancia'] += float(pedido.ganancia_total or 0)
    
    ventas_por_dia_lista = sorted(ventas_por_dia.values(), key=lambda x: x['fecha'])
    
    # ═══════════════════════════════════════════════════════════
    # 10. PREPARAR DATOS PARA GRÁFICOS
    # ═══════════════════════════════════════════════════════════
    
    chart_balones_labels = dumps([item['balon__nombre'] for item in por_balon[:8]])
    chart_balones_unidades = dumps([int(item['unidades_vendidas']) for item in por_balon[:8]])
    chart_balones_kilos = dumps([float(item['kilos_vendidos']) for item in por_balon[:8]])
    chart_balones_monto = dumps([float(item['monto_vendido']) for item in por_balon[:8]])
    
    chart_origen_labels = dumps([dict(Pedido.ORIGENES).get(item['origen'], item['origen']) for item in por_origen])
    chart_origen_data = dumps([float(item['total_vendido']) for item in por_origen])
    
    metodos_dict = dict([("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia")])
    chart_metodo_labels = dumps([metodos_dict.get(item['metodo_pago'], item['metodo_pago']) for item in por_metodo])
    chart_metodo_data = dumps([float(item['total_vendido']) for item in por_metodo])
    
    chart_dias_labels = dumps([item['fecha'].strftime('%d/%m') for item in ventas_por_dia_lista])
    chart_dias_ventas = dumps([item['monto'] for item in ventas_por_dia_lista])
    chart_dias_ganancias = dumps([item['ganancia'] for item in ventas_por_dia_lista])
    
    chart_sectores_labels = dumps([item['sector'] for item in top_sectores])
    chart_sectores_data = dumps([float(item['total_vendido']) for item in top_sectores])

    # Gráfico de barras: rendimiento de trabajadores por kilos
    chart_trabajadores_labels = dumps([w['nombre_trabajador'] for w in rendimiento_trabajadores_lista[:10]])
    chart_trabajadores_registrado = dumps([float(w['kilos_registrados']) for w in rendimiento_trabajadores_lista[:10]])
    chart_trabajadores_entregado = dumps([float(w['kilos_entregados']) for w in rendimiento_trabajadores_lista[:10]])
    chart_trabajadores_tarreo = dumps([float(w['kilos_tarreo']) for w in rendimiento_trabajadores_lista[:10]])
    
    # ═══════════════════════════════════════════════════════════
    # 11. LISTA DE TRABAJADORES PARA FILTRO
    # ═══════════════════════════════════════════════════════════
    
    trabajadores = Usuario.objects.filter(
        is_active=True,
        rol__in=['telefonista', 'bodeguero', 'jefe', 'admin']
    ).order_by('first_name', 'last_name')
    
    # ═══════════════════════════════════════════════════════════
    # 12. CONTEXT
    # ═══════════════════════════════════════════════════════════
    
    context = {
        # Métricas principales
        'total_ventas': total_ventas,
        'total_ganancias': total_ganancias,
        'total_pedidos': total_pedidos,
        'total_kilos': total_kilos,
        'promedio_pedido': promedio_pedido,
        'margen_promedio': margen_promedio,
        
        # Tablas de análisis
        'por_balon': por_balon,
        'por_origen': por_origen,
        'por_metodo': por_metodo,
        'top_sectores': top_sectores,
        'ventas_por_dia': ventas_por_dia_lista,

        # Rendimiento consolidado por trabajador
        'rendimiento_trabajadores': rendimiento_trabajadores_lista,
        
        # Datos para gráficos
        'chart_balones_labels': chart_balones_labels,
        'chart_balones_unidades': chart_balones_unidades,
        'chart_balones_kilos': chart_balones_kilos,
        'chart_balones_monto': chart_balones_monto,
        
        'chart_origen_labels': chart_origen_labels,
        'chart_origen_data': chart_origen_data,
        
        'chart_metodo_labels': chart_metodo_labels,
        'chart_metodo_data': chart_metodo_data,
        
        'chart_dias_labels': chart_dias_labels,
        'chart_dias_ventas': chart_dias_ventas,
        'chart_dias_ganancias': chart_dias_ganancias,
        
        'chart_sectores_labels': chart_sectores_labels,
        'chart_sectores_data': chart_sectores_data,

        'chart_trabajadores_labels': chart_trabajadores_labels,
        'chart_trabajadores_registrado': chart_trabajadores_registrado,
        'chart_trabajadores_entregado': chart_trabajadores_entregado,
        'chart_trabajadores_tarreo': chart_trabajadores_tarreo,
        
        # Navegación de mes
        'mes_display': mes_display,
        'mes_anterior': mes_anterior,
        'mes_siguiente': mes_siguiente,
        'mes_actual': mes_actual,
        'es_mes_actual': es_mes_actual,
        
        # Filtros
        'filtro_origen': filtro_origen,
        'filtro_metodo': filtro_metodo,
        'filtro_sector': filtro_sector,
        'filtro_registrador': filtro_registrador,
        'filtro_trabajador': filtro_trabajador,
        
        # Choices para filtros
        'origen_choices': Pedido.ORIGENES,
        'metodo_choices': [("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia")],
        'trabajadores': trabajadores,
        'sectores_choices': Pedido.SECTORES,
        'camioneros': Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name'),
    }
    
    return render(request, "reportes/reporte_ventas.html", context)

# Admin/Jefe: reporte de sobres diarios
@login_required
def reporte_sobres(request):
    """Reporte mensual de sobres cerrados: dinero, kilos, gastos, pagos por tipo."""
    resp = require_roles(request, ["jefe", "admin"], "index",
                         "Solo jefes y administradores pueden acceder al reporte de sobres.")
    if resp:
        return resp

    # ═══════════════════════════════════════════════════════════
    # 1. PERÍODO — siempre un mes completo, navegable con ?mes=YYYY-MM
    # ═══════════════════════════════════════════════════════════
    hoy = today_chile()

    mes_str = request.GET.get("mes", "").strip()
    try:
        if mes_str:
            anio, mes = int(mes_str[:4]), int(mes_str[5:7])
        else:
            anio, mes = hoy.year, hoy.month
    except (ValueError, IndexError):
        anio, mes = hoy.year, hoy.month

    fecha_inicio = date(anio, mes, 1)
    fecha_fin    = date(anio, mes, monthrange(anio, mes)[1])
    mes_display  = fecha_inicio.strftime('%B %Y').capitalize()
    mes_actual   = f"{anio:04d}-{mes:02d}"

    mes_anterior  = f"{anio-1:04d}-12" if mes == 1  else f"{anio:04d}-{mes-1:02d}"
    mes_siguiente = f"{anio+1:04d}-01" if mes == 12 else f"{anio:04d}-{mes+1:02d}"
    es_mes_actual = (anio == hoy.year and mes == hoy.month)

    # Filtro opcional por camionero
    filtro_trabajador = request.GET.get("trabajador", "").strip()

    # ═══════════════════════════════════════════════════════════
    # 2. QUERYSET BASE — solo sobres cerrados del mes
    # ═══════════════════════════════════════════════════════════
    sobres_qs = SobreDiario.objects.filter(
        fecha_correspondiente__gte=fecha_inicio,
        fecha_correspondiente__lte=fecha_fin,
        cerrado=True,
    ).select_related('trabajador').prefetch_related(
        'lineas__balon', 'pagos', 'gastos'
    )

    if filtro_trabajador:
        sobres_qs = sobres_qs.filter(trabajador_id=filtro_trabajador)

    # Evaluar una sola vez y separar por tipo
    lista_bodega = list(sobres_qs.filter(tipo='bodega').order_by('fecha_correspondiente'))
    lista_camion = list(sobres_qs.filter(tipo='camion').order_by('fecha_correspondiente'))
    lista_todos  = lista_bodega + lista_camion

    # ═══════════════════════════════════════════════════════════
    # 3. FUNCIÓN AUXILIAR — métricas de un grupo de sobres
    # ═══════════════════════════════════════════════════════════
    TIPO_PAGO_DISPLAY = {
        'abono': 'Abono Caja', 'transferencia': 'Transferencia',
        'visa': 'Visa/POS', 'cheque': 'Cheque',
        'efectivo': 'Efectivo', 'otro': 'Otro',
    }

    def calcular_metricas(sobres_lista):
        total_declarado = 0.0
        total_kilos     = 0
        total_balones   = 0
        total_gastos    = 0.0
        total_no_ef     = 0.0
        pagos_por_tipo  = {}
        balones_dict    = {}

        for s in sobres_lista:
            total_declarado += float(s.monto_declarado or 0)

            for linea in s.lineas.all():
                decl  = int(linea.cantidad_declarada or 0)
                peso  = int(linea.balon.peso_neto_gas or 0)
                nombre = linea.balon.nombre
                total_balones += decl
                total_kilos   += decl * peso
                if nombre not in balones_dict:
                    balones_dict[nombre] = {'nombre': nombre, 'peso': peso, 'unidades': 0, 'kilos': 0}
                balones_dict[nombre]['unidades'] += decl
                balones_dict[nombre]['kilos']    += decl * peso

            for g in s.gastos.all():
                total_gastos += float(g.monto or 0)

            for p in s.pagos.all():
                monto = float(p.monto or 0)
                # Solo contar como "no efectivo" si NO es efectivo
                if p.tipo_pago != 'efectivo':
                    total_no_ef += monto
                label = TIPO_PAGO_DISPLAY.get(p.tipo_pago, p.tipo_pago)
                pagos_por_tipo[label] = pagos_por_tipo.get(label, 0) + monto

        return {
            'total_declarado':   total_declarado,
            'total_kilos':       total_kilos,
            'total_balones':     total_balones,
            'total_gastos':      total_gastos,
            'total_no_ef':       total_no_ef,
            'efectivo_estimado': total_declarado - total_gastos - total_no_ef,
            'pagos_por_tipo':    pagos_por_tipo,
            'balones_lista':     sorted(balones_dict.values(), key=lambda x: x['peso']),
            'count':             len(sobres_lista),
        }

    # ═══════════════════════════════════════════════════════════
    # 3a. AGRUPAR POR TRABAJADOR — para tarjetas individuales
    # ═══════════════════════════════════════════════════════════
    trabajadores_dict = {}  # trabajador_id (o 'bodega') → lista de sobres

    for s in lista_todos:
        clave = 'bodega' if s.tipo == 'bodega' else s.trabajador_id
        if clave not in trabajadores_dict:
            trabajadores_dict[clave] = []
        trabajadores_dict[clave].append(s)

    # Calcular métricas por trabajador
    resumen_trabajadores = []
    for clave, sobres_trab in trabajadores_dict.items():
        metricas = calcular_metricas(sobres_trab)
        
        if clave == 'bodega':
            nombre = 'Bodega/Local'
            tipo_mostrar = 'bodega'
            usuario_obj = None
        else:
            usuario_obj = Usuario.objects.get(id=clave)
            nombre = usuario_obj.get_full_name() or usuario_obj.username
            tipo_mostrar = 'camion'
        
        resumen_trabajadores.append({
            'clave': clave,
            'nombre': nombre,
            'tipo': tipo_mostrar,
            'usuario_obj': usuario_obj,
            'sobres': sobres_trab,
            **metricas  # expande todas las métricas
        })

    # Ordenar: bodega primero, luego camioneros por nombre
    resumen_trabajadores.sort(key=lambda x: (x['tipo'] == 'camion', x['nombre']))

    m_bodega = calcular_metricas(lista_bodega)
    m_camion = calcular_metricas(lista_camion)

    # Totales globales
    total_declarado   = m_bodega['total_declarado'] + m_camion['total_declarado']
    total_kilos       = m_bodega['total_kilos']     + m_camion['total_kilos']
    total_balones     = m_bodega['total_balones']   + m_camion['total_balones']
    total_gastos      = m_bodega['total_gastos']    + m_camion['total_gastos']
    total_no_ef       = m_bodega['total_no_ef']     + m_camion['total_no_ef']
    efectivo_estimado = total_declarado - total_gastos - total_no_ef

    # Pagos globales consolidados
    pagos_global = {}
    for d in (m_bodega['pagos_por_tipo'], m_camion['pagos_por_tipo']):
        for tipo, monto in d.items():
            pagos_global[tipo] = pagos_global.get(tipo, 0) + monto

    # Balones globales consolidados
    balones_global_dict = {}
    for b in m_bodega['balones_lista'] + m_camion['balones_lista']:
        n = b['nombre']
        if n not in balones_global_dict:
            balones_global_dict[n] = {'nombre': n, 'peso': b['peso'], 'unidades': 0, 'kilos': 0}
        balones_global_dict[n]['unidades'] += b['unidades']
        balones_global_dict[n]['kilos']    += b['kilos']
    balones_global_lista = sorted(balones_global_dict.values(), key=lambda x: x['peso'])

    # ═══════════════════════════════════════════════════════════
    # 4. EVOLUCIÓN DIARIA (para gráfico)
    # ═══════════════════════════════════════════════════════════
    dias_dict = {}
    for s in lista_todos:
        d = s.fecha_correspondiente
        if d not in dias_dict:
            dias_dict[d] = {'fecha': d, 'bodega': 0.0, 'camion': 0.0}
        monto = float(s.monto_declarado or 0)
        dias_dict[d]['bodega' if s.tipo == 'bodega' else 'camion'] += monto

    dias_lista = sorted(dias_dict.values(), key=lambda x: x['fecha'])

    # ═══════════════════════════════════════════════════════════
    # 5.5 GASTOS DESGLOSADOS — por trabajador y bodega
    # ═══════════════════════════════════════════════════════════
    gastos_por_trabajador = {}
    gastos_globales = []

    for s in lista_todos:
        clave = 'bodega' if s.tipo == 'bodega' else s.trabajador_id
        for gasto in s.gastos.all():
            gasto_dict = {
                'fecha': s.fecha_correspondiente,
                'descripcion': gasto.descripcion,
                'monto': float(gasto.monto or 0),
                'nota': gasto.nota,
                'trabajador_clave': clave,
            }
            gastos_globales.append(gasto_dict)
            if clave not in gastos_por_trabajador:
                gastos_por_trabajador[clave] = []
            gastos_por_trabajador[clave].append(gasto_dict)

    # Sumarizar gastos por trabajador
    gastos_resumen = []
    for clave, gastos_list in gastos_por_trabajador.items():
        if clave == 'bodega':
            nombre = 'Bodega/Local'
        else:
            try:
                usuario_obj = Usuario.objects.get(id=clave)
                nombre = usuario_obj.get_full_name() or usuario_obj.username
            except:
                nombre = f'Trabajador #{clave}'
        
        monto_total = sum(g['monto'] for g in gastos_list)
        gastos_resumen.append({
            'nombre': nombre,
            'clave': clave,
            'monto_total': monto_total,
            'gastos': sorted(gastos_list, key=lambda x: x['fecha'], reverse=True),
        })

    gastos_resumen.sort(key=lambda x: x['monto_total'], reverse=True)

    # ═══════════════════════════════════════════════════════════
    # 5. TABLA DETALLE — una fila por sobre
    # ═══════════════════════════════════════════════════════════
    sobres_tabla = []
    for s in sorted(lista_todos, key=lambda x: (x.fecha_correspondiente, x.tipo), reverse=True):
        kilos_s    = sum(int(l.cantidad_declarada or 0) * int(l.balon.peso_neto_gas or 0) for l in s.lineas.all())
        balones_s  = sum(int(l.cantidad_declarada or 0) for l in s.lineas.all())
        gastos_s   = sum(float(g.monto or 0) for g in s.gastos.all())
        no_ef_s    = sum(float(p.monto or 0) for p in s.pagos.all())
        declarado  = float(s.monto_declarado or 0)

        nombre_trabajador = 'Bodega' if s.tipo == 'bodega' else (
            (s.trabajador.get_full_name() or s.trabajador.username) if s.trabajador else '—'
        )

        sobres_tabla.append({
            'id':                s.id,
            'fecha':             s.fecha_correspondiente,
            'tipo':              s.tipo,
            'nombre_trabajador': nombre_trabajador,
            'balones':           balones_s,
            'kilos':             kilos_s,
            'declarado':         declarado,
            'gastos':            gastos_s,
            'no_efectivo':       no_ef_s,
            'dinero_neto':       declarado,
            'km':                s.kilometraje_camion or 0,
        })

    # Subtotales por tipo (para filas de resumen al final de cada grupo)
    def subtotal(tipo):
        filas = [f for f in sobres_tabla if f['tipo'] == tipo]
        if not filas:
            return None
        return {
            'count':             len(filas),
            'balones':           sum(f['balones']           for f in filas),
            'kilos':             sum(f['kilos']             for f in filas),
            'declarado':         sum(f['declarado']         for f in filas),
            'gastos':            sum(f['gastos']            for f in filas),
            'no_efectivo':       sum(f['no_efectivo']       for f in filas),
            'dinero_neto':       sum(f['dinero_neto']       for f in filas),
            'km':                sum(f['km']                for f in filas),
        }

    sub_bodega = subtotal('bodega')
    sub_camion = subtotal('camion')

    # ═══════════════════════════════════════════════════════════
    # 6. DATOS PARA GRÁFICOS
    # ═══════════════════════════════════════════════════════════
    chart_dias_labels    = dumps([d['fecha'].strftime('%d/%m')  for d in dias_lista])
    chart_dias_bodega    = dumps([d['bodega']                   for d in dias_lista])
    chart_dias_camion    = dumps([d['camion']                   for d in dias_lista])

    chart_balon_labels   = dumps([b['nombre']   for b in balones_global_lista])
    chart_balon_unidades = dumps([b['unidades'] for b in balones_global_lista])
    chart_balon_kilos    = dumps([b['kilos']    for b in balones_global_lista])

    chart_pagos_labels   = dumps(list(pagos_global.keys()))
    chart_pagos_data     = dumps(list(pagos_global.values()))

    # ═══════════════════════════════════════════════════════════
    # 7. CONTEXT
    # ═══════════════════════════════════════════════════════════
    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    context = {
        # Navegación
        'mes_display':    mes_display,
        'mes_actual':     mes_actual,
        'mes_anterior':   mes_anterior,
        'mes_siguiente':  mes_siguiente,
        'es_mes_actual':  es_mes_actual,

        # Filtros
        'filtro_trabajador': filtro_trabajador,
        'camioneros':        camioneros,

        # Resumen por trabajador (NEW)
        'resumen_trabajadores': resumen_trabajadores,

        # Métricas globales
        'total_declarado':    total_declarado,
        'total_kilos':        total_kilos,
        'total_balones':      total_balones,
        'total_gastos':       total_gastos,
        'total_no_ef':        total_no_ef,
        'efectivo_estimado':  efectivo_estimado,
        'pagos_global':       pagos_global,
        'balones_global':     balones_global_lista,

        # Métricas por tipo
        'm_bodega': m_bodega,
        'm_camion': m_camion,

        # Tabla detalle
        'sobres_tabla': sobres_tabla,
        'sub_bodega':   sub_bodega,
        'sub_camion':   sub_camion,

        # Gastos desglosados (NEW)
        'gastos_resumen': gastos_resumen,
        'gastos_globales': gastos_globales,

        # Gráficos
        'chart_dias_labels':    chart_dias_labels,
        'chart_dias_bodega':    chart_dias_bodega,
        'chart_dias_camion':    chart_dias_camion,
        'chart_balon_labels':   chart_balon_labels,
        'chart_balon_unidades': chart_balon_unidades,
        'chart_balon_kilos':    chart_balon_kilos,
        'chart_pagos_labels':   chart_pagos_labels,
        'chart_pagos_data':     chart_pagos_data,
    }

    return render(request, "sobres/reporte_sobres.html", context)


# Admin/Jefe: ver detalle de un pedido específico
@login_required
def detalle_pedido(request, pedido_id):
    """Detalle completo de un pedido: productos, estado, historial de cambios."""
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
        return redirect("pedidos_consulta" if request.user.rol in ["jefe", "admin"] else "index")

    context = {
        "pedido": pedido,
        "puede_editar": request.user.rol in ["jefe", "admin"],
    }
    return render(request, "pedidos/detalle_pedido.html", context)


# ══════════════════════════════════════════════════════════════
# 8. GESTIÓN DE SOBRES DIARIOS (Cierre de Caja y Rendición)
# ══════════════════════════════════════════════════════════════

# Jefe/Bodeguero: listar sobres diarios creados
@login_required
def lista_sobres_diarios(request):
    """Ver sobres operativos filtrados por fecha de creacion."""
    """
    Listado para seleccionar qué sobre abrir/editar (Bodega o Camionero).
    Si hay más de un sobre del día, muestra lista para elegir.
    """
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        messages.error(request, "Acceso no permitido.")
        return redirect('index')

    hoy = today_chile()
    fecha_filtro_str = (request.GET.get('fecha') or '').strip()
    try:
        fecha_filtro = datetime.strptime(fecha_filtro_str, "%Y-%m-%d").date() if fecha_filtro_str else hoy
    except ValueError:
        fecha_filtro = hoy
        messages.warning(request, "La fecha indicada no es valida. Se mostro la fecha de hoy.")

    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    tz_chile = ZoneInfo('America/Santiago')
    tz_utc = ZoneInfo('UTC')
    inicio_dia = datetime.combine(fecha_filtro, time.min)
    inicio_dia = timezone.make_aware(inicio_dia, tz_chile).astimezone(tz_utc)
    fin_dia = datetime.combine(fecha_filtro, time.max)
    fin_dia = timezone.make_aware(fin_dia, tz_chile).astimezone(tz_utc)

    sobres_del_dia = SobreDiario.objects.filter(
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia,
    ).select_related('trabajador', 'creado_por').order_by('-fecha', '-id')

    # Sobres de bodega creados en la fecha seleccionada
    sobres_bodega = list(sobres_del_dia.filter(tipo='bodega'))

    # Sobres de camioneros creados en la fecha seleccionada
    sobres_camioneros = list(
        sobres_del_dia.filter(tipo='camion').order_by('trabajador__first_name', '-fecha', '-id')
    )

    return render(request, 'sobres/lista_sobres.html', {
        'hoy': hoy,
        'fecha_filtro': fecha_filtro,
        'camioneros': camioneros,
        'sobres_bodega': sobres_bodega,
        'sobres_camioneros': sobres_camioneros,
    })


def get_rango_utc_para_fecha(fecha_objetivo):
    """Retorna el inicio y fin del dia en UTC para una fecha Chile."""
    tz_chile = ZoneInfo('America/Santiago')
    tz_utc = ZoneInfo('UTC')

    inicio_dia = datetime.combine(fecha_objetivo, time.min)
    inicio_dia = timezone.make_aware(inicio_dia, tz_chile).astimezone(tz_utc)
    fin_dia = datetime.combine(fecha_objetivo, time.max)
    fin_dia = timezone.make_aware(fin_dia, tz_chile).astimezone(tz_utc)

    return inicio_dia, fin_dia

# Jefe/Bodeguero: editar sobre diario
@login_required
def editar_sobre_diario(request):
    """Modificar monto declarado, gastos y notas de un sobre."""
    """
    Vista para editar un sobre diario.
    
    IMPORTANTE: La URL debe ser:
    - /sobres/editar/?sobre_id=123  (FORMA CORRECTA - carga sobre específico)
    
    ANTIGUAS (deprecadas pero soportadas por compatibilidad):
    - /sobres/editar/?bodega=1&fecha=...  (crea/busca sobre y redirige con sobre_id)
    - /sobres/editar/?camionero=5&fecha=...  (crea/busca sobre y redirige con sobre_id)
    """
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para editar sobres.")
    if resp:
        return resp

    usuario = request.user
    sobre_id = request.GET.get('sobre_id')
    
    # CASO 1: Si viene sobre_id, cargar directamente ese sobre
    if sobre_id:
        sobre = get_object_or_404(SobreDiario, id=sobre_id)
        if sobre.tipo == 'bodega':
            titulo = "Sobre Diario - Bodega"
        else:
            titulo = f"Sobre Diario - {sobre.trabajador.get_full_name() or sobre.trabajador.username}"
        tipo_sobre = sobre.tipo
        trabajador = sobre.trabajador
        creado = False
    
    # CASO 2: Si no viene sobre_id pero viene bodega/camionero (legacy)
    # → Buscar/crear el sobre del día y redirigir con sobre_id
    else:
        es_bodega = request.GET.get('bodega') == '1'
        camionero_id = request.GET.get('camionero')
        fecha_query = (request.GET.get('fecha') or '').strip()
        fecha_creacion_query = (request.GET.get('fecha_creacion') or '').strip()
        
        if es_bodega:
            tipo_sobre = 'bodega'
            trabajador = None
            titulo = "Sobre Diario - Bodega"
        elif camionero_id:
            tipo_sobre = 'camion'
            trabajador = get_object_or_404(Usuario, id=camionero_id, rol='camionero')
            titulo = f"Sobre Diario - {trabajador.get_full_name() or trabajador.username}"
        else:
            messages.error(request, "Parámetros inválidos. Use: ?sobre_id=123")
            return redirect('sobres_lista')
        
        # Buscar sobre de la fecha solicitada (abierto o cerrado) o crear uno nuevo
        try:
            fecha_objetivo = datetime.strptime(fecha_query, "%Y-%m-%d").date() if fecha_query else today_chile()
        except ValueError:
            messages.warning(request, "La fecha indicada no es valida. Se usara la fecha de hoy.")
            fecha_objetivo = today_chile()

        try:
            fecha_creacion_objetivo = (
                datetime.strptime(fecha_creacion_query, "%Y-%m-%d").date()
                if fecha_creacion_query else today_chile()
            )
        except ValueError:
            messages.warning(request, "La fecha de creacion indicada no es valida. Se usara la fecha de hoy.")
            fecha_creacion_objetivo = today_chile()

        inicio_creacion, fin_creacion = get_rango_utc_para_fecha(fecha_creacion_objetivo)
        
        forzar_nuevo = request.GET.get('forzar_nuevo') == '1'

        if forzar_nuevo:
            sobre = None
        else:
            sobres_candidatos = SobreDiario.objects.filter(
                fecha_correspondiente=fecha_objetivo,
                tipo=tipo_sobre,
                trabajador=trabajador
            )

            # Preferir sobres creados en la jornada desde donde el usuario esta trabajando.
            sobre = sobres_candidatos.filter(
                fecha__gte=inicio_creacion,
                fecha__lte=fin_creacion,
            ).order_by('-fecha', '-id').first()

            # Compatibilidad: si no existe uno creado en esa jornada, usar el mas reciente historico.
            if not sobre:
                sobre = sobres_candidatos.order_by('-fecha', '-id').first()

        if sobre:
            # Existe un sobre del día, usarlo (el usuario decide si crear otro)
            creado = False
        else:
            # No existe ningún sobre para esa fecha, o se forzó uno nuevo
            sobre = SobreDiario.objects.create(
                fecha=now_chile(),
                tipo=tipo_sobre,
                trabajador=trabajador,
                creado_por=usuario,
                fecha_correspondiente=fecha_objetivo,
            )
            creado = True
        
        # REDIRECT CON sobre_id (forma correcta)
        return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre.id}")

    # ═══════════════════════════════════════════════════════════
    # Crear o actualizar líneas de balones
    # Se hace siempre: tanto si el sobre es nuevo como si ya existe
    # ═══════════════════════════════════════════════════════════
    
    # Preparar datos para crear líneas (si es necesario)
    sincronizar_sobre_desde_pedidos(sobre)

    # Preparar los formsets
    formset_lineas = LineaSobreFormSet(
        request.POST or None,
        instance=sobre,
        prefix='lineas'
    )

    formset_pagos = LineaPagoFormSet(
        request.POST or None,
        instance=sobre,
        prefix='pagos'
    )

    formset_gastos = LineaGastoFormSet(
        request.POST or None,
        instance=sobre,
        prefix='gastos'
    )

    if request.method == "POST":
        
        if not sobre.pk:
            sobre.save()
        
        # Actualizar campos adicionales del sobre
        if "kilometraje_camion" in request.POST:
            try:
                km = int(request.POST["kilometraje_camion"])
                if 0 <= km <= 999999:
                    sobre.kilometraje_camion = km
                else:
                    messages.warning(request, "Kilometraje fuera de rango (0–999999). Se guardó el valor anterior.")
            except (ValueError, TypeError):
                pass  # Campo vacío o inválido: se conserva el valor existente
        
        # Si se especifica fecha correspondiente
        fecha_correspondiente_str = request.POST.get("fecha_correspondiente")
        if fecha_correspondiente_str:
            try:
                sobre.fecha_correspondiente = datetime.strptime(fecha_correspondiente_str, "%Y-%m-%d").date()
            except ValueError:
                messages.warning(request, "Fecha correspondiente inválida. Se conservó la fecha anterior.")

        sobre.creado_por = request.user
        sobre.save()

        sincronizar_sobre_desde_pedidos(sobre)
        formset_lineas = LineaSobreFormSet(
            request.POST or None,
            instance=sobre,
            prefix='lineas'
        )

        # Ahora sí validar y guardar los formsets
        if all([
            formset_lineas.is_valid(),
            formset_pagos.is_valid(),
            formset_gastos.is_valid()
        ]):
            for form in formset_lineas.forms:
                if not form.cleaned_data:
                    continue

                snapshot_key = f"calc_snapshot_{form.instance.id}"
                snapshot_value = request.POST.get(snapshot_key)
                if snapshot_value is None:
                    continue

                try:
                    snapshot_calculada = int(snapshot_value)
                except (TypeError, ValueError):
                    continue

                cantidad_declarada = form.cleaned_data.get('cantidad_declarada')
                if cantidad_declarada is None:
                    continue

                if int(cantidad_declarada) == snapshot_calculada:
                    cantidad_actual = int(form.instance.cantidad_calculada or 0)
                    form.cleaned_data['cantidad_declarada'] = cantidad_actual
                    form.instance.cantidad_declarada = cantidad_actual

            # Guardar todos los formsets en una transacción atómica
            with transaction.atomic():
                formset_lineas.save()
                formset_pagos.save()
                formset_gastos.save()

                # Recalcular totales del sobre
                sobre.save()

            # Si se presionó el botón "Cerrar"
            if "cerrar" in request.POST:
                if sobre.cerrado:
                    messages.warning(request, "Este sobre ya estaba cerrado.")
                else:
                    sobre.cerrado = True
                    sobre.declarado_el = timezone.now()
                    sobre.nota_cierre = request.POST.get("nota_cierre", "")
                    
                    # Capturar kilometraje del camión (solo para camioneros)
                    if sobre.tipo != 'bodega':
                        try:
                            km = request.POST.get("kilometraje_camion", "").strip()
                            if km:
                                sobre.kilometraje_camion = int(km)
                        except (ValueError, TypeError):
                            messages.warning(request, "Kilometraje inválido. Se guardará el valor anterior.")
                    
                    sobre.save()
                    
                    if sobre.tipo == 'bodega':
                        km_msg = ""
                    else:
                        km_msg = f" Kilometraje: {sobre.kilometraje_camion} km."
                    
                    messages.success(
                        request,
                        f"Sobre cerrado correctamente el {sobre.declarado_el.strftime('%d/%m/%Y %H:%M')}.{km_msg}"
                    )
                    
                    # Redirigir a la lista para que el usuario elija qué hacer
                    return redirect('sobres_lista')
            else:
                messages.success(request, "Cambios guardados correctamente (borrador).")
                # Redirigir al mismo sobre (ya tenemos sobre_id)
                return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre.id}")

        else:
            messages.error(request, "Hay errores en el formulario. Revise los campos marcados.")

    # Contexto para el template
    ahora_chile = now_chile()
    hora_actual = ahora_chile.hour
    
    # ⏰ Determinar si mostrar alerta de "hora recomendada para cerrar"
    # (ej: después de las 20:00 = 8 PM)
    hora_recomendada_cierre = 18  # 
    mostrar_alerta_cierre = hora_actual >= hora_recomendada_cierre and not sobre.cerrado
    
    # Determinar si es bodega (para el template)
    es_bodega = sobre.tipo == 'bodega'
    
    context = {
        'sobre': sobre,
        'formset_lineas': formset_lineas,
        'formset_pagos': formset_pagos,
        'formset_gastos': formset_gastos,
        'hoy': today_chile(),
        'titulo': titulo,
        'es_bodega': es_bodega,
        'mostrar_alerta_cierre': mostrar_alerta_cierre,
        'hora_recomendada_cierre': f"{hora_recomendada_cierre}:00",
        'hora_actual': f"{hora_actual}:{ahora_chile.minute:02d}",
    }

    return render(request, 'sobres/sobres.html', context)

@login_required
def refrescar_sobre_diario(request, sobre_id):
    """Sincroniza un sobre abierto con los pedidos y retorna sus cantidades actualizadas."""
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        return JsonResponse({'ok': False, 'error': 'Acceso no permitido.'}, status=403)

    sobre = get_object_or_404(SobreDiario, id=sobre_id)

    if sobre.cerrado:
        return JsonResponse({'ok': True, 'cerrado': True, 'lineas': []})

    sincronizar_sobre_desde_pedidos(sobre, crear_lineas_faltantes=False)
    sobre.refresh_from_db()

    lineas = list(
        sobre.lineas.values('id', 'cantidad_calculada', 'cantidad_declarada')
    )

    return JsonResponse({
        'ok': True,
        'cerrado': False,
        'lineas': lineas,
        'monto_calculado_app': int(sobre.monto_calculado_app or 0),
        'sincronizado_en': now_chile().strftime('%H:%M:%S'),
    })

# ══════════════════════════════════════════════════════════════
# 
# ══════════════════════════════════════════════════════════════

# Jefe/Bodeguero: crear nuevo sobre diario
@login_required
def crear_sobre_nuevo(request):
    """Crear sobre para cierres de caja día a día."""
    """
    Vista para crear un nuevo sobre manualmente con fecha específica.
    OPCIONAL - Solo si necesitas crear sobres post-cierre.
    """
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        messages.error(request, "Acceso no permitido.")
        return redirect('index')

    if request.method == "POST":
        es_bodega = request.POST.get('tipo') == 'bodega'
        camionero_id = request.POST.get('camionero')
        fecha_correspondiente_str = request.POST.get('fecha_correspondiente')

        if not fecha_correspondiente_str:
            messages.error(request, "Debe especificar una fecha para el sobre.")
            return redirect('sobres_lista')

        try:
            fecha_correspondiente = datetime.strptime(fecha_correspondiente_str, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Fecha inválida.")
            return redirect('sobres_lista')

        if es_bodega:
            tipo_sobre = 'bodega'
            trabajador = None
        elif camionero_id:
            tipo_sobre = 'camion'
            trabajador = get_object_or_404(Usuario, id=camionero_id, rol='camionero')
        else:
            messages.error(request, "Debe seleccionar un tipo de sobre válido.")
            return redirect('sobres_lista')

        # Crear el sobre
        sobre = SobreDiario.objects.create(
            fecha=today_chile(),
            fecha_correspondiente=fecha_correspondiente,
            tipo=tipo_sobre,
            trabajador=trabajador,
            creado_por=request.user,
        )
        messages.success(request, f"Sobre creado para la fecha {fecha_correspondiente.strftime('%d/%m/%Y')}.")
        
        # Redirigir a editar ese sobre
        return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre.id}")

    # Formulario
    hoy = today_chile()
    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    return render(request, 'sobres/crear_sobre.html', {
        'hoy': hoy,
        'camioneros': camioneros,
    })


# Jefe/Bodeguero: crear sobre para cierre posterior
@login_required
def crear_sobre_post_cierre(request, sobre_id):
    """Crear nuevo sobre basado en uno anterior (para seguimiento)."""
    """
    Crea un nuevo sobre SOLO si el anterior está cerrado.
    Hereda: tipo, trabajador del sobre anterior.
    Parámetro POST: fecha_correspondiente (opcional, default = hoy).
    """
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para crear un nuevo sobre.")
    if resp:
        return resp

    # Obtener el sobre anterior
    sobre_anterior = get_object_or_404(SobreDiario, id=sobre_id)
    
    # Validar que el sobre anterior esté CERRADO (salvo que se fuerce por jefe/admin)
    forzar = request.POST.get('forzar_creacion') == '1'
    puede_forzar = request.user.rol in ['jefe', 'admin']

    if not sobre_anterior.cerrado and not (forzar and puede_forzar):
        messages.error(request, "Solo puedes crear un nuevo sobre después de cerrar el anterior.")
        return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre_anterior.id}")
    
    if request.method == "POST":
        # Obtener fecha correspondiente del formulario
        fecha_correspondiente_str = request.POST.get('fecha_correspondiente', '')
        
        try:
            fecha_correspondiente = datetime.strptime(fecha_correspondiente_str, "%Y-%m-%d").date()
        except ValueError:
            fecha_correspondiente = today_chile()
        
        # Crear el nuevo sobre con los mismos datos del anterior
        nuevo_sobre = SobreDiario.objects.create(
            fecha=now_chile(),
            fecha_correspondiente=fecha_correspondiente,
            tipo=sobre_anterior.tipo,
            trabajador=sobre_anterior.trabajador,
            creado_por=request.user,
            cerrado=False,  # IMPORTANTE: Asegurar que el nuevo sobre está ABIERTO
        )
        
        # SAFEGUARD: Asegurar que el nuevo sobre está COMPLETAMENTE VACÍO
        # (aunque no debería tener nada, ya que se acaba de crear)
        nuevo_sobre.lineas.all().delete()
        nuevo_sobre.pagos.all().delete()
        nuevo_sobre.gastos.all().delete()
        
        if forzar and puede_forzar and not sobre_anterior.cerrado:
            messages.warning(
                request,
                f"Sobre adicional creado para {fecha_correspondiente.strftime('%d/%m/%Y')}. "
                f"El sobre anterior (#{sobre_anterior.id}) sigue abierto."
            )
        else:
            messages.success(
                request,
                f"Nuevo sobre creado para {fecha_correspondiente.strftime('%d/%m/%Y')}. "
                f"El anterior estaba cerrado el {sobre_anterior.declarado_el.strftime('%d/%m/%Y %H:%M')}."
            )
        
        # Redirigir a editar el nuevo sobre
        # IMPORTANTE: Solo usar sobre_id para evitar que se cargue otro sobre por búsqueda de fecha
        return redirect(f"{reverse('sobres_editar')}?sobre_id={nuevo_sobre.id}")
    
    # Si no es POST, redirigir al sobre anterior
    return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre_anterior.id}")


# Jefe: historial de sobres
@login_required
def historial_sobres(request):
    """Ver historial completo de sobres cerrados con métricas diarias."""
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para ver historial de sobres.")
    if resp:
        return resp

    modo_historial = request.GET.get('modo', 'dia')
    if modo_historial not in ['dia', 'mes', 'todo']:
        modo_historial = 'dia'

    hoy = today_chile()
    fecha_str = request.GET.get('fecha', '')
    mes_str = request.GET.get('mes', '')

    if fecha_str:
        try:
            fecha_seleccionada = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            fecha_seleccionada = hoy
            messages.warning(request, 'La fecha indicada no es valida. Se usara hoy.')
    else:
        fecha_seleccionada = hoy

    if mes_str:
        try:
            anio_seleccionado, mes_seleccionado_num = map(int, mes_str.split('-'))
            primer_dia_mes = date(anio_seleccionado, mes_seleccionado_num, 1)
        except (TypeError, ValueError):
            primer_dia_mes = date(hoy.year, hoy.month, 1)
            messages.warning(request, 'El mes indicado no es valido. Se usara el mes actual.')
    else:
        primer_dia_mes = date(hoy.year, hoy.month, 1)

    ultimo_dia_mes = date(
        primer_dia_mes.year,
        primer_dia_mes.month,
        monthrange(primer_dia_mes.year, primer_dia_mes.month)[1],
    )

    todos_sobres = SobreDiario.objects.select_related('trabajador', 'creado_por').prefetch_related('lineas')

    if modo_historial == 'mes':
        todos_sobres = todos_sobres.filter(
            fecha_correspondiente__gte=primer_dia_mes,
            fecha_correspondiente__lte=ultimo_dia_mes,
        )
        titulo_periodo = primer_dia_mes.strftime('%B de %Y').capitalize()
        descripcion_periodo = 'Resumen mensual de sobres segun su fecha correspondiente.'
    elif modo_historial == 'todo':
        titulo_periodo = 'Todo el historial'
        descripcion_periodo = 'Todos los sobres registrados, ordenados por fecha correspondiente.'
    else:
        todos_sobres = todos_sobres.filter(fecha_correspondiente=fecha_seleccionada)
        titulo_periodo = fecha_seleccionada.strftime('%A %d de %B de %Y').capitalize()
        descripcion_periodo = 'Sobres de una fecha especifica, agrupados por tipo.'
    
    # Agrupar: Bodega vs Camioneros
    sobres_bodega = todos_sobres.filter(tipo='bodega').order_by('-fecha_correspondiente', '-cerrado', '-fecha')
    
    # Para camioneros: agrupar por trabajador
    sobres_camionero = todos_sobres.filter(tipo='camion').order_by('trabajador__first_name', '-fecha_correspondiente', '-cerrado', '-fecha')
    
    # Crear diccionario {camionero: [sobres]}
    sobres_por_camionero = {}
    for sobre in sobres_camionero:
        camionero = sobre.trabajador
        if camionero not in sobres_por_camionero:
            sobres_por_camionero[camionero] = []
        sobres_por_camionero[camionero].append(sobre)

    grupos_camioneros = []
    for camionero, sobres in sobres_por_camionero.items():
        cerrados = sum(1 for sobre in sobres if sobre.cerrado)
        grupos_camioneros.append({
            'camionero': camionero,
            'sobres': sobres,
            'total': len(sobres),
            'cerrados': cerrados,
            'abiertos': len(sobres) - cerrados,
            'total_declarado': sum(int(sobre.monto_declarado or 0) for sobre in sobres),
        })

    total_sobres = todos_sobres.count()
    total_cerrados = todos_sobres.filter(cerrado=True).count()
    total_bodega = sobres_bodega.count()
    total_camion = sobres_camionero.count()
    total_declarado = sum(int(sobre.monto_declarado or 0) for sobre in todos_sobres)
    
    context = {
        'modo_historial': modo_historial,
        'fecha_seleccionada': fecha_seleccionada,
        'mes_seleccionado': primer_dia_mes.strftime('%Y-%m'),
        'hoy': hoy,
        'titulo_periodo': titulo_periodo,
        'descripcion_periodo': descripcion_periodo,
        'sobres_bodega': sobres_bodega,
        'grupos_camioneros': grupos_camioneros,
        'total_sobres': total_sobres,
        'total_cerrados': total_cerrados,
        'total_abiertos': total_sobres - total_cerrados,
        'total_bodega': total_bodega,
        'total_camion': total_camion,
        'total_declarado': total_declarado,
    }

    return render(request, 'sobres/historial_sobres.html', context)

# Jefe/Bodeguero: imprimir sobre para descarga/impresión
@login_required
def imprimir_sobre_diario(request, sobre_id):
    """Generar PDF o versión imprimible del sobre."""
    """
    Genera una página HTML optimizada para impresión del sobre diario.
    Formato compacto similar a Excel.
    """
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        messages.error(request, "No tienes permiso para imprimir sobres.")
        return redirect('index')

    sobre = get_object_or_404(SobreDiario, id=sobre_id)
    lineas = sobre.lineas.all().annotate(
        tipo_orden=Case(
            When(balon__tipo_gas='normal', then=Value(0)),
            When(balon__tipo_gas='catalitico', then=Value(1)),
            When(balon__tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by('tipo_orden', '-balon__peso_neto_gas')
    pagos = sobre.pagos.all()
    gastos = sobre.gastos.all()

    # Calcular totales y subtotales por línea
    lineas_data = []
    total_venta = 0
    total_cantidad_calc = 0
    total_cantidad_decl = 0
    total_diferencia = 0
    total_kilos = 0
    
    for linea in lineas:
        subtotal = linea.cantidad_declarada * linea.precio_venta_unitario
        diferencia = linea.cantidad_declarada - linea.cantidad_calculada
        kilos = linea.cantidad_declarada * linea.balon.peso_neto_gas
        
        lineas_data.append({
            'linea': linea,
            'subtotal': subtotal,
            'diferencia': diferencia
        })
        
        total_venta += subtotal
        total_cantidad_calc += linea.cantidad_calculada
        total_cantidad_decl += linea.cantidad_declarada
        total_diferencia += diferencia
        total_kilos += kilos
    
    total_pagos = sum(pago.monto for pago in pagos)
    total_gastos = sum(gasto.monto for gasto in gastos)
    total_contabilizado = total_pagos + total_gastos
    diferencia = total_venta - total_contabilizado

    # Kilos acumulados en el mes (camioneros y bodega)
    total_kilos_mes = 0
    if sobre.tipo != 'bodega' and sobre.trabajador:
        from django.db.models import DecimalField as DField
        sobres_mes = SobreDiario.objects.filter(
            tipo='camion',
            trabajador=sobre.trabajador,
            fecha_correspondiente__year=sobre.fecha_correspondiente.year,
            fecha_correspondiente__month=sobre.fecha_correspondiente.month,
        )
        total_kilos_mes = LineaSobre.objects.filter(
            sobre__in=sobres_mes
        ).aggregate(
            total=Sum(
                F('cantidad_declarada') * F('balon__peso_neto_gas'),
                output_field=DField()
            )
        )['total'] or 0
    elif sobre.tipo == 'bodega':
        from django.db.models import DecimalField as DField
        sobres_mes = SobreDiario.objects.filter(
            tipo='bodega',
            fecha_correspondiente__year=sobre.fecha_correspondiente.year,
            fecha_correspondiente__month=sobre.fecha_correspondiente.month,
        )
        total_kilos_mes = LineaSobre.objects.filter(
            sobre__in=sobres_mes
        ).aggregate(
            total=Sum(
                F('cantidad_declarada') * F('balon__peso_neto_gas'),
                output_field=DField()
            )
        )['total'] or 0

    context = {
        'sobre': sobre,
        'lineas_data': lineas_data,
        'pagos': pagos,
        'gastos': gastos,
        'total_venta': total_venta,
        'total_pagos': total_pagos,
        'total_gastos': total_gastos,
        'total_contabilizado': total_contabilizado,
        'diferencia': diferencia,
        'total_cantidad_calc': total_cantidad_calc,
        'total_cantidad_decl': total_cantidad_decl,
        'total_diferencia': total_diferencia,
        'total_kilos': total_kilos,
        'total_kilos_mes': total_kilos_mes,
    }

    return render(request, 'sobres/imprimir_sobre.html', context)

# Jefe/Bodeguero: exportar sobre a Excel
@login_required
def exportar_sobre_excel(request, sobre_id):
    """Exportar detalles de sobre a archivo .xlsx."""
    if request.user.rol not in ['bodeguero', 'jefe', 'admin']:
        messages.error(request, "No tienes permiso para exportar sobres.")
        return redirect('index')

    sobre = get_object_or_404(SobreDiario, id=sobre_id)
    lineas = sobre.lineas.all().annotate(
        tipo_orden=Case(
            When(balon__tipo_gas='normal', then=Value(0)),
            When(balon__tipo_gas='catalitico', then=Value(1)),
            When(balon__tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by('tipo_orden', '-balon__peso_neto_gas')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sobre Diario"

    # ── Estilos ───────────────────────────────────────
    font_title = Font(name='Arial', size=14, bold=True)
    font_header = Font(name='Arial', size=10, bold=True)
    font_normal = Font(name='Arial', size=10)
    font_label = Font(name='Arial', size=10, bold=True)
    
    fill_yellow = PatternFill(start_color='FFFF00', fill_type='solid')
    fill_light_yellow = PatternFill(start_color='FFFFCC', fill_type='solid')
    fill_green = PatternFill(start_color='C6EFCE', fill_type='solid')
    fill_red = PatternFill(start_color='FFC7CE', fill_type='solid')
    fill_gray = PatternFill(start_color='D3D3D3', fill_type='solid')
    fill_blue_light = PatternFill(start_color='DDEBF7', fill_type='solid')

    border_thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))
    border_medium = Border(left=Side(style='medium'), right=Side(style='medium'),
                           top=Side(style='medium'), bottom=Side(style='medium'))
    
    align_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    align_right = Alignment(horizontal='right', vertical='center')
    align_left = Alignment(horizontal='left', vertical='center')

    # ── ENCABEZADO ────────────────────────────────────
    ws.merge_cells('A1:K1')
    ws['A1'] = "CUENTAS SOBRES - KIM GAS"
    ws['A1'].font = font_title
    ws['A1'].alignment = align_center
    ws['A1'].fill = fill_blue_light

    ws['A2'] = "Responsable:"
    ws['A2'].font = font_header
    ws['A2'].alignment = align_right

    chofer_nombre = "BODEGA" if sobre.tipo == 'bodega' else (sobre.trabajador.get_full_name() or sobre.trabajador.username).upper()
    ws['B2'] = chofer_nombre
    ws['B2'].font = font_header

    # Fecha en formato visual
    ws['D2'] = "FECHA:"
    ws['D2'].font = font_header
    ws['D2'].alignment = align_right
    
    ws['E2'] = sobre.fecha.day
    ws['F2'] = sobre.fecha.strftime("%b").upper()
    ws['G2'] = sobre.fecha.year
    for col in 'EFG':
        ws[f'{col}2'].font = font_header
        ws[f'{col}2'].alignment = align_center
        ws[f'{col}2'].fill = fill_yellow
        ws[f'{col}2'].border = border_thin

    # ── TABLA BALONES ─────────────────────────────────
    start_col = 1  # Columna A
    col = start_col + 1  # B en adelante

    # FILA 4: PESOS (5 KG, 11 KG, etc.)
    ws.cell(row=4, column=start_col).value = "TIPO BALÓN"
    ws.cell(row=4, column=start_col).font = font_label
    ws.cell(row=4, column=start_col).fill = fill_blue_light
    ws.cell(row=4, column=start_col).alignment = align_center
    ws.cell(row=4, column=start_col).border = border_thin
    
    for linea in lineas:
        ws.cell(row=4, column=col).value = f"{linea.balon.peso_neto_gas} KG"
        ws.cell(row=4, column=col).font = font_header
        ws.cell(row=4, column=col).alignment = align_center
        ws.cell(row=4, column=col).fill = fill_blue_light
        ws.cell(row=4, column=col).border = border_thin
        col += 1

    end_col_balones = col - 1

    # FILA 5: PRECIOS UNITARIOS (amarillo)
    ws.cell(row=5, column=start_col).value = "PRECIO UNITARIO"
    ws.cell(row=5, column=start_col).font = font_label
    ws.cell(row=5, column=start_col).fill = fill_yellow
    ws.cell(row=5, column=start_col).alignment = align_center
    ws.cell(row=5, column=start_col).border = border_thin
    
    col = start_col + 1
    for linea in lineas:
        cell = ws.cell(row=5, column=col)
        cell.value = int(linea.precio_venta_unitario or 0)
        cell.fill = fill_yellow
        cell.font = font_header
        cell.alignment = align_center
        cell.border = border_thin
        cell.number_format = '#,##0'
        col += 1

    # FILA 6: CANTIDAD CALCULADA (APP)
    ws.cell(row=6, column=start_col).value = "CANT. CALCULADA (APP)"
    ws.cell(row=6, column=start_col).font = font_label
    ws.cell(row=6, column=start_col).fill = fill_gray
    ws.cell(row=6, column=start_col).alignment = align_center
    ws.cell(row=6, column=start_col).border = border_thin
    
    col = start_col + 1
    for linea in lineas:
        cell = ws.cell(row=6, column=col)
        cell.value = linea.cantidad_calculada or 0
        cell.alignment = align_center
        cell.border = border_thin
        cell.font = font_normal
        col += 1

    # FILA 7: CANTIDAD DECLARADA (REAL)
    ws.cell(row=7, column=start_col).value = "CANT. DECLARADA (REAL)"
    ws.cell(row=7, column=start_col).font = font_label
    ws.cell(row=7, column=start_col).fill = fill_gray
    ws.cell(row=7, column=start_col).alignment = align_center
    ws.cell(row=7, column=start_col).border = border_thin
    
    col = start_col + 1
    for linea in lineas:
        cell = ws.cell(row=7, column=col)
        cell.value = linea.cantidad_declarada or 0
        cell.alignment = align_center
        cell.border = border_thin
        cell.font = Font(bold=True)
        if (linea.cantidad_declarada or 0) == (linea.cantidad_calculada or 0):
            cell.fill = fill_green
        else:
            cell.fill = fill_light_yellow
        col += 1

    # FILA 8: DIFERENCIA
    ws.cell(row=8, column=start_col).value = "DIFERENCIA"
    ws.cell(row=8, column=start_col).font = font_label
    ws.cell(row=8, column=start_col).fill = fill_gray
    ws.cell(row=8, column=start_col).alignment = align_center
    ws.cell(row=8, column=start_col).border = border_thin
    
    col = start_col + 1
    for linea in lineas:
        diff = (linea.cantidad_declarada or 0) - (linea.cantidad_calculada or 0)
        cell = ws.cell(row=8, column=col)
        cell.value = diff
        cell.alignment = align_center
        cell.border = border_thin
        if diff != 0:
            cell.font = Font(bold=True, color="FF0000")
            cell.fill = fill_red
        else:
            cell.font = Font(bold=True)
        col += 1

    # FILA 9: SUBTOTAL POR BALÓN (NUEVO - fórmula)
    ws.cell(row=9, column=start_col).value = "SUBTOTAL VENTA"
    ws.cell(row=9, column=start_col).font = font_label
    ws.cell(row=9, column=start_col).fill = fill_light_yellow
    ws.cell(row=9, column=start_col).alignment = align_center
    ws.cell(row=9, column=start_col).border = border_thin
    
    col = start_col + 1
    for i in range(len(lineas)):
        precio_coord = f"{get_column_letter(col)}5"
        decl_coord = f"{get_column_letter(col)}7"
        cell = ws.cell(row=9, column=col)
        cell.value = f"={decl_coord}*{precio_coord}"
        cell.number_format = '#,##0'
        cell.alignment = align_center
        cell.border = border_thin
        cell.font = Font(bold=True)
        cell.fill = fill_light_yellow
        col += 1

    # ── RESUMEN A LA DERECHA ──────────────────────────
    ventas_col = end_col_balones + 2

    # TOTAL VENTAS
    ws.cell(row=5, column=ventas_col).value = "TOTAL VENTAS"
    ws.cell(row=5, column=ventas_col).font = Font(bold=True, size=11)
    ws.cell(row=5, column=ventas_col).alignment = align_right
    ws.cell(row=5, column=ventas_col).border = border_thin

    if lineas.exists():
        ws.cell(row=5, column=ventas_col + 1).value = f"=SUM({get_column_letter(start_col+1)}9:{get_column_letter(end_col_balones)}9)"
    else:
        ws.cell(row=5, column=ventas_col + 1).value = 0
    ws.cell(row=5, column=ventas_col + 1).fill = fill_yellow
    ws.cell(row=5, column=ventas_col + 1).number_format = '#,##0'
    ws.cell(row=5, column=ventas_col + 1).font = Font(bold=True, size=12)
    ws.cell(row=5, column=ventas_col + 1).alignment = align_right
    ws.cell(row=5, column=ventas_col + 1).border = border_medium

    # TOTAL KILOS
    ws.cell(row=6, column=ventas_col).value = "TOTAL KILOS"
    ws.cell(row=6, column=ventas_col).font = Font(bold=True, size=11)
    ws.cell(row=6, column=ventas_col).alignment = align_right
    ws.cell(row=6, column=ventas_col).border = border_thin

    if lineas.exists():
        kilos_parts = [f"({get_column_letter(c)}7*{lineas[i].balon.peso_neto_gas})" 
                      for i, c in enumerate(range(start_col+1, end_col_balones+1))]
        ws.cell(row=6, column=ventas_col + 1).value = "=" + "+".join(kilos_parts)
    else:
        ws.cell(row=6, column=ventas_col + 1).value = 0
    ws.cell(row=6, column=ventas_col + 1).fill = fill_yellow
    ws.cell(row=6, column=ventas_col + 1).number_format = '#,##0'
    ws.cell(row=6, column=ventas_col + 1).font = Font(bold=True, size=12)
    ws.cell(row=6, column=ventas_col + 1).alignment = align_right
    ws.cell(row=6, column=ventas_col + 1).border = border_medium

    # ── PAGOS ─────────────────────────────────────────
    pagos_col_label = ventas_col
    pagos_col_monto = ventas_col + 1

    pagos_row_start = 10
    ws.cell(row=pagos_row_start, column=pagos_col_label).value = "TIPO PAGO"
    ws.cell(row=pagos_row_start, column=pagos_col_monto).value = "MONTO"
    for c in [pagos_col_label, pagos_col_monto]:
        ws.cell(row=pagos_row_start, column=c).font = font_header
        ws.cell(row=pagos_row_start, column=c).fill = fill_blue_light
        ws.cell(row=pagos_row_start, column=c).alignment = align_center
        ws.cell(row=pagos_row_start, column=c).border = border_thin

    row_p = pagos_row_start + 1
    total_pagos = 0

    tipo_map = {
        'abono': 'Abono Caja',
        'transferencia': 'Transferencia',
        'visa': 'Visa/POS',
        'cheque': 'Cheque',
        'efectivo': 'Efectivo',
        'otro': 'Otro',
    }

    for pago in sobre.pagos.all():
        tipo = tipo_map.get(pago.tipo_pago, pago.tipo_pago.upper())
        ws.cell(row=row_p, column=pagos_col_label).value = tipo
        ws.cell(row=row_p, column=pagos_col_label).border = border_thin
        ws.cell(row=row_p, column=pagos_col_monto).value = pago.monto
        ws.cell(row=row_p, column=pagos_col_monto).number_format = '#,##0'
        ws.cell(row=row_p, column=pagos_col_monto).alignment = align_right
        ws.cell(row=row_p, column=pagos_col_monto).border = border_thin
        total_pagos += pago.monto
        row_p += 1

    ws.cell(row=row_p, column=pagos_col_label).value = "TOTAL PAGOS"
    ws.cell(row=row_p, column=pagos_col_label).font = font_header
    ws.cell(row=row_p, column=pagos_col_label).fill = fill_blue_light
    ws.cell(row=row_p, column=pagos_col_label).border = border_thin
    ws.cell(row=row_p, column=pagos_col_monto).value = total_pagos
    ws.cell(row=row_p, column=pagos_col_monto).font = Font(bold=True, size=11)
    ws.cell(row=row_p, column=pagos_col_monto).number_format = '#,##0'
    ws.cell(row=row_p, column=pagos_col_monto).alignment = align_right
    ws.cell(row=row_p, column=pagos_col_monto).fill = fill_blue_light
    ws.cell(row=row_p, column=pagos_col_monto).border = border_medium

    # ── GASTOS ────────────────────────────────────────
    gastos_row_start = row_p + 2
    ws.cell(row=gastos_row_start, column=pagos_col_label).value = "DESCRIPCIÓN GASTO"
    ws.cell(row=gastos_row_start, column=pagos_col_monto).value = "MONTO"
    for c in [pagos_col_label, pagos_col_monto]:
        ws.cell(row=gastos_row_start, column=c).font = font_header
        ws.cell(row=gastos_row_start, column=c).fill = fill_blue_light
        ws.cell(row=gastos_row_start, column=c).alignment = align_center
        ws.cell(row=gastos_row_start, column=c).border = border_thin

    row_g = gastos_row_start + 1
    total_gastos = 0

    for gasto in sobre.gastos.all():
        ws.cell(row=row_g, column=pagos_col_label).value = gasto.descripcion[:40]
        ws.cell(row=row_g, column=pagos_col_label).border = border_thin
        ws.cell(row=row_g, column=pagos_col_monto).value = gasto.monto
        ws.cell(row=row_g, column=pagos_col_monto).number_format = '#,##0'
        ws.cell(row=row_g, column=pagos_col_monto).alignment = align_right
        ws.cell(row=row_g, column=pagos_col_monto).border = border_thin
        total_gastos += gasto.monto
        row_g += 1

    ws.cell(row=row_g, column=pagos_col_label).value = "TOTAL GASTOS"
    ws.cell(row=row_g, column=pagos_col_label).font = font_header
    ws.cell(row=row_g, column=pagos_col_label).fill = fill_blue_light
    ws.cell(row=row_g, column=pagos_col_label).border = border_thin
    ws.cell(row=row_g, column=pagos_col_monto).value = total_gastos
    ws.cell(row=row_g, column=pagos_col_monto).font = Font(bold=True, size=11)
    ws.cell(row=row_g, column=pagos_col_monto).number_format = '#,##0'
    ws.cell(row=row_g, column=pagos_col_monto).alignment = align_right
    ws.cell(row=row_g, column=pagos_col_monto).fill = fill_blue_light
    ws.cell(row=row_g, column=pagos_col_monto).border = border_medium

    # ── DIFERENCIA FINAL ──────────────────────────────
    diff_row = row_g + 2
    ws.cell(row=diff_row, column=pagos_col_label).value = "DIFERENCIA FINAL"
    ws.cell(row=diff_row, column=pagos_col_label).font = Font(bold=True, size=12)
    ws.cell(row=diff_row, column=pagos_col_label).border = border_medium
    ws.cell(row=diff_row, column=pagos_col_monto).value = f"={get_column_letter(ventas_col + 1)}5 - {get_column_letter(pagos_col_monto)}{row_p} - {get_column_letter(pagos_col_monto)}{row_g}"
    ws.cell(row=diff_row, column=pagos_col_monto).fill = fill_yellow
    ws.cell(row=diff_row, column=pagos_col_monto).font = Font(bold=True, size=14)
    ws.cell(row=diff_row, column=pagos_col_monto).number_format = '#,##0;[Red]-#,##0'
    ws.cell(row=diff_row, column=pagos_col_monto).alignment = align_right
    ws.cell(row=diff_row, column=pagos_col_monto).border = border_medium

    # ── KM y Firma ────────────────────────────────────
    km_row = diff_row + 3
    ws.cell(row=km_row, column=1).value = "KM RECORRIDOS:"
    ws.cell(row=km_row, column=1).font = font_header
    if sobre.kilometraje_camion:
        ws.cell(row=km_row, column=2).value = sobre.kilometraje_camion
        ws.cell(row=km_row, column=2).fill = fill_yellow
        ws.cell(row=km_row, column=2).font = Font(bold=True)
        ws.cell(row=km_row, column=2).border = border_thin

    ws.cell(row=km_row + 3, column=2).value = "FIRMA RESPONSABLE"
    ws.cell(row=km_row + 3, column=2).font = font_header
    ws.cell(row=km_row + 4, column=2).value = "_" * 40
    ws.cell(row=km_row + 4, column=2).font = Font(size=14)

    # ── Anchos de columnas ────────────────────────────
    ws.column_dimensions['A'].width = 22
    for c in range(start_col + 1, end_col_balones + 1):
        ws.column_dimensions[get_column_letter(c)].width = 12
    ws.column_dimensions[get_column_letter(pagos_col_label)].width = 24
    ws.column_dimensions[get_column_letter(pagos_col_monto)].width = 16

    # Respuesta
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    tipo = "BODEGA" if sobre.tipo == 'bodega' else (sobre.trabajador.username.upper() or "CAM")
    filename = f"Sobre_{tipo}_{sobre.fecha.strftime('%d%m%Y')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    wb.save(response)
    return response


# ══════════════════════════════════════════════════════════════
# PUSH NOTIFICATIONS - VISTAS PARA SUSCRIPCIONES WEB PUSH
# ══════════════════════════════════════════════════════════════

@login_required
def push_subscribe(request):
    """
    Guarda o actualiza la suscripción push del usuario actual.
    Solo permite suscripciones a camioneros (por ahora).
    Endpoint: POST /push/subscribe/
    """
    # Log de debug
    audit_logger.info(f"PUSH_SUBSCRIBE_ATTEMPT | User: {request.user.username} | Rol: {request.user.rol} | Method: {request.method}")
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    
    # Solo camioneros pueden suscribirse (por ahora)
    if request.user.rol != 'camionero':
        audit_logger.warning(f"PUSH_SUBSCRIBE_DENIED | User: {request.user.username} | Rol: {request.user.rol} (no es camionero)")
        return JsonResponse({
            'success': False, 
            'error': f'Solo los camioneros pueden activar notificaciones (tu rol: {request.user.rol})'
        }, status=403)
    
    try:
        import json
        data = json.loads(request.body)
        
        endpoint = data.get('endpoint', '')[:500]  # Truncar a 500 caracteres (límite del modelo)
        keys = data.get('keys', {})
        p256dh = keys.get('p256dh')
        auth = keys.get('auth')
        
        audit_logger.info(f"PUSH_SUBSCRIBE_DATA | Endpoint length: {len(data.get('endpoint', ''))} | Has p256dh: {bool(p256dh)} | Has auth: {bool(auth)}")
        
        if not all([endpoint, p256dh, auth]):
            return JsonResponse({
                'success': False, 
                'error': 'Datos de suscripción incompletos'
            }, status=400)
        
        from .models import PushSubscription
        
        # Crear o actualizar suscripción
        subscription, created = PushSubscription.objects.update_or_create(
            usuario=request.user,
            endpoint=endpoint,
            defaults={
                'p256dh': p256dh,
                'auth': auth,
                'activa': True,
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500]
            }
        )
        
        action = 'creada' if created else 'actualizada'
        audit_logger.info(f"PUSH_SUBSCRIBE | User: {request.user.username} | Suscripción {action}")
        
        return JsonResponse({
            'success': True,
            'message': f'Suscripción {action} correctamente',
            'subscription_id': subscription.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)
    except Exception as e:
        audit_logger.error(f"PUSH_SUBSCRIBE_ERROR | User: {request.user.username} | Error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Error interno del servidor'}, status=500)


# Usuario: desuscribirse de notificaciones
@login_required
def push_unsubscribe(request):
    """Desregistrar endpoint de suscripción."""
    """
    Desactiva la suscripción push del usuario actual.
    Endpoint: POST /push/unsubscribe/
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    
    try:
        import json
        data = json.loads(request.body)
        endpoint = data.get('endpoint')
        
        from .models import PushSubscription
        
        if endpoint:
            # Desactivar suscripción específica
            updated = PushSubscription.objects.filter(
                usuario=request.user,
                endpoint=endpoint
            ).update(activa=False)
        else:
            # Desactivar todas las suscripciones del usuario
            updated = PushSubscription.objects.filter(
                usuario=request.user
            ).update(activa=False)
        
        audit_logger.info(f"PUSH_UNSUBSCRIBE | User: {request.user.username} | {updated} suscripciones desactivadas")
        
        return JsonResponse({
            'success': True,
            'message': 'Notificaciones desactivadas',
            'count': updated
        })
        
    except Exception as e:
        audit_logger.error(f"PUSH_UNSUBSCRIBE_ERROR | User: {request.user.username} | Error: {str(e)}")
        return JsonResponse({'success': False, 'error': 'Error interno del servidor'}, status=500)


# Usuario: probar enviar notificación push
@login_required
def push_test(request):
    """Enviar notificación de prueba al usuario actual."""
    """
    Envía una notificación de prueba al usuario actual.
    Solo para testing/debugging.
    Endpoint: POST /push/test/
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    
    from .push_notifications import test_push_notification
    
    success, message = test_push_notification(request.user)
    
    return JsonResponse({
        'success': success,
        'message': message
    })


# Usuario: ver estado de suscripción push
@login_required  
def push_status(request):
    """Obtener estado actual de suscripción push del usuario."""
    """
    Retorna el estado de las suscripciones push del usuario actual.
    Endpoint: GET /push/status/
    """
    from .models import PushSubscription
    from django.conf import settings
    
    suscripciones = PushSubscription.objects.filter(
        usuario=request.user,
        activa=True
    )
    
    return JsonResponse({
        'success': True,
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', None),
        'has_subscriptions': suscripciones.exists(),
        'subscription_count': suscripciones.count(),
        'subscriptions': [
            {
                'id': s.id,
                'device': 'Móvil' if 'Mobile' in s.user_agent else 'Desktop',
                'created': s.creada_el.isoformat()
            }
            for s in suscripciones
        ]
    })


# PWA: servir service worker para notificaciones offline
def service_worker(request):
    """Archivo service worker para soporte de notificaciones push del navegador."""
    """
    Sirve el Service Worker desde la raíz del sitio.
    Esto es necesario para que el SW tenga scope '/' y pueda
    manejar notificaciones push en todo el sitio.
    """
    import os
    from django.conf import settings as django_settings
    
    # Leer el archivo sw.js desde static
    sw_path = os.path.join(django_settings.BASE_DIR, 'mockups', 'static', 'sw.js')
    
    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            sw_content = f.read()
    except FileNotFoundError:
        return HttpResponse('Service Worker not found', status=404)
    
    response = HttpResponse(sw_content, content_type='application/javascript')
    # Headers importantes para Service Workers
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
