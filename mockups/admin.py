# mockups/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from .models import Usuario, TipoBalon, HistorialPrecioBalon, Sector, Pedido, DetallePedido, HistorialEstadoPedido, SobreDiario, LineaSobre, AuditoriaAccion, PushSubscription


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
        'tipo_gas',
        'precio_compra',
        'precio_local',
        'precio_domicilio',
        'activo',
        'actualizado_el',
        'actualizado_por'
    )
    list_filter = ('activo', 'tipo_gas')
    search_fields = ('nombre', 'peso_neto_gas')
    list_editable = ('precio_compra', 'precio_local', 'precio_domicilio', 'activo')
    readonly_fields = ('actualizado_el', 'actualizado_por')
    ordering = ('-peso_neto_gas', 'tipo_gas')


@admin.register(HistorialPrecioBalon)
class HistorialPrecioBalonAdmin(admin.ModelAdmin):
    list_display = (
        'nombre_balon',                    # ← snapshot del nombre
        'precio_compra_anterior',
        'precio_local_anterior',
        'precio_domicilio_anterior',
        'activo_anterior',
        'fecha_cambio',
        'actualizado_por',
    )
    list_filter = (
        'fecha_cambio',
        # 'activo_anterior',  # opcional, si quieres filtrar por estado disponible
    )
    search_fields = (
        'nombre_balon',                    # ← buscamos por el nombre snapshot
        'actualizado_por__username',
        'actualizado_por__first_name',
        'actualizado_por__last_name',
    )
    readonly_fields = (
        'nombre_balon',
        'precio_compra_anterior',
        'precio_local_anterior',
        'precio_domicilio_anterior',
        'activo_anterior',
        'fecha_cambio',
        'actualizado_por',
    )
    ordering = ('-fecha_cambio',)
    
    # No permitir crear/editar/borrar manualmente (es historial automático)
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'zona', 'activo', 'actualizado_el')
    list_filter = ('zona', 'activo')
    search_fields = ('nombre', 'codigo')
    list_editable = ('activo',)
    readonly_fields = ('creado_el', 'actualizado_el')
    ordering = ('zona', 'nombre')
    
class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 1
    fields = ('balon', 'cantidad', 'precio_venta_unitario', 'precio_compra_unitario', 'subtotal', 'ganancia')
    readonly_fields = ('subtotal', 'ganancia')
    ordering = ('-balon__peso_neto_gas', 'balon__tipo_gas')


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

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('detalles__balon')

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
    # date_hierarchy = 'fecha'
    ordering = ('-fecha',)
    readonly_fields = ('total_declarado', 'total_diferencia')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _total_declarado=Sum('lineas__cantidad_declarada'),
            _total_diferencia=Sum(F('lineas__cantidad_declarada') - F('lineas__cantidad_calculada')),
        )

    def total_declarado(self, obj):
        if hasattr(obj, '_total_declarado'):
            return obj._total_declarado or 0
        return obj.lineas.aggregate(total=Sum('cantidad_declarada'))['total'] or 0
    total_declarado.short_description = 'Total Declarado'

    def total_diferencia(self, obj):
        if hasattr(obj, '_total_diferencia'):
            return obj._total_diferencia or 0
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


# ══════════════════════════════════════════════════════════════════
# AUDITORÍA
# ══════════════════════════════════════════════════════════════════

@admin.register(AuditoriaAccion)
class AuditoriaAccionAdmin(admin.ModelAdmin):
    """
    Panel de auditoría para revisar todas las acciones del sistema.
    Solo lectura - los registros de auditoría NO deben modificarse.
    """
    list_display = (
        'fecha',
        'tipo_badge',
        'username',
        'descripcion_corta',
        'ip_address',
        'objeto_info',
    )
    list_filter = (
        'tipo',
        'fecha',
        ('usuario', admin.RelatedOnlyFieldListFilter),
    )
    search_fields = (
        'username',
        'descripcion',
        'ip_address',
        'objeto_repr',
    )
    readonly_fields = (
        'tipo',
        'usuario',
        'username',
        'ip_address',
        'user_agent',
        'descripcion',
        'objeto_tipo',
        'objeto_id',
        'objeto_repr',
        'datos_anteriores',
        'datos_nuevos',
        'fecha',
    )
    ordering = ('-fecha',)
    # date_hierarchy = 'fecha'  # Desactivado: MySQL necesita timezone definitions
    list_per_page = 50
    
    # Deshabilitar eliminación y edición
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def tipo_badge(self, obj):
        """Muestra el tipo de acción con color según categoría."""
        colores = {
            'LOGIN_OK': '#28a745',      # verde
            'LOGIN_FAIL': '#dc3545',    # rojo
            'LOGOUT': '#6c757d',        # gris
            'USER_CREATE': '#17a2b8',   # cyan
            'USER_UPDATE': '#17a2b8',
            'USER_DELETE': '#dc3545',
            'PEDIDO_CREATE': '#007bff', # azul
            'PEDIDO_UPDATE': '#ffc107', # amarillo
            'PEDIDO_DELETE': '#dc3545',
            'PRECIO_UPDATE': '#fd7e14', # naranja
            'PERM_DENIED': '#dc3545',   # rojo
            'SUSPICIOUS': '#dc3545',
        }
        color = colores.get(obj.tipo, '#6c757d')
        return format_html(
            '<span style="background-color:{}; color:white; padding:3px 8px; '
            'border-radius:3px; font-size:11px;">{}</span>',
            color, obj.get_tipo_display()
        )
    tipo_badge.short_description = 'Tipo'
    tipo_badge.admin_order_field = 'tipo'
    
    def descripcion_corta(self, obj):
        """Muestra descripción truncada."""
        if obj.descripcion:
            return obj.descripcion[:60] + ('...' if len(obj.descripcion) > 60 else '')
        return '-'
    descripcion_corta.short_description = 'Descripción'
    
    def objeto_info(self, obj):
        """Muestra información del objeto afectado."""
        if obj.objeto_tipo:
            return f"{obj.objeto_tipo} #{obj.objeto_id}"
        return '-'
    objeto_info.short_description = 'Objeto'
    
    fieldsets = (
        ('Información General', {
            'fields': ('tipo', 'fecha', 'descripcion')
        }),
        ('Usuario', {
            'fields': ('usuario', 'username', 'ip_address', 'user_agent')
        }),
        ('Objeto Afectado', {
            'fields': ('objeto_tipo', 'objeto_id', 'objeto_repr'),
            'classes': ('collapse',)
        }),
        ('Datos (JSON)', {
            'fields': ('datos_anteriores', 'datos_nuevos'),
            'classes': ('collapse',)
        }),
    )


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    """Admin para gestionar suscripciones de notificaciones push."""
    
    list_display = ('usuario', 'dispositivo', 'activa', 'creada_el', 'actualizada_el')
    list_filter = ('activa', 'creada_el')
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name', 'endpoint')
    readonly_fields = ('endpoint', 'p256dh', 'auth', 'user_agent', 'creada_el', 'actualizada_el')
    list_per_page = 25

    def dispositivo(self, obj):
        """Muestra el tipo de dispositivo basado en el user agent."""
        if 'Mobile' in obj.user_agent or 'Android' in obj.user_agent or 'iPhone' in obj.user_agent:
            return format_html('<span class="badge" style="background-color: #198754;">📱 Móvil</span>')
        return format_html('<span class="badge" style="background-color: #0d6efd;">💻 Desktop</span>')
    dispositivo.short_description = 'Dispositivo'
    
    fieldsets = (
        ('Usuario', {
            'fields': ('usuario', 'activa')
        }),
        ('Suscripción', {
            'fields': ('endpoint', 'p256dh', 'auth'),
            'classes': ('collapse',)
        }),
        ('Metadatos', {
            'fields': ('user_agent', 'creada_el', 'actualizada_el'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['activar_suscripciones', 'desactivar_suscripciones']
    
    def activar_suscripciones(self, request, queryset):
        queryset.update(activa=True)
        self.message_user(request, f'{queryset.count()} suscripciones activadas.')
    activar_suscripciones.short_description = 'Activar suscripciones seleccionadas'
    
    def desactivar_suscripciones(self, request, queryset):
        queryset.update(activa=False)
        self.message_user(request, f'{queryset.count()} suscripciones desactivadas.')
    desactivar_suscripciones.short_description = 'Desactivar suscripciones seleccionadas'
