"""
Vistas HTTP de mockups (empaquetado modular).

Migración en progreso: el código vive aún en _legacy.py y se re-exporta aquí
para que `from mockups import views` y gasmanager/urls.py sigan igual.

Respaldo congelado del monolito: mockups/views_monolith_backup.py
"""

from mockups.views._legacy import *  # noqa: F403
from mockups.views._legacy import (  # helpers con _ (no van en import *)
    _kilos_de_pedido,
    _stats_dia_camionero,
)