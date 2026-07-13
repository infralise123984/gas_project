"""Vistas HTTP — auth."""

import io
import logging

import pyotp
import segno
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from django_ratelimit.decorators import ratelimit

from mockups.forms import (
    Activar2FAConfirmForm,
    CambiarPasswordForm,
    CrearUsuarioSeguroForm,
    Desactivar2FAForm,
    EditarPerfilForm,
    Verificar2FAForm,
)
from mockups.models import (
    AuditoriaAccion,
    Usuario,
)
from mockups.utils.fechas import (
    now_chile,
)
from mockups.utils.permisos import get_client_ip, require_roles

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')




# ══════════════════════════════════════════════════════════════
# 3. AUTENTICACIÓN Y VISTAS PRINCIPALES
# ══════════════════════════════════════════════════════════════

# Página de inicio del sistema
@login_required
def index(request):
    """Dashboard principal. Muestra opciones según rol del usuario."""
    context = {
        'hora_servidor': now_chile().isoformat(),
    }
    return render(request, "index.html", context)



# Autenticación: inicio de sesión
@never_cache
def login_view(request):
    """Autentica usuario, registra intento en auditoría."""
    if request.user.is_authenticated:
        return redirect("index")

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")

        # Obtener IP para logging y auditoría
        ip = get_client_ip(request)

        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.totp_activo:
                # 2FA requerido: rotar session key (mitiga session fixation) y guardar estado pendiente
                request.session.cycle_key()
                request.session['2fa_pending_user_id'] = user.pk
                # Guardar el backend para poder llamar login() luego sin authenticate()
                request.session['2fa_pending_backend'] = user.backend
                request.session['2fa_next'] = request.POST.get('next', '')
                # Timestamp para expirar el paso 2FA en 10 minutos
                request.session['2fa_pending_at'] = timezone.now().timestamp()
                audit_logger.info(f"2FA_REQUIRED | User: {username} | IP: {ip}")
                return redirect("auth_verificar_2fa")

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
            
            # Redirigir a ?next= si se especificó, si no al index
            next_url = request.POST.get('next', '') or request.GET.get('next', '')
            if next_url:
                return redirect(next_url)
            return redirect("index")
        else:
            messages.error(request, "Usuario o contraseña incorrectos")
            
            # Auditoría: Login fallido
            AuditoriaAccion.registrar(
                request=request,
                tipo='LOGIN_FAIL',
                descripcion=f'Intento de login fallido para usuario: {username}'
            )
            security_logger.warning(f"LOGIN_FAIL | User: {username} | IP: {ip}")

    return render(request, "auth/login.html")



# Autenticación: cierre de sesión
def logout_view(request):
    """Cierra sesión y registra acción en auditoría."""
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

    # Expirar el estado pendiente si han pasado más de 10 minutos desde el factor 1
    pending_at = request.session.get('2fa_pending_at', 0)
    if (timezone.now().timestamp() - pending_at) > 600:
        for k in ('2fa_pending_user_id', '2fa_pending_backend', '2fa_next', '2fa_intentos', '2fa_pending_at'):
            request.session.pop(k, None)
        messages.error(request, "La sesión de verificación expiró. Inicia sesión nuevamente.")
        return redirect("auth_login")

    form = Verificar2FAForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        # Control de intentos 2FA por sesión para frenar fuerza bruta del segundo factor
        intentos_2fa = request.session.get('2fa_intentos', 0)
        if intentos_2fa >= 5:
            ip_2fa = get_client_ip(request)
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

        ip = get_client_ip(request)

        if valido:
            user.totp_ultimo_verificado = ahora
            user.save(update_fields=['totp_ultimo_verificado'])

            backend = request.session.pop('2fa_pending_backend', None)
            del request.session['2fa_pending_user_id']
            request.session.pop('2fa_intentos', None)  # Limpiar contador al autenticar

            if not backend:
                messages.error(request, "La sesión de verificación expiró. Inicia sesión nuevamente.")
                security_logger.warning(f"2FA_BACKEND_MISSING | User: {user.username} | IP: {ip}")
                return redirect("auth_login")

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

    # Generar secreto temporal solo si no existe en sesión (evita invalidar QR ya escaneado al recargar)
    if '2fa_setup_secret' not in request.session:
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
                if data.get('bodega'):
                    user.bodega = data['bodega']
                user.save(update_fields=['is_active', 'bodega'])

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
                    'bodega': user.bodega.nombre if user.bodega else None,
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
            f"USER_CREATE_REJECTED | By: {request.user.username} | "
            f"Username: {attempted_username or '-'} | Errors: {list(form.errors.keys())}"
        )

    return render(request, "auth/crear_usuario.html", {"form": form})



# ──────────────────────────────────────────────────────────────
# BODEGA SWITCHER (admin / jefe)
# ──────────────────────────────────────────────────────────────

@login_required
def cambiar_bodega(request):
    """Cambia la bodega activa en sesión para admin/jefe."""
    bodega_id = request.GET.get('bodega_id')
    next_url = request.GET.get('next', '/')

    if request.user.rol not in ('admin', 'jefe'):
        messages.error(request, "No tienes permiso para cambiar de bodega.")
        return redirect(next_url)

    if bodega_id:
        from mockups.models import Bodega
        bodega = Bodega.objects.filter(id=bodega_id, activo=True).first()
        if bodega:
            request.session['active_bodega_id'] = bodega.id
            messages.info(request, f"Bodega cambiada a: {bodega.nombre}")
        else:
            messages.warning(request, "Bodega no encontrada o inactiva.")
    else:
        # Sin bodega_id → ver todas (solo admin)
        if 'active_bodega_id' in request.session:
            del request.session['active_bodega_id']
            messages.info(request, "Mostrando datos de todas las bodegas.")

    return redirect(next_url)


# ──────────────────────────────────────────────────────────────
# VISTA DE RATE-LIMIT (429 Too Many Requests)
# ──────────────────────────────────────────────────────────────

def rate_limited_view(request, exception=None):
    """Renderiza una página 429 amigable cuando se excede el rate limit."""
    from django.http import HttpResponseTooManyRequests
    from django.shortcuts import render
    return HttpResponseTooManyRequests(
        render(request, "429.html", status=429).content
    )

