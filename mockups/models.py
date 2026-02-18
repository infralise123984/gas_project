# mockups/models.py
# GasFácil - Versión con múltiples balones por pedido + precios diferenciados
# Enero 2026 - KIM GAS Rancagua

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.core.exceptions import ValidationError
import re


class Usuario(AbstractUser):
    ROLES = [
        ("telefonista", "Telefonista"),
        ("bodeguero",   "Bodeguero"),
        ("camionero",   "Camionero"),
        ("jefe",        "Jefe"),
        ("admin",       "Administrador"),
    ]

    rol = models.CharField(
        max_length=20, choices=ROLES, default="telefonista", verbose_name="Rol"
    )
    telefono = models.CharField(
        max_length=15, blank=True, null=True, unique=True, verbose_name="Teléfono"
    )

    def __str__(self):
        nombre = self.get_full_name().strip() or self.username
        return f"{nombre} ({self.get_rol_display()})"

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ["-date_joined"]


class TipoBalon(models.Model):
    nombre = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Nombre comercial",
        help_text="Ej: Gas 5 kg, Gas 11 kg, Gas 15 kg, Gas 45 kg"
    )
    peso_neto_gas = models.PositiveIntegerField(
        verbose_name="Peso neto de gas (kg)",
        help_text="Cantidad real de gas licuado (sin envase)"
    )

    precio_compra = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=0,
        verbose_name="Precio de compra (CLP)",
        help_text="Costo real para KIM GAS (proveedor)"
    )
    precio_local = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=0,
        verbose_name="Precio venta local (CLP)",
        help_text="Precio al público en bodega / venta directa"
    )
    precio_domicilio = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=0,
        verbose_name="Precio venta domicilio (CLP)",
        help_text="Precio para pedidos telefónicos, tarreo o entregas a domicilio"
    )

    activo = models.BooleanField(default=True, verbose_name="Disponible para venta")
    actualizado_el = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Última actualización por"
    )

    def clean(self):
        if self.nombre:
            match = re.search(r"(\d+)", self.nombre)
            if match and self.peso_neto_gas:
                peso_en_nombre = int(match.group(1))
                if peso_en_nombre != self.peso_neto_gas:
                    raise ValidationError(
                        f"El peso en el nombre ({peso_en_nombre} kg) debe coincidir con el peso neto ({self.peso_neto_gas} kg)."
                    )

    def __str__(self):
        return f"{self.nombre} ({self.peso_neto_gas} kg)"

    class Meta:
        verbose_name = "Tipo de balón"
        verbose_name_plural = "Tipos de balones"
        ordering = ["peso_neto_gas"]


class HistorialPrecioBalon(models.Model):
    # Snapshot del nombre del balón en el momento del cambio (para que se entienda de qué se trata sin FK)
    nombre_balon = models.CharField(
        max_length=100,
        verbose_name="Nombre del Balón (snapshot)",
        null=True,          # ← temporal, para pasar la migración
        blank=True,
    )
    
    # Precios anteriores (los 3 tipos)
    precio_compra_anterior     = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="Precio Compra Anterior")
    precio_local_anterior      = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="Precio Local Anterior")
    precio_domicilio_anterior  = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="Precio Domicilio Anterior")
    
    activo_anterior            = models.BooleanField(verbose_name="Disponible Anterior")
    
    fecha_cambio               = models.DateTimeField(default=timezone.now, verbose_name="Fecha del Cambio")
    actualizado_por            = models.ForeignKey(
        'Usuario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Actualizado por"
    )

    class Meta:
        verbose_name = "Historial de Precio de Balón"
        verbose_name_plural = "Historial de Precios de Balones"
        ordering = ["-fecha_cambio"]

    def __str__(self):
        return f"{self.nombre_balon} - {self.fecha_cambio.date()}"


class Pedido(models.Model):
    ESTADOS = [
        ("pendiente",  "Pendiente"),
        ("en_ruta",    "En ruta"),
        ("entregado",  "Entregado"),
        ("cancelado",  "Cancelado"),
    ]

    ORIGENES = [
        ("telefono",     "Teléfono / Domicilio"),
        ("local",        "Venta en local"),
        ("tarreo",       "Venta por tarreo"),
        ("venta_extra",  "Venta adicional en entrega"),
    ]

    # Campos de cabecera
    sector              = models.CharField(max_length=100, blank=True, verbose_name="Sector / Población")
    direccion_entrega   = models.CharField(max_length=250, blank=True, verbose_name="Dirección o referencia")

    registrador = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True,
        related_name="pedidos_registrados", verbose_name="Registrado por"
    )
    entregador = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pedidos_entregados", verbose_name="Entregado por"
    )

    fecha       = models.DateTimeField(default=timezone.now, verbose_name="Fecha de registro")
    estado      = models.CharField(max_length=20, choices=ESTADOS, default="pendiente")
    origen      = models.CharField(max_length=20, choices=ORIGENES, default="telefono")

    metodo_pago = models.CharField(
        max_length=20,
        choices=[("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia")],
        default="efectivo"
    )

    monto_total     = models.DecimalField(max_digits=12, decimal_places=0, default=0, verbose_name="Monto total venta")
    ganancia_total  = models.DecimalField(max_digits=12, decimal_places=0, default=0, verbose_name="Ganancia total")

    def calcular_totales(self):
        """Actualiza monto_total y ganancia_total sumando los detalles"""
        detalles = self.detalles.all()
        self.monto_total = sum(d.subtotal for d in detalles)
        self.ganancia_total = sum(d.ganancia for d in detalles)
        self.save(update_fields=["monto_total", "ganancia_total"])

    @property
    def resumen_productos(self):
        """Texto corto para mostrar en listas: 2×11kg + 1×5kg"""
        if not hasattr(self, '_resumen_cache'):
            items = [f"{d.cantidad}×{d.balon.peso_neto_gas}kg" for d in self.detalles.all()]
            self._resumen_cache = " + ".join(items) if items else "—"
        return self._resumen_cache

    def __str__(self):
        return f"Pedido #{self.id} • {self.get_origen_display()} • ${self.monto_total:,} • {self.resumen_productos}"

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-fecha"]
        indexes = [
            models.Index(fields=["fecha"]),
            models.Index(fields=["estado"]),
            models.Index(fields=["origen"]),
            models.Index(fields=["registrador"]),
            models.Index(fields=["entregador"]),
        ]


class HistorialEstadoPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='historial_estados')
    estado_anterior = models.CharField(max_length=20, choices=Pedido.ESTADOS, blank=True, null=True)
    estado_nuevo    = models.CharField(max_length=20, choices=Pedido.ESTADOS)
    cambiado_por    = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_cambio    = models.DateTimeField(default=timezone.now)
    comentario      = models.TextField(blank=True, null=True)  # opcional: "Cliente no encontró", "Sin cambio de dinero", etc.

    class Meta:
        ordering = ['-fecha_cambio']
        verbose_name = "Cambio de estado"
        verbose_name_plural = "Historial de estados"

    def __str__(self):
        return f"{self.pedido_id} → {self.estado_nuevo} ({self.fecha_cambio:%d/%m %H:%M})"

class DetallePedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="detalles")

    balon   = models.ForeignKey(TipoBalon, on_delete=models.PROTECT, verbose_name="Tipo de balón")
    cantidad = models.PositiveIntegerField(default=1, verbose_name="Cantidad")

    # Snapshot de precios al momento de la venta (evita cambios retroactivos)
    precio_venta_unitario  = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="Precio venta unitario")
    precio_compra_unitario = models.DecimalField(max_digits=10, decimal_places=0, verbose_name="Precio compra unitario")

    @property
    def subtotal(self):
        return self.precio_venta_unitario * self.cantidad

    @property
    def ganancia(self):
        return (self.precio_venta_unitario - self.precio_compra_unitario) * self.cantidad

    def __str__(self):
        return f"{self.cantidad} × {self.balon.nombre}"

    class Meta:
        verbose_name = "Detalle de pedido"
        verbose_name_plural = "Detalles de pedidos"
        ordering = ["balon__peso_neto_gas"]

class SobreDiario(models.Model):
    # Fechas
    fecha = models.DateTimeField(default=timezone.now, verbose_name="Fecha de creación")
    fecha_correspondiente = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha a la que corresponde"
    )
    
    # Relaciones
    creado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sobres_creados",
        verbose_name="Creado por (bodeguero/jefe)"
    )
    trabajador = models.ForeignKey(
        Usuario,
        on_delete=models.PROTECT,
        related_name="sobres",
        limit_choices_to={'rol__in': ['camionero', 'bodeguero']},
        null=True,
        blank=True,
        verbose_name="Trabajador (camionero o bodeguero)"
    )
    
    # Tipo y timestamps
    tipo = models.CharField(
        max_length=20,
        choices=[('camion', 'Camión'), ('bodega', 'Bodega/local')],
        default='camion',
        verbose_name="Tipo de sobre"
    )
    creado_el = models.DateTimeField(auto_now_add=True)
    actualizado_el = models.DateTimeField(auto_now=True)
    
    # Montos
    monto_calculado_app = models.DecimalField(max_digits=12, decimal_places=0, default=0, verbose_name="Monto según pedidos en app")
    monto_declarado = models.DecimalField(max_digits=12, decimal_places=0, default=0, verbose_name="Monto declarado/ajustado")
    diferencia = models.DecimalField(max_digits=12, decimal_places=0, default=0, verbose_name="Diferencia (ajuste)")
    nota = models.TextField(blank=True, verbose_name="Motivo del ajuste en esta línea")
    
    # Cierre
    cerrado = models.BooleanField(default=False, verbose_name="Sobre cerrado/finalizado")
    declarado_el = models.DateTimeField(null=True, blank=True, verbose_name="Fecha y hora de declaración/cierre")
    nota_cierre = models.TextField(blank=True, verbose_name="Nota al cerrar el sobre")
    
    # Kilometraje
    kilometraje_camion = models.PositiveIntegerField(
        default=0,
        blank=True,
        verbose_name="Kilometraje total del camión",
        help_text="Lectura del odómetro al momento de cerrar el sobre"
    )
    
    class Meta:
        verbose_name = "Sobre diario"
        verbose_name_plural = "Sobres diarios"
        # unique_together = ['fecha_correspondiente', 'trabajador', 'tipo']
        ordering = ['-fecha_correspondiente', '-fecha']

    def __str__(self):
        if self.tipo == 'bodega':
            return f"Sobre Bodega - {self.fecha_correspondiente.strftime('%d/%m/%Y')}"
        else:
            trabajador_nombre = self.trabajador.get_full_name() if self.trabajador else "Sin trabajador"
            return f"Sobre {self.fecha_correspondiente.strftime('%d/%m/%Y')} - {trabajador_nombre} ({self.get_tipo_display()})"

    def calcular_desde_pedidos(self):
        """Suma cantidades y montos desde pedidos del día para este trabajador"""
        if self.tipo == 'camion':
            pedidos = Pedido.objects.filter(
                fecha__date=self.fecha_correspondiente,
                entregador=self.trabajador,
                estado='entregado'
            )
        else:
            pedidos = Pedido.objects.filter(
                fecha__date=self.fecha_correspondiente,
                registrador=self.trabajador,
                origen='local',
                estado='entregado'
            )
        total_monto = pedidos.aggregate(total=Sum('monto_total'))['total'] or 0
        self.monto_calculado_app = total_monto
        return total_monto

    def calcular_total_declarado(self):
        """Suma real de las líneas declaradas"""
        return sum(linea.subtotal_declarado for linea in self.lineas.all())

    def save(self, *args, **kwargs):
        """
        Versión corregida: Guarda primero para tener pk, luego recalcula desde líneas.
        Evita el error "instance needs to have a primary key".
        """
        if not self.pk:  # Al crear
            self.calcular_desde_pedidos()
            self.monto_declarado = self.monto_calculado_app
            self.diferencia = 0

        # Guardar primero
        super().save(*args, **kwargs)

        # Recalcular desde líneas solo si ya tiene pk
        if self.pk:
            nuevo_monto = self.calcular_total_declarado()
            nueva_diferencia = nuevo_monto - self.monto_calculado_app
            if nuevo_monto != self.monto_declarado or nueva_diferencia != self.diferencia:
                self.monto_declarado = nuevo_monto
                self.diferencia = nueva_diferencia
                super().save(update_fields=['monto_declarado', 'diferencia'])
class LineaSobre(models.Model):
    """
    Línea de detalle por tipo de balón en el sobre (editable manualmente)
    """
    sobre = models.ForeignKey(SobreDiario, on_delete=models.CASCADE, related_name='lineas')
    balon = models.ForeignKey(TipoBalon, on_delete=models.PROTECT, verbose_name="Tipo de balón")
    
    # Cantidades
    cantidad_calculada = models.PositiveIntegerField(default=0, verbose_name="Cantidad según app")
    cantidad_declarada = models.PositiveIntegerField(default=0, verbose_name="Cantidad declarada")
    
    # Precios snapshot (para consistencia histórica)
    precio_venta_unitario = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    
    # ← AGREGAR ESTA LÍNEA
    nota = models.TextField(blank=True, verbose_name="Motivo del ajuste en esta línea")
    
    @property
    def diferencia_cantidad(self):
        return self.cantidad_declarada - self.cantidad_calculada

    @property
    def subtotal_calculado(self):
        return self.cantidad_calculada * self.precio_venta_unitario

    @property
    def subtotal_declarado(self):
        return self.cantidad_declarada * self.precio_venta_unitario

    class Meta:
        unique_together = ['sobre', 'balon']
        ordering = ['balon__peso_neto_gas']

    def __str__(self):
        return f"{self.balon.nombre} → {self.cantidad_declarada} (calc: {self.cantidad_calculada})"
    """
    Línea de detalle por tipo de balón en el sobre (editable manualmente)
    """
    sobre = models.ForeignKey(SobreDiario, on_delete=models.CASCADE, related_name='lineas')
    balon = models.ForeignKey(TipoBalon, on_delete=models.PROTECT, verbose_name="Tipo de balón")
    
    # Cantidades
    cantidad_calculada = models.PositiveIntegerField(default=0, verbose_name="Cantidad según app")
    cantidad_declarada = models.PositiveIntegerField(default=0, verbose_name="Cantidad declarada")
    
    # Precios snapshot (para consistencia histórica)
    precio_venta_unitario = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    
    @property
    def diferencia_cantidad(self):
        return self.cantidad_declarada - self.cantidad_calculada

    @property
    def subtotal_calculado(self):
        return self.cantidad_calculada * self.precio_venta_unitario

    @property
    def subtotal_declarado(self):
        return self.cantidad_declarada * self.precio_venta_unitario

    class Meta:
        unique_together = ['sobre', 'balon']
        ordering = ['balon__peso_neto_gas']

    def __str__(self):
        return f"{self.balon.nombre} → {self.cantidad_declarada} (calc: {self.cantidad_calculada})"
    
class HistorialCambioPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='historial_cambios')
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, verbose_name="Usuario que editó")
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha del cambio")
    descripcion = models.TextField(verbose_name="Qué cambió", help_text="Descripción automática de los cambios realizados")

    class Meta:
        verbose_name = "Historial de Cambio en Pedido"
        verbose_name_plural = "Historial de Cambios en Pedidos"
        ordering = ['-fecha']

    def __str__(self):
        return f"Cambio en Pedido #{self.pedido.id} por {self.usuario} ({self.fecha})"
    
# ... (código existente de models.py)

class LineaPago(models.Model):
    TIPO_PAGO = [
        ('abono', 'Abono Caja'),
        ('transferencia', 'Transferencia'),
        ('visa', 'Visa'),
        ('cheque', 'Cheque'),
        ('efectivo', 'Efectivo'),
        ('otro', 'Otro'),
    ]

    sobre = models.ForeignKey(SobreDiario, on_delete=models.CASCADE, related_name='pagos')
    tipo_pago = models.CharField(max_length=20, choices=TIPO_PAGO, default='otro', verbose_name="Tipo de Pago")
    monto = models.DecimalField(max_digits=10, decimal_places=0, default=0, verbose_name="Monto")
    referencia = models.CharField(max_length=100, blank=True, verbose_name="Referencia/Nota")

    class Meta:
        verbose_name = "Línea de Pago No Efectivo"
        verbose_name_plural = "Líneas de Pagos No Efectivos"
        ordering = ['-monto']

    def __str__(self):
        return f"{self.get_tipo_pago_display()} - ${self.monto:,}"

class LineaGasto(models.Model):
    sobre = models.ForeignKey(SobreDiario, on_delete=models.CASCADE, related_name='gastos')
    descripcion = models.CharField(max_length=100, verbose_name="Descripción")
    monto = models.DecimalField(max_digits=10, decimal_places=0, default=0, verbose_name="Monto")
    nota = models.TextField(blank=True, verbose_name="Nota")

    class Meta:
        verbose_name = "Línea de Gasto Extra"
        verbose_name_plural = "Líneas de Gastos Extras"
        ordering = ['-monto']

    def __str__(self):
        return f"{self.descripcion} - ${self.monto:,}"