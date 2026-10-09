"""Catálogos y metadatos del API v1 (docs/API_MOVIL.md §7.5)."""

from django.views.decorators.cache import never_cache

from mockups.api.acceso import ROLES_CAMIONERO, acceso_api
from mockups.api.limites import limitar
from mockups.api.respuestas import ok
from mockups.api.serializadores import serializar_balon
from mockups.services.catalogos import get_balones_activos_ordenados

VERSION_MINIMA = '0.1.0'

# Sin hosting definido todavía: no hay carpeta `apk/` ni ruta que la sirva, y el
# filesystem de Render es efímero (docs/API_MOVIL.md §7.5). Se devuelve None en
# vez de una URL que daría 404; la app debe tolerarlo.
URL_APK = None


@never_cache
@acceso_api()
@limitar('version')
def version_api(request):
    """Versión mínima exigida y origen del APK.

    Exige sesión: sin ella, cualquiera podría sondear la ruta de distribución.
    """
    return ok({'version_minima': VERSION_MINIMA, 'url_apk': URL_APK})


@never_cache
@acceso_api(ROLES_CAMIONERO)
@limitar('balones')
def listar_balones(request):
    """Catálogo vigente para la venta en la calle (§7.5).

    Solo activos y en el orden que usan los sobres: es la misma consulta que
    hace la web (``get_balones_activos_ordenados``), así que la app no puede
    ofrecer algo que la web ya no ofrece.
    """
    return ok([serializar_balon(balon) for balon in get_balones_activos_ordenados()])
