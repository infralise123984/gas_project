"""Autenticación del API v1 (docs/API_MOVIL.md §6.1).

Se reutiliza la sesión de Django: no hay tokens ni JWT, y no se toca
``mockups/views/auth.py`` (el de la web).
"""

import logging
from datetime import timedelta

from axes.handlers.proxy import AxesProxyHandler
from axes.helpers import get_credentials
from axes.models import AccessAttempt
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.cache import never_cache

from mockups.api import respuestas
from mockups.api.acceso import acceso_api, cuerpo_json, solo_post
from mockups.api.limites import limitar
from mockups.api.serializadores import serializar_usuario
from mockups.models import AuditoriaAccion
from mockups.utils.permisos import get_client_ip

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')

CAMPOS_LOGIN = ('username', 'password')


def _segundos_para_desbloqueo(request):
    """Segundos que faltan para que django-axes libere al cliente.

    Se calcula con el intento más antiguo de esa IP, porque el bloqueo se libera
    cuando esos intentos salen de la ventana de ``AXES_COOLOFF_TIME``. Sin datos
    utilizables se devuelve la ventana completa (estimación conservadora).
    """
    horas = getattr(settings, 'AXES_COOLOFF_TIME', None)
    if not horas:
        return None

    ventana = timedelta(hours=float(horas))
    ventana_completa = max(1, int(ventana.total_seconds()))

    try:
        ip = getattr(request, 'axes_ip_address', None)  # lo fija AxesProxyHandler
        if not ip:
            return ventana_completa
        mas_antiguo = (
            AccessAttempt.objects.filter(ip_address=ip)
            .order_by('attempt_time')
            .first()
        )
        if mas_antiguo is None:
            return ventana_completa
        restante = (mas_antiguo.attempt_time + ventana) - timezone.now()
        return max(1, int(restante.total_seconds()))
    except Exception:
        return ventana_completa


def _respuesta_bloqueado(request, username):
    headers = {}
    espera = _segundos_para_desbloqueo(request)
    if espera:
        # La app no debe reintentar antes de este plazo: cada intento previo
        # extiende la ventana.
        headers['Retry-After'] = str(espera)
    security_logger.warning(
        f'API_LOGIN_BLOQUEADO | User: {username} | IP: {get_client_ip(request)}'
    )
    return respuestas.error('bloqueado_login', headers=headers)


@never_cache
def entregar_csrf(request):
    """Entrega el token CSRF que la app debe reenviar en ``X-CSRFToken``.

    Esta ruta es imprescindible: ``CsrfViewMiddleware`` exige el doble envío
    (cookie + cabecera) en todo método no seguro, y el resto del API requiere
    sesión previa, así que sin esto el cliente no tendría de dónde sacar el
    token antes del primer login.
    """
    return respuestas.ok({'csrf_token': get_token(request)})


@never_cache
@solo_post
def login_api(request):
    """Autentica y abre sesión. Responde en el sobre del §5.2/§5.1."""
    datos, respuesta_error = cuerpo_json(request, permitidos=CAMPOS_LOGIN)
    if respuesta_error is not None:
        return respuesta_error

    username = datos.get('username')
    password = datos.get('password')
    if (
        not isinstance(username, str)
        or not isinstance(password, str)
        or not username.strip()
        or not password
    ):
        return respuestas.error(
            'validacion', mensaje='Usuario y contraseña son obligatorios.'
        )
    username = username.strip()

    credenciales = get_credentials(username=username, password=password)

    # Con el proyecto sin configurar, el bloqueo de axes es por IP
    # (AXES_LOCKOUT_PARAMETERS = ['ip_address']). Se consulta ANTES de
    # autenticar para poder responder el sobre del §5.2 en vez del 429 en texto
    # plano de axes, y para no extender la ventana con reintentos del API.
    if not AxesProxyHandler.is_allowed(request, credenciales):
        return _respuesta_bloqueado(request, username)

    user = authenticate(request, username=username, password=password)

    # axes marca la petición cuando ESTE intento alcanzó el límite; su
    # middleware reemplazaría nuestra respuesta al volver. Se responde aquí, con
    # el sobre, y se limpia la marca para que no la sobrescriba.
    if getattr(request, 'axes_locked_out', False):
        request.axes_locked_out = False
        return _respuesta_bloqueado(request, username)

    if user is None:
        AuditoriaAccion.registrar(
            request=request,
            tipo='LOGIN_FAIL',
            descripcion=f'Intento de login fallido para usuario: {username} (API)',
        )
        security_logger.warning(
            f'LOGIN_FAIL_API | User: {username} | IP: {get_client_ip(request)}'
        )
        # 401 con `no_autenticado`: el código del contrato que la app ya conoce.
        # El mensaje distingue credenciales malas de sesión ausente.
        return respuestas.error(
            'no_autenticado', mensaje='Usuario o contraseña incorrectos.'
        )

    if user.totp_activo:
        # Decisión del dueño (docs/API_MOVIL.md §6.1): la app aún no soporta 2FA.
        # No se abre sesión ni se toca la sesión anónima: no hay fixation que
        # mitigar porque nunca se llama a login().
        audit_logger.info(
            f'2FA_REQUIRED_API | User: {username} | IP: {get_client_ip(request)}'
        )
        return respuestas.ok({'requiere_2fa': True}, status=202)

    login(request, user)
    AuditoriaAccion.registrar(
        request=request,
        tipo='LOGIN_OK',
        descripcion=f'Inicio de sesión exitoso para {username} (API)',
        objeto=user,
    )
    audit_logger.info(
        f'LOGIN_OK_API | User: {username} | IP: {get_client_ip(request)}'
    )
    return respuestas.ok({'usuario': serializar_usuario(user)})


@never_cache
@solo_post
@acceso_api()
def logout_api(request):
    """Cierra la sesión. Solo POST, igual que la web (evita logout por GET)."""
    usuario = request.user
    AuditoriaAccion.registrar(
        request=request,
        tipo='LOGOUT',
        descripcion=f'Cierre de sesión para {usuario.username} (API)',
        objeto=usuario,
    )
    audit_logger.info(f'LOGOUT_API | User: {usuario.username}')
    logout(request)
    return respuestas.ok({'sesion_cerrada': True})


@never_cache
@acceso_api()
@limitar('perfil', peticiones=30, ventana_segundos=60)
def perfil_api(request):
    """Identidad del token de sesión + token CSRF para las siguientes llamadas.

    Sin filtro de rol a propósito: es el arranque de identidad, no un dato de
    negocio. Los endpoints de negocio sí exigen ``['camionero']``.
    """
    return respuestas.ok(
        {
            'usuario': serializar_usuario(request.user),
            'csrf_token': get_token(request),
        }
    )
