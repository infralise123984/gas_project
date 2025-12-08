# mockups/admin.py → Versión limpia y 100% funcional (sin formateo de precios)

from django.contrib import admin
from django.utils.html import format_html
from .models import Usuario, TipoBalon, HistorialPrecioBalon, Pedido


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "get_full_name",
        "rol",
        "email",
        "telefono",
        "is_active",
    )
    list_filter = ("rol", "is_active")
    search_fields = ("username", "first_name", "last_name", "email", "telefono")
    ordering = ("-date_joined",)

    def get_full_name(self, obj):
        return obj.get_full_name() or "—"

    get_full_name.short_description = "Nombre"


@admin.register(TipoBalon)
class TipoBalonAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "peso_neto_gas",
        "precio",
        "activo",
        "actualizado_el",
        "actualizado_por",
    )
    list_editable = ("precio", "activo")  # ← sigue funcionando perfecto
    list_filter = ("activo", "peso_neto_gas")
    search_fields = ("nombre", "peso_neto_gas")
    ordering = ("peso_neto_gas",)
    readonly_fields = ("actualizado_el",)

    def nombre(self, obj):
        return obj.nombre or "Sin nombre"

    nombre.short_description = "Nombre comercial"


@admin.register(HistorialPrecioBalon)
class HistorialPrecioBalonAdmin(admin.ModelAdmin):
    list_display = (
        "tipo_balón",
        "peso_kg",
        "precio_anterior",
        "precio_nuevo",
        "fecha_cambio",
        "cambiado_por",
    )
    list_filter = ("tipo_balón__peso_neto_gas", "fecha_cambio")
    search_fields = ("tipo_balón__nombre",)
    ordering = ("-fecha_cambio",)

    def peso_kg(self, obj):
        if obj.tipo_balón and obj.tipo_balón.peso_neto_gas:
            return f"{obj.tipo_balón.peso_neto_gas} kg"
        return "—"

    peso_kg.short_description = "Peso neto gas"


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "fecha",
        "balon_nombre",
        "peso_kg",
        "cantidad_balon",
        "monto",
        "estado",
        "registrador",
    )
    list_filter = ("estado", "balon__peso_neto_gas", "fecha")
    search_fields = ("balon__nombre", "registrador__username")
    ordering = ("-fecha",)

    def fecha(self, obj):
        return obj.fecha.strftime("%d/%m/%Y %H:%M")

    fecha.short_description = "Fecha"

    def balon_nombre(self, obj):
        return obj.balon.nombre

    balon_nombre.short_description = "Balón"

    def peso_kg(self, obj):
        return f"{obj.balon.peso_neto_gas} kg"

    peso_kg.short_description = "Peso gas"
