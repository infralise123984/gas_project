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
    # Autenticación y página principal
    # ────────────────────────────────────────────────
    path("", views.index, name="index"),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('crear_usuario/', views.crear_usuario, name='crear_usuario'),
    # ────────────────────────────────────────────────
    # Registro y edición de pedidos (transaccional)
    # ────────────────────────────────────────────────
    path('transaccional_pedido/', views.transaccional_pedido, name='transaccional_pedido'),
    path('pedido/<int:pedido_id>/', views.detalle_pedido, name='detalle_pedido'),
    # ← Nueva ruta añadida para edición de pedidos (manteniendo consistencia de nombres)
    path('editar_pedido/<int:pedido_id>/', views.editar_pedido, name='editar_pedido'),

    # ────────────────────────────────────────────────
    # Vistas específicas por rol
    # ────────────────────────────────────────────────
    # Telefonista / Bodeguero
    path('mis-pedidos-hoy/', views.mis_pedidos_hoy, name='mis_pedidos_hoy'),

    # Camionero
    path("camionero/", views.camionero_entregas, name="camionero_entregas"),
    path('mis-entregas-camionero/', views.mis_entregas_camionero, name='mis_entregas'),
    path("camionero/tomar/<int:pedido_id>/", views.camionero_tomar_pedido, name="camionero_tomar_pedido"),
    path("camionero/entregado/<int:pedido_id>/", views.camionero_marcar_entregado, name="camionero_marcar_entregado"),
    path('camionero/cancelar/<int:pedido_id>/', views.camionero_cancelar_entrega, name='camionero_cancelar_entrega'),
    path('tarreo/', views.tarreo_pedido, name='tarreo_pedido'),

    # ────────────────────────────────────────────────
    # Reportes y gestión administrativa (Jefe / Admin)
    # ────────────────────────────────────────────────
    path('reporte_ventas/', views.reporte_ventas, name='reporte_ventas'),
    path('reporte/sobres/', views.reporte_sobres, name='reporte_sobres'),
    path('precios/', views.precios_balones, name='precios_balones'),
    path('consultas_pedidos/', views.consultas_pedidos, name='consultas_pedidos'),
    path('historial-precios/', views.historial_precios, name='historial_precios'),
    # Nota: esta ruta duplicada la dejamos comentada para evitar confusión
    # path('consultas-pedidos/', views.consultas_pedidos, name='consultas_pedidos'),  # ← duplicada, usar la de arriba

    # ────────────────────────────────────────────────
    # Gestión de sobres diarios (cierre de caja)
    # ────────────────────────────────────────────────
    path('sobres/', views.lista_sobres_diarios, name='lista_sobres_diarios'),
    path('sobres/editar/', views.editar_sobre_diario, name='editar_sobre_diario'),
    path('historial-sobres/', views.historial_sobres, name='historial_sobres'),
    path('sobres/exportar/<int:sobre_id>/', views.exportar_sobre_excel, name='exportar_sobre_excel'),
]