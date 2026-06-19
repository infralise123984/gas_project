"""
URL configuration for gasmanager project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from mockups import views

urlpatterns = [
    # ────────────────────────────────────────────────
    # Admin y acceso general
    # ────────────────────────────────────────────────
    path('admin/', admin.site.urls),

    # ────────────────────────────────────────────────
    # AUTENTICACIÓN Y PÁGINA PRINCIPAL
    # ────────────────────────────────────────────────
    path("", views.index, name="index"),
    path('auth/login/', views.login_view, name='auth_login'),
    path('auth/logout/', views.logout_view, name='auth_logout'),
    path('auth/perfil/', views.perfil_view, name='auth_perfil'),
    path('auth/crear-usuario/', views.crear_usuario, name='auth_crear_usuario'),
    path('auth/verificar-2fa/', views.verificar_2fa_view, name='auth_verificar_2fa'),
    path('auth/activar-2fa/', views.activar_2fa_view, name='auth_activar_2fa'),
    path('auth/desactivar-2fa/', views.desactivar_2fa_view, name='auth_desactivar_2fa'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE PEDIDOS
    # ────────────────────────────────────────────────
    path('pedidos/crear/', views.transaccional_pedido, name='pedidos_crear'),
    path('pedidos/<int:pedido_id>/', views.detalle_pedido, name='pedidos_detalle'),
    path('pedidos/<int:pedido_id>/editar/', views.editar_pedido, name='pedidos_editar'),
    path('pedidos/mios/', views.mis_pedidos_hoy, name='pedidos_mios'),
    path('pedidos/mios/api/', views.mis_pedidos_hoy_api, name='pedidos_mios_api'),
    path('pedidos/<int:pedido_id>/cancelar/', views.telefonista_cancelar_pedido, name='pedidos_cancelar'),
    path('pedidos/consulta/', views.consultas_pedidos, name='pedidos_consulta'),

    # ────────────────────────────────────────────────
    # ENTREGAS Y DISTRIBUCIÓN (Camionero)
    # ────────────────────────────────────────────────
    path('entregas/', views.camionero_entregas, name='entregas_lista'),
    path('entregas/api/', views.camionero_entregas_api, name='entregas_api'),
    path('entregas/mias/', views.mis_entregas_camionero, name='entregas_mias'),
    path('entregas/mias/api/', views.mis_entregas_camionero_api, name='entregas_mias_api'),
    path('entregas/historial/', views.camionero_historial, name='entregas_historial'),
    path('entregas/historial/dia/<str:fecha>/', views.camionero_historial_dia, name='entregas_historial_dia'),
    path('entregas/<int:pedido_id>/tomar/', views.camionero_tomar_pedido, name='entregas_tomar'),
    path('entregas/<int:pedido_id>/entregado/', views.camionero_marcar_entregado, name='entregas_entregado'),
    path('entregas/<int:pedido_id>/cancelar/', views.camionero_cancelar_entrega, name='entregas_cancelar'),
    path('entregas/tarreo/', views.tarreo_pedido, name='entregas_tarreo'),

    # ────────────────────────────────────────────────
    # REPORTES Y ANÁLISIS
    # ────────────────────────────────────────────────
    path('reportes/ventas/', views.reporte_ventas, name='reportes_ventas'),
    path('reportes/sobres/', views.reporte_sobres, name='reportes_sobres'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE PRECIOS Y BALONES
    # ────────────────────────────────────────────────
    path('precios/lista/', views.gestionar_balones_lista, name='precios_lista'),  # Redirige a gestión integrada
    path('precios/historial/', views.historial_precios, name='precios_historial'),

    # ────────────────────────────────────────────────
    # AUDITORÍA
    # ────────────────────────────────────────────────
    path('auditoria/', views.auditoria_lista, name='auditoria_lista'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE BALONES (sin admin)
    # ────────────────────────────────────────────────
    path('balones/lista/', views.gestionar_balones_lista, name='balones_lista'),
    path('balones/crear/', views.gestionar_balones_crear, name='balones_crear'),
    path('balones/<int:balon_id>/editar/', views.gestionar_balones_editar, name='balones_editar'),
    path('balones/<int:balon_id>/eliminar/', views.gestionar_balones_eliminar, name='balones_eliminar'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE SECTORES (sin admin)
    # ────────────────────────────────────────────────
    path('sectores/lista/', views.gestionar_sectores_lista, name='sectores_lista'),
    path('sectores/crear/', views.gestionar_sectores_crear, name='sectores_crear'),
    path('sectores/<int:sector_id>/editar/', views.gestionar_sectores_editar, name='sectores_editar'),
    path('sectores/<int:sector_id>/eliminar/', views.gestionar_sectores_eliminar, name='sectores_eliminar'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE SOBRES Y CIERRE DE CAJA
    # ────────────────────────────────────────────────
    path('sobres/lista/', views.lista_sobres_diarios, name='sobres_lista'),
    path('sobres/editar/', views.editar_sobre_diario, name='sobres_editar'),
    path('sobres/<int:sobre_id>/refrescar/', views.refrescar_sobre_diario, name='sobres_refrescar'),
    path('sobres/historial/', views.historial_sobres, name='sobres_historial'),
    path('sobres/<int:sobre_id>/imprimir/', views.imprimir_sobre_diario, name='sobres_imprimir'),
    path('sobres/<int:sobre_id>/crear-nuevo/', views.crear_sobre_post_cierre, name='crear_sobre_post_cierre'),

    # ────────────────────────────────────────────────
    # PUSH NOTIFICATIONS (Web Push API)
    # ────────────────────────────────────────────────
    path('push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),
    path('push/test/', views.push_test, name='push_test'),
    path('push/status/', views.push_status, name='push_status'),
    
    # Service Worker debe servirse desde la raíz para tener scope completo
    path('sw.js', views.service_worker, name='service_worker'),
]