# mockups/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from .models import Usuario, TipoBalon, HistorialPrecioBalon, Pedido, DetallePedido, HistorialEstadoPedido, SobreDiario, LineaSobre


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


class HistorialEstadoPedidoInline(admin.TabularInline):
    model = HistorialEstadoPedido
    extra = 0
    fields = ('estado_anterior', 'estado_nuevo', 'cambiado_por', 'fecha_cambio', 'comentario')
    readonly_fields = ('estado_anterior', 'estado_nuevo', 'cambiado_por', 'fecha_cambio')
    ordering = ('-fecha_cambio',)



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
    inlines = [DetallePedidoInline, HistorialEstadoPedidoInline]
    # date_hierarchy = 'fecha'  # ya comentado, perfecto
    actions = ['marcar_entregado', 'marcar_cancelado']

    def marcar_entregado(self, request, queryset):
        queryset.update(estado='entregado')
    marcar_entregado.short_description = "Marcar seleccionados como entregados"

    def marcar_cancelado(self, request, queryset):
        queryset.update(estado='cancelado')
    marcar_cancelado.short_description = "Marcar seleccionados como cancelados"


@admin.register(HistorialEstadoPedido)
class HistorialEstadoPedidoAdmin(admin.ModelAdmin):
    list_display = (
        'pedido',
        'estado_anterior',
        'estado_nuevo',
        'cambiado_por',
        'fecha_cambio',
        'comentario'
    )
    list_filter = ('estado_nuevo', 'fecha_cambio', 'cambiado_por')
    search_fields = ('pedido__id', 'comentario')
    readonly_fields = ('fecha_cambio',)
    ordering = ('-fecha_cambio',)


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
    

@admin.register(SobreDiario)
class SobreDiarioAdmin(admin.ModelAdmin):
    list_display = (
        'fecha',
        'get_tipo_display',
        'trabajador',
        'creado_por',
        'total_declarado',
        'total_diferencia',
    )
    list_filter = ('fecha', 'tipo', 'trabajador')
    search_fields = ('trabajador__username', 'creado_por__username')
    date_hierarchy = 'fecha'
    ordering = ('-fecha',)
    readonly_fields = ('total_declarado', 'total_diferencia')

    def total_declarado(self, obj):
        return obj.lineas.aggregate(total=Sum('cantidad_declarada'))['total'] or 0
    total_declarado.short_description = 'Total Declarado'

    def total_diferencia(self, obj):
        return obj.lineas.aggregate(
            total=Sum(F('cantidad_declarada') - F('cantidad_calculada'))
        )['total'] or 0
    total_diferencia.short_description = 'Diferencia Total'

    fieldsets = (
        ('Información básica', {
            'fields': ('fecha', 'tipo', 'trabajador', 'creado_por')
        }),
        ('Resumen calculado (solo lectura)', {
            'fields': ('total_declarado', 'total_diferencia')
        }),
    )

    # Inline para ver y editar líneas directamente desde el sobre
    class LineaInline(admin.TabularInline):
        model = LineaSobre
        extra = 0
        fields = ('balon', 'cantidad_calculada', 'cantidad_declarada', 'diferencia_cantidad', 'nota')
        readonly_fields = ('balon', 'cantidad_calculada', 'diferencia_cantidad')
        can_delete = False
        show_change_link = True

    inlines = [LineaInline]


@admin.register(LineaSobre)
class LineaSobreAdmin(admin.ModelAdmin):
    list_display = (
        'sobre',
        'balon',
        'cantidad_calculada',
        'cantidad_declarada',
        'diferencia_cantidad',
        'subtotal_declarado',
        'nota_corta',
    )
    list_filter = ('sobre__fecha', 'balon')
    search_fields = ('balon__nombre', 'sobre__trabajador__username')
    readonly_fields = ('diferencia_cantidad', 'subtotal_declarado')

    def nota_corta(self, obj):
        return (obj.nota[:40] + '...') if obj.nota else '-'
    nota_corta.short_description = 'Nota'

    fieldsets = (
        (None, {
            'fields': ('sobre', 'balon', 'cantidad_calculada', 'cantidad_declarada')
        }),
        ('Calculados', {
            'fields': ('diferencia_cantidad', 'subtotal_declarado'),
            'classes': ('collapse',)
        }),
        ('Observaciones', {
            'fields': ('nota',)
        }),
    )