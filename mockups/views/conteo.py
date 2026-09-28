"""Vistas HTTP — conteo diario de balones (bodega)."""

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from mockups.forms import LineaConteoBalonFormSet
from mockups.models import AuditoriaAccion, ConteoDiarioBalon
from mockups.services.conteo import es_editable, obtener_o_crear_conteo, resumen
from mockups.utils.fechas import today_chile
from mockups.utils.permisos import require_roles

audit_logger = logging.getLogger('audit')

ROLES_CON = ['bodeguero']
MENSAJE_SIN_PERMISO = "Solo el bodeguero puede usar el conteo de balones."
MENSAJE_SECCION_OCULTA = "El conteo de balones está deshabilitado por ahora."


def _seccion_oculta(request):
    """Redirect si la sección está oculta por feature flag; None si está activa.

    La sección queda oculta hasta liberarla: se evalúa DESPUÉS del control de
    roles para no perder la auditoría de accesos indebidos.
    """
    if getattr(settings, "CONTEO_BALONES_HABILITADO", False):
        return None
    messages.warning(request, MENSAJE_SECCION_OCULTA)
    return redirect("index")


@login_required
def conteo_balones_lista(request):
    """Historial de conteos y acceso al conteo de hoy."""
    resp = require_roles(request, ROLES_CON, "index", MENSAJE_SIN_PERMISO)
    if resp:
        return resp

    resp = _seccion_oculta(request)
    if resp:
        return resp

    hoy = today_chile()

    return render(request, 'balones/conteo_lista.html', {
        'hoy': hoy,
        'conteos': (
            ConteoDiarioBalon.objects
            .select_related('creado_por')
            .annotate(num_lineas=Count('lineas'))
            .order_by('-fecha')[:30]
        ),
        'existe_hoy': ConteoDiarioBalon.objects.filter(fecha=hoy).exists(),
    })


@login_required
def conteo_balones_hoy(request):
    """Abre el conteo de hoy, creándolo si hace falta, y va a su detalle."""
    resp = require_roles(request, ROLES_CON, "index", MENSAJE_SIN_PERMISO)
    if resp:
        return resp

    resp = _seccion_oculta(request)
    if resp:
        return resp

    conteo, creado = obtener_o_crear_conteo(today_chile(), request.user)

    if creado:
        AuditoriaAccion.registrar(
            request=request,
            tipo='CONTEO_CREATE',
            descripcion=f"Conteo de balones {conteo.fecha} creado.",
            objeto=conteo,
        )
        messages.info(request, "Se creó el conteo de hoy con el saldo del día anterior.")

    return redirect('conteo_balones_detalle', conteo_id=conteo.id)


@login_required
def conteo_balones_detalle(request, conteo_id):
    """Ver el conteo y, si es del día de hoy, editarlo."""
    resp = require_roles(request, ROLES_CON, "index", MENSAJE_SIN_PERMISO)
    if resp:
        return resp

    resp = _seccion_oculta(request)
    if resp:
        return resp

    conteo = get_object_or_404(ConteoDiarioBalon, pk=conteo_id)
    editable = es_editable(conteo)

    if request.method == 'POST':
        if not editable:
            messages.error(
                request,
                "Este conteo ya no se puede editar: solo se edita el día que corresponde.",
            )
            return redirect('conteo_balones_detalle', conteo_id=conteo.id)

        formset = LineaConteoBalonFormSet(request.POST, instance=conteo)

        if formset.is_valid():
            with transaction.atomic():
                formset.save()
                AuditoriaAccion.registrar(
                    request=request,
                    tipo='CONTEO_UPDATE',
                    descripcion=f"Conteo de balones {conteo.fecha} actualizado.",
                    objeto=conteo,
                )
            messages.success(request, "Conteo guardado.")
            return redirect('conteo_balones_detalle', conteo_id=conteo.id)

        messages.error(request, "Revisa los valores marcados en rojo.")
    else:
        formset = LineaConteoBalonFormSet(instance=conteo)

    lineas = [form.instance for form in formset.forms]

    return render(request, 'balones/conteo_detalle.html', {
        'conteo': conteo,
        'formset': formset,
        'editable': editable,
        'resumen': resumen(lineas),
        'hoy': today_chile(),
    })
