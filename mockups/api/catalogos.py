"""Catálogos y metadatos del API v1 (docs/API_MOVIL.md §7.5)."""

from django.views.decorators.cache import never_cache

from mockups.api.acceso import acceso_api
from mockups.api.limites import limitar
from mockups.api.respuestas import ok

VERSION_MINIMA = '0.1.0'

# Sin hosting definido todavía: no hay carpeta `apk/` ni ruta que la sirva, y el
# filesystem de Render es efímero (docs/API_MOVIL.md §7.5). Se devuelve None en
# vez de una URL que daría 404; la app debe tolerarlo.
URL_APK = None


@never_cache
@acceso_api()
@limitar('version', peticiones=10, ventana_segundos=60)
def version_api(request):
    """Versión mínima exigida y origen del APK.

    Exige sesión: sin ella, cualquiera podría sondear la ruta de distribución.
    """
    return ok({'version_minima': VERSION_MINIMA, 'url_apk': URL_APK})
