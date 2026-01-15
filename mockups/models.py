# mockups/models.py
# GasFácil - Versión con múltiples balones por pedido + precios diferenciados
# Enero 2026 - KIM GAS Rancagua

from django.contrib.auth.models import AbstractUser
from django.db import models
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
    tipo_balón = models.ForeignKey(
        TipoBalon, on_delete=models.CASCADE, related_name="historial_precios"
    )
    precio_compra_anterior     = models.DecimalField(max_digits=10, decimal_places=0)
    precio_local_anterior      = models.DecimalField(max_digits=10, decimal_places=0)
    precio_domicilio_anterior  = models.DecimalField(max_digits=10, decimal_places=0)
    activo_anterior            = models.BooleanField()
    fecha_cambio               = models.DateTimeField(default=timezone.now)
    actualizado_por            = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "Historial de precio"
        verbose_name_plural = "Historial de precios"
        ordering = ["-fecha_cambio"]

    def __str__(self):
        return f"{self.tipo_balón} - {self.fecha_cambio.date()}"


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