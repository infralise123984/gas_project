"""Panel de monitoreo de la flota (docs/PLAN_MONITOREO_FLOTA.md).

La vista es **transporte**: decide quién entra y entrega lo que arma
``services.flota``. Ni consulta de pedidos ni cálculo de tramos acá.

Por qué existe este panel: con los GPS arrendados que la empresa tuvo antes, la
telefonista veía quién llevaba rato parado y lo llamaba; las ventas de tarreo
subieron. Al quedarse sin los aparatos, bajaron. Esto repone esa lectura, sin
notificaciones ni alertas automáticas: alguien mira y decide.
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from mockups.services.flota import ROLES_PANEL_FLOTA, resumen_flota
from mockups.utils.fechas import now_chile
from mockups.utils.permisos import require_roles, require_roles_api

MENSAJE_SIN_PERMISO = 'El panel de flota es para telefonistas, jefes y administradores.'


@login_required
@never_cache
def flota_panel(request):
    """Mapa y bitácora de los camiones de la jornada en curso."""
    denegado = require_roles(request, ROLES_PANEL_FLOTA, 'index', MENSAJE_SIN_PERMISO)
    if denegado is not None:
        return denegado

    return render(request, 'flota/panel.html', {'roles_panel': ROLES_PANEL_FLOTA})


@login_required
@never_cache
@require_GET
def flota_api(request):
    """Estado de la flota en JSON, para el refresco periódico del panel.

    Se responde con el **sobre de la web** (no el del API móvil): quien lo
    consume es JavaScript de esta misma página, no un cliente externo.
    """
    denegado = require_roles_api(request, ROLES_PANEL_FLOTA)
    if denegado is not None:
        return denegado

    return JsonResponse(
        {
            'servidor_ahora': now_chile().isoformat(),
            # El umbral viaja con los datos: el color del panel cambia ajustando
            # la configuración, sin tocar el JavaScript.
            'minutos_detenido': settings.UBICACION_MINUTOS_DETENIDO,
            'camiones': resumen_flota(),
        }
    )
