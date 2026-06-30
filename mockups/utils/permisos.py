"""Utilidades de permisos y auditoría de acceso."""

import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from mockups.models import AuditoriaAccion

security_logger = logging.getLogger('security')


def get_client_ip(request):
    """Obtiene IP de cliente para auditoría de forma consistente."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    return x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')


def require_roles(request, roles, redirect_to="index", message="No tienes permiso para acceder a esta sección."):
    """Validar roles. Retorna None si está autorizado; si no, redirige y audita."""
    if request.user.rol not in roles:
        messages.error(request, message)
        AuditoriaAccion.registrar(
            request=request,
            tipo='PERM_DENIED',
            descripcion=(
                f'Acceso denegado a {request.path}. '
                f'Rol requerido: {roles}. Rol actual: {request.user.rol}'
            ),
        )
        security_logger.warning(
            f"PERM_DENIED | User: {request.user.username} | Path: {request.path} | "
            f"Required: {roles} | Has: {request.user.rol}"
        )
        return redirect(redirect_to)
    return None


def require_roles_api(request, roles):
    """Validar roles para endpoints JSON/AJAX.

    Retorna None si está autorizado; si no, retorna JsonResponse 403 y audita.
    A diferencia de require_roles(), NO redirige — devuelve JSON para el cliente.
    """
    if request.user.rol not in roles:
        AuditoriaAccion.registrar(
            request=request,
            tipo='PERM_DENIED',
            descripcion=(
                f'Acceso denegado a {request.path} (API). '
                f'Rol requerido: {roles}. Rol actual: {request.user.rol}'
            ),
        )
        security_logger.warning(
            f"PERM_DENIED_API | User: {request.user.username} | Path: {request.path} | "
            f"Required: {roles} | Has: {request.user.rol}"
        )
        return JsonResponse({'error': 'No autorizado'}, status=403)
    return None


def get_display_name(user):
    """Obtiene nombre completo o username. Retorna '—' si no existe."""
    if not user:
        return "—"
    nombre = (user.get_full_name() or "").strip()
    return nombre if nombre else user.username


def get_bodega_actual(request):
    """Retorna la Bodega activa según el contexto del usuario.

    - admin (bodega=None): lee 'active_bodega_id' de la sesión
    - jefe: su propia bodega asignada
    - otros roles: request.user.bodega directamente
    Retorna None si no hay bodega seleccionada (admin sin sesión activa).
    """
    if request.user.rol == 'admin' and request.user.bodega is None:
        bodega_id = request.session.get('active_bodega_id')
        if bodega_id:
            from mockups.models import Bodega
            return Bodega.objects.filter(id=bodega_id, activo=True).first()
        return None
    return request.user.bodega


def filtrar_por_bodega(qs, request):
    """Aplica filtro de bodega a un queryset.

    Si get_bodega_actual() retorna None (admin sin selección),
    NO filtra — muestra todo. En cualquier otro caso, filtra.
    """
    bodega = get_bodega_actual(request)
    if bodega is not None:
        return qs.filter(bodega=bodega)
    return qs