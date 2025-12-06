# mockups/admin.py
# Admin COMPLETO y CORREGIDO para GasFácil - Rancagua 2025
# Compatible con el models.py final (TipoBalon, Pedido, Historial, etc.)
# SIN ERRORES de list_editable

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import Usuario, TipoBalon, HistorialPrecioBalon, Pedido


# ==============================================================
# 1. USUARIOS
# ==============================================================
@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "get_full_name",
        "rol",
        "email",
        "telefono",
        "is_active",
        "date_joined",
    )
    list_filter = ("rol", "is_active", "date_joined")
    search_fields = ("username", "first_name", "last_name", "email", "telefono")
    ordering = ("-date_joined",)

    def get_full_name(self, obj):
        return obj.get_full_name() or "—"

    get_full_name.short_description = "Nombre completo"


# ==============================================================
# 2. TIPOS DE BALÓN (Edición en línea perfecta)
# ==============================================================
@admin.register(TipoBalon)
class TipoBalonAdmin(admin.ModelAdmin):
    list_display = ("tamaño", "precio", "activo", "actualizado_el", "actualizado_por")
    list_editable = (
        "precio",
        "activo",
    )  # ← FUNCIONA porque precio y activo están en list_display
    list_filter = ("activo", "actualizado_el")
    search_fields = ("tamaño",)
    ordering = ("tamaño",)

    # Formateo bonito del precio
    def precio(self, obj):
        if not obj.activo:
            return format_html(
                '<span style="color: #999; text-decoration: line-through;">${:,}</span>',
                obj.precio,
            )
        return format_html(
            '<strong style="color: green; font-size: 1.1em;">${:,}</strong>', obj.precio
        )

    precio.short_description = "Precio actual (CLP)"
    precio.admin_order_field = "precio"

    # Checkbox bonito
    def activo(self, obj):
        color = "success" if obj.activo else "danger"
        texto = "Sí" if obj.activo else "No"
        return format_html('<span class="badge bg-{}">{}</span>', color, texto)

    activo.boolean = True
    activo.short_description = "Disponible"


# ==============================================================
# 3. HISTORIAL DE PRECIOS (solo lectura, auditoría)
# ==============================================================
@admin.register(HistorialPrecioBalon)
class HistorialPrecioBalonAdmin(admin.ModelAdmin):
    list_display = (
        "tipo_balón",
        "precio_anterior",
        # "flecha",
        "precio_nuevo",
        "fecha_cambio",
        "cambiado_por"
    )
    list_filter = ("tipo_balón", "fecha_cambio")
    search_fields = ("tipo_balón__tamaño",)
    readonly_fields = ("tipo_balón", "precio_anterior", "precio_nuevo", "fecha_cambio", "cambiado_por")
    ordering = ("-fecha_cambio",)
    list_per_page = 25

    def precio_anterior(self, obj):
        return f"${obj.precio_anterior:,}".replace(",", ".")
    precio_anterior.short_description = "Antes"

    def precio_nuevo(self, obj):
        return f"${obj.precio_nuevo:,}".replace(",", ".")
    precio_nuevo.short_description = "Ahora"

    def flecha(self, obj):
        if obj.precio_nuevo > obj.precio_anterior:
            return mark_safe('<span style="font-size:1.4em; color:red;">up arrow</span>')
        elif obj.precio_nuevo < obj.precio_anterior:
            return mark_safe('<span style="font-size:1.4em; color:green;">down arrow</span>')
        else:
            return mark_safe('<span style="color:gray;">right arrow</span>')
    flecha.short_description = "Cambio"


# ==============================================================
# 4. PEDIDOS (vista clara y útil)
# ==============================================================
@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "fecha_hora",
        "balon",
        "cantidad_balon",
        "monto_f",
        "origen_badge",
        "estado_badge",
        "sector_corto",
        "registrador",
    )
    list_filter = ("origen", "estado", "balon__tamaño", "fecha")
    search_fields = ("id", "sector", "direccion_entrega", "registrador__username")
    readonly_fields = ("fecha", "monto", "registrador")
    ordering = ("-fecha",)
    list_per_page = 25

    def fecha_hora(self, obj):
        return obj.fecha.strftime("%d/%m/%Y %H:%M")

    fecha_hora.short_description = "Fecha"

    def monto_f(self, obj):
        return f"${int(obj.monto):,}".replace(",", ".")

    monto_f.short_description = "Total"

    def origen_badge(self, obj):
        color = "primary" if obj.origen == "telefono" else "success"
        texto = "Domicilio" if obj.origen == "telefono" else "Local"
        return format_html('<span class="badge bg-{}">{}</span>', color, texto)

    origen_badge.short_description = "Origen"

    def estado_badge(self, obj):
        colores = {
            "entregado": "success",
            "en_ruta": "warning",
            "pendiente": "secondary",
            "cancelado": "danger",
        }
        return format_html(
            '<span class="badge bg-{}">{}</span>',
            colores.get(obj.estado, "dark"),
            obj.get_estado_display(),
        )

    estado_badge.short_description = "Estado"

    def sector_corto(self, obj):
        if obj.sector:
            return (obj.sector[:30] + "...") if len(obj.sector) > 30 else obj.sector
        return "—"

    sector_corto.short_description = "Sector"
