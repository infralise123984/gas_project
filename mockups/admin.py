# mockups/admin.py
from django.contrib import admin
from .models import Usuario, TipoBalon, HistorialPrecioBalon, Pedido, DetallePedido


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('username', 'get_full_name', 'rol', 'telefono', 'date_joined', 'is_active')
    list_filter = ('rol', 'is_active', 'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'telefono')
    readonly_fields = ('date_joined',)
    ordering = ('-date_joined',)


@admin.register(TipoBalon)
class TipoBalonAdmin(admin.ModelAdmin):
    list_display = (
        'nombre',
        'peso_neto_gas',
        'precio_compra',
        'precio_local',
        'precio_domicilio',
        'activo',
        'actualizado_el',
        'actualizado_por'
    )
    list_filter = ('activo',)
    search_fields = ('nombre', 'peso_neto_gas')
    list_editable = ('precio_compra', 'precio_local', 'precio_domicilio', 'activo')
    readonly_fields = ('actualizado_el', 'actualizado_por')
    ordering = ('peso_neto_gas',)


@admin.register(HistorialPrecioBalon)
class HistorialPrecioBalonAdmin(admin.ModelAdmin):
    list_display = (
        'tipo_balón',
        'precio_compra_anterior',
        'precio_local_anterior',
        'precio_domicilio_anterior',
        'activo_anterior',
        'fecha_cambio',
        'actualizado_por'
    )
    list_filter = ('tipo_balón', 'fecha_cambio')
    search_fields = ('tipo_balón__nombre',)
    readonly_fields = ('fecha_cambio',)
    ordering = ('-fecha_cambio',)


class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 1
    fields = ('balon', 'cantidad', 'precio_venta_unitario', 'precio_compra_unitario', 'subtotal', 'ganancia')
    readonly_fields = ('subtotal', 'ganancia')
    ordering = ('balon__peso_neto_gas',)



@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'fecha',
        'estado',
        'origen',
        'registrador',
        'entregador',
        'metodo_pago',
        'monto_total',
        'ganancia_total',
        'sector',
        'resumen_productos'
    )
    list_filter = ('estado', 'origen', 'registrador__rol', 'entregador', 'metodo_pago')  # ← quitamos 'fecha'
    search_fields = ('id', 'sector', 'direccion_entrega', 'registrador__username', 'entregador__username')
    readonly_fields = ('fecha', 'monto_total', 'ganancia_total', 'resumen_productos')
    inlines = [DetallePedidoInline]
    # date_hierarchy = 'fecha'  # ya comentado, perfecto
    actions = ['marcar_entregado', 'marcar_cancelado']

    def marcar_entregado(self, request, queryset):
        queryset.update(estado='entregado')
    marcar_entregado.short_description = "Marcar seleccionados como entregados"

    def marcar_cancelado(self, request, queryset):
        queryset.update(estado='cancelado')
    marcar_cancelado.short_description = "Marcar seleccionados como cancelados"
@admin.register(DetallePedido)
class DetallePedidoAdmin(admin.ModelAdmin):
    list_display = (
        'pedido',
        'balon',
        'cantidad',
        'precio_venta_unitario',
        'precio_compra_unitario',
        'subtotal',
        'ganancia'
    )
    list_filter = ('pedido__estado', 'balon__peso_neto_gas', 'pedido__origen')
    search_fields = ('pedido__id', 'balon__nombre')
    readonly_fields = ('subtotal', 'ganancia')
    ordering = ('pedido__fecha', 'balon__peso_neto_gas')
    
    