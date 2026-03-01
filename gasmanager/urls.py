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
    path('auth/crear-usuario/', views.crear_usuario, name='auth_crear_usuario'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE PEDIDOS
    # ────────────────────────────────────────────────
    path('pedidos/crear/', views.transaccional_pedido, name='pedidos_crear'),
    path('pedidos/<int:pedido_id>/', views.detalle_pedido, name='pedidos_detalle'),
    path('pedidos/<int:pedido_id>/editar/', views.editar_pedido, name='pedidos_editar'),
    path('pedidos/mios/', views.mis_pedidos_hoy, name='pedidos_mios'),
    path('pedidos/consulta/', views.consultas_pedidos, name='pedidos_consulta'),

    # ────────────────────────────────────────────────
    # ENTREGAS Y DISTRIBUCIÓN (Camionero)
    # ────────────────────────────────────────────────
    path('entregas/', views.camionero_entregas, name='entregas_lista'),
    path('entregas/mias/', views.mis_entregas_camionero, name='entregas_mias'),
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
    # GESTIÓN DE PRECIOS
    # ────────────────────────────────────────────────
    path('precios/lista/', views.precios_balones, name='precios_lista'),
    path('precios/historial/', views.historial_precios, name='precios_historial'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE BALONES (sin admin)
    # ────────────────────────────────────────────────
    path('balones/lista/', views.gestionar_balones_lista, name='balones_lista'),
    path('balones/crear/', views.gestionar_balones_crear, name='balones_crear'),
    path('balones/<int:balon_id>/editar/', views.gestionar_balones_editar, name='balones_editar'),
    path('balones/<int:balon_id>/eliminar/', views.gestionar_balones_eliminar, name='balones_eliminar'),

    # ────────────────────────────────────────────────
    # GESTIÓN DE SOBRES Y CIERRE DE CAJA
    # ────────────────────────────────────────────────
    path('sobres/lista/', views.lista_sobres_diarios, name='sobres_lista'),
    path('sobres/editar/', views.editar_sobre_diario, name='sobres_editar'),
    path('sobres/historial/', views.historial_sobres, name='sobres_historial'),
    path('sobres/<int:sobre_id>/imprimir/', views.imprimir_sobre_diario, name='sobres_imprimir'),
    path('sobres/<int:sobre_id>/crear-nuevo/', views.crear_sobre_post_cierre, name='crear_sobre_post_cierre'),
]