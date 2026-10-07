"""Rutas de /api/v1/ (docs/API_MOVIL.md §4).

Se incluye desde ``gasmanager/urls.py`` con una sola línea, al final:

    path('api/v1/', include('mockups.api.urls')),
"""

from django.urls import path

from mockups.api import auth, catalogos, entregas

app_name = 'api_v1'

urlpatterns = [
    # Arranque de identidad: sin sesión previa (necesario para el doble envío CSRF)
    path('auth/csrf/', auth.entregar_csrf, name='csrf'),
    path('auth/login/', auth.login_api, name='login'),

    # Con sesión
    path('auth/logout/', auth.logout_api, name='logout'),
    path('auth/perfil/', auth.perfil_api, name='perfil'),

    # Negocio (F1: solo lectura)
    path('entregas/', entregas.listar_entregas, name='entregas'),

    # Negocio (F2: las cuatro acciones del §7.3)
    path('entregas/<int:pedido_id>/tomar/', entregas.tomar_pedido, name='entregas_tomar'),
    path('entregas/<int:pedido_id>/entregar/', entregas.entregar_pedido, name='entregas_entregar'),
    path('entregas/<int:pedido_id>/cancelar/', entregas.cancelar_pedido, name='entregas_cancelar'),
    path('entregas/<int:pedido_id>/devolver/', entregas.devolver_pedido, name='entregas_devolver'),

    path('version/', catalogos.version_api, name='version'),
]
