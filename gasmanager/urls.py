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
    path('admin/', admin.site.urls),

    path("", views.index, name="index"),                                   # ← le puse nombre (opcional pero recomendado)

    path('login/', views.login_view, name='login'),      # ← NUEVA RUTA (cambia de login_view/ → login/)
    path('logout/', views.logout_view, name='logout'),  # ← NUEVA RUTA (para cerrar sesión)

    # Tus rutas originales (sin cambios, solo les agregué nombres)
    # path('mantenedor_clientes/', views.mantenedor_clientes, name='mantenedor_clientes'),
    path('consultas_pedidos/', views.consultas_pedidos, name='consultas_pedidos'),
    path('transaccional_pedido/', views.transaccional_pedido, name='transaccional_pedido'),
    path('reporte_ventas/', views.reporte_ventas, name='reporte_ventas'),
    path('cliente_pedido/', views.cliente_pedido, name='cliente_pedido'),
    path('camionero_entregas/', views.camionero_entregas, name='camionero_entregas'),
    path('precios/', views.precios_balones, name='precios_balones'),
]