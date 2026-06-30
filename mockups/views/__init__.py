"""
Vistas HTTP de mockups — paquete modular.

Respaldo del monolito: mockups/views_monolith_backup.py
"""

from mockups.views.auth import *  # noqa: F403
from mockups.views.catalogos import *  # noqa: F403
from mockups.views.entregas import *  # noqa: F403
from mockups.views.pedidos import *  # noqa: F403
from mockups.views.push import *  # noqa: F403
from mockups.views.reportes import *  # noqa: F403
from mockups.views.sobres import *  # noqa: F403

from mockups.services.exports import exportar_pedidos_excel  # noqa: F401
from mockups.services.camionero import (  # noqa: F401
    _kilos_de_pedido,
    _stats_dia_camionero,
    stats_ventas_camionero,
)
from mockups.services.sobres import get_pedidos_queryset_para_sobre, sincronizar_sobre_desde_pedidos  # noqa: F401
from mockups.services.catalogos import get_balones_activos_ordenados  # noqa: F401
from mockups.utils.fechas import (  # noqa: F401
    MESES_ES_CAMIONERO,
    get_rango_utc_para_fecha,
    navegacion_mes,
    now_chile,
    parse_fecha_rango,
    parse_mes_param,
    rango_dia_chile,
    today_chile,
)
from mockups.utils.permisos import get_client_ip, get_display_name, require_roles, require_roles_api, get_bodega_actual, filtrar_por_bodega  # noqa: F401