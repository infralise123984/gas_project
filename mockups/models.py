# mockups/models.py
# GasFácil - Versión definitiva para Rancagua y ciudades medianas
# Diciembre 2025

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


# ==============================================================
# 1. USUARIO PERSONALIZADO CON ROLES
# ==============================================================
class Usuario(AbstractUser):
    ROLES = [
        ("telefonista", "Telefonista"),
        ("bodeguero", "Bodeguero"),
        ("camionero", "Camionero"),
        ("jefe", "Jefe"),
        ("admin", "Administrador"),
    ]

    rol = models.CharField(max_length=20, choices=ROLES, default="telefonista", verbose_name="Rol")
    telefono = models.CharField(max_length=15, blank=True, null=True, unique=True, verbose_name="Teléfono")

    def __str__(self):
        nombre = self.get_full_name().strip() or self.username
        return f"{nombre} ({self.get_rol_display()})"

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ["-date_joined"]


# ==============================================================
# 2. TIPO DE BALÓN (con precio actual integrado)
# ==============================================================
class TipoBalon(models.Model):
    """
    Cada tamaño de balón tiene su precio actual.
    Ejemplos reales en Rancagua 2025:
    - 5kg  → $14.500
    - 11kg → $25.800
    - 15kg → $32.800
    - 45kg → $89.000
    """
    tamaño = models.CharField(max_length=10, unique=True, verbose_name="Tamaño", help_text="Ej: 5kg, 15kg, 45kg")
    precio = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=0,
        verbose_name="Precio actual (CLP)",
        help_text="Precio entero en pesos chilenos"
    )
    activo = models.BooleanField(default=True, verbose_name="Disponible para venta")
    actualizado_el = models.DateTimeField(auto_now=True, verbose_name="Última actualización")
    actualizado_por = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Actualizado por"
    )

    def save(self, *args, **kwargs):
        # Solo crear historial si ya existe el registro (es una actualización)
        if self.pk:
            viejo = TipoBalon.objects.get(pk=self.pk)
            if viejo.precio != self.precio or viejo.activo != self.activo:
                HistorialPrecioBalon.objects.create(
                    tipo_balón=self,
                    precio_anterior=viejo.precio,
                    precio_nuevo=self.precio,
                    activo_anterior=viejo.activo,
                    activo_nuevo=self.activo,
                    cambiado_por=self.actualizado_por
                )
        super().save(*args, **kwargs)

    def __str__(self):
        if not self.activo:
            return f"{self.tamaño} (inactivo)"
        return f"{self.tamaño} - ${self.precio:,.0f}".replace(",", ".")

    class Meta:
        verbose_name = "Tipo de Balón"
        verbose_name_plural = "Tipos de Balones"
        ordering = ["tamaño"]


# ==============================================================
# 3. HISTORIAL DE PRECIOS (log automático)
# ==============================================================
class HistorialPrecioBalon(models.Model):
    """
    Cada vez que el jefe cambia un precio o desactiva un balón,
    se guarda aquí automáticamente.
    Ideal para reportes, auditoría o justificar alzas.
    """
    tipo_balón = models.ForeignKey(TipoBalon, on_delete=models.CASCADE, related_name="historial")
    precio_anterior = models.DecimalField(max_digits=10, decimal_places=0)
    precio_nuevo = models.DecimalField(max_digits=10, decimal_places=0)
    activo_anterior = models.BooleanField()
    activo_nuevo = models.BooleanField()
    fecha_cambio = models.DateTimeField(default=timezone.now)
    cambiado_por = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cambiado por"
    )

    def __str__(self):
        return f"{self.tipo_balón.tamaño}: ${self.precio_anterior:,} → ${self.precio_nuevo:,} ({self.fecha_cambio.date()})"

    class Meta:
        verbose_name = "Cambio de Precio"
        verbose_name_plural = "Historial de Precios"
        ordering = ["-fecha_cambio"]


# ==============================================================
# 4. PEDIDO (simplificado para la realidad de Rancagua)
# ==============================================================
class Pedido(models.Model):
    ESTADOS = [
        ("pendiente", "Pendiente"),
        ("en_ruta", "En ruta"),
        ("entregado", "Entregado"),
        ("cancelado", "Cancelado"),
    ]
    ORIGENES = [
        ("telefono", "Teléfono"),
        ("local", "Local"),
    ]

    # Solo para pedidos a domicilio (telefonista)
    sector = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Sector / Población",
        help_text="Ej: Población Dintrans, Machalí Alto, Villa Los Tilos, Gultro..."
    )
    direccion_entrega = models.CharField(
        max_length=250,
        blank=True,
        verbose_name="Dirección o referencia",
        help_text="Ej: Los Álamos 123, casa esquina roja, frente al colegio"
    )

    # Datos del pedido
    balon = models.ForeignKey(TipoBalon, on_delete=models.PROTECT, verbose_name="Tipo de balón")
    cantidad_balon = models.PositiveIntegerField(default=1, verbose_name="Cantidad")
    metodo_pago = models.CharField(
        max_length=20,
        choices=[("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia")],
        default="efectivo"
    )
    monto = models.DecimalField(max_digits=10, decimal_places=0, default=0, verbose_name="Monto total")

    # Metadatos
    registrador = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedidos_registrados",
        verbose_name="Registrado por"
    )
    fecha = models.DateTimeField(default=timezone.now)
    estado = models.CharField(max_length=20, choices=ESTADOS, default="pendiente")
    origen = models.CharField(max_length=20, choices=ORIGENES, default="telefono")

    def save(self, *args, **kwargs):
        # Si es venta local (bodeguero), se marca como entregado automáticamente
        if self.origen == "local" and self.estado == "pendiente":
            self.estado = "entregado"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Pedido #{self.id} - {self.balon} x{self.cantidad_balon} ({self.get_origen_display()})"

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-fecha"]
        indexes = [
            models.Index(fields=["fecha"]),
            models.Index(fields=["origen"]),
            models.Index(fields=["estado"]),
        ]