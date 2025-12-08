# mockups/models.py
# GasFácil - Versión definitiva para Rancagua y ciudades medianas
# Diciembre 2025

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import re


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


# ==============================================================
# 2. TIPO DE BALÓN (con precio actual integrado)
# ==============================================================
class TipoBalon(models.Model):
    """
    Representa cada tipo de balón comercializado.
    Ejemplos reales Rancagua 2025:
    - Gas de 5 kg   → peso_neto_gas = 5
    - Gas de 11 kg  → peso_neto_gas = 11
    - Gas de 15 kg  → peso_neto_gas = 15
    - Gas de 45 kg  → peso_neto_gas = 45
    """

    nombre = models.CharField(
        max_length=50,
        unique=True,
        null=True,  # ← Temporal: permite migración sin default
        blank=True,  # ← Temporal
        verbose_name="Nombre comercial",
        help_text="Ej: Gas de 5 kg, Gas de 15 kg",
    )
    peso_neto_gas = models.PositiveIntegerField(
        null=True,  # ← Temporal: permite migración
        blank=True,  # ← Temporal
        verbose_name="Peso neto de gas (kg)",
        help_text="Cantidad real de gas licuado (sin envase)",
    )
    precio = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=0,
        verbose_name="Precio actual (CLP)",
    )
    activo = models.BooleanField(default=True, verbose_name="Disponible para venta")
    actualizado_el = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        "Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def clean(self):
        if self.nombre:
            # Validar que el número en nombre coincida con peso_neto_gas
            match = re.search(r"(\d+)", self.nombre)
            if match and self.peso_neto_gas:
                peso_en_nombre = int(match.group(1))
                if peso_en_nombre != self.peso_neto_gas:
                    raise ValidationError(
                        f"El peso en el nombre ({peso_en_nombre} kg) debe coincidir con el peso neto ({self.peso_neto_gas} kg)."
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.pk:
            viejo = TipoBalon.objects.get(pk=self.pk)
            if viejo.precio != self.precio or viejo.activo != self.activo:
                HistorialPrecioBalon.objects.create(
                    tipo_balón=self,
                    precio_anterior=viejo.precio,
                    precio_nuevo=self.precio,
                    activo_anterior=viejo.activo,
                    activo_nuevo=self.activo,
                    cambiado_por=self.actualizado_por,
                )
        super().save(*args, **kwargs)

    def __str__(self):
        if not self.activo:
            return f"{self.nombre or 'Sin nombre'} (inactivo)"
        return f"{self.nombre or 'Sin nombre'} - ${int(self.precio):,}".replace(
            ",", "."
        )

    class Meta:
        verbose_name = "Tipo de Balon"
        verbose_name_plural = "Tipos de Balones"
        ordering = ["peso_neto_gas"]


# ==============================================================
# 3. HISTORIAL DE PRECIOS (log automático)
# ==============================================================
class HistorialPrecioBalon(models.Model):
    tipo_balón = models.ForeignKey(
        TipoBalon, on_delete=models.CASCADE, related_name="historial"
    )
    precio_anterior = models.DecimalField(max_digits=10, decimal_places=0)
    precio_nuevo = models.DecimalField(max_digits=10, decimal_places=0)
    activo_anterior = models.BooleanField()
    activo_nuevo = models.BooleanField()
    fecha_cambio = models.DateTimeField(default=timezone.now)
    cambiado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Cambiado por",
    )

    def __str__(self):
        balon = self.tipo_balón
        nombre = balon.nombre if balon else "Balón eliminado"
        peso = f" ({balon.peso_neto_gas} kg)" if balon and balon.peso_neto_gas else ""
        return f"{nombre}{peso}: ${self.precio_anterior:,} → ${self.precio_nuevo:,} ({self.fecha_cambio.date()})".replace(
            ",", "."
        )

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
        help_text="Ej: Población Dintrans, Machalí Alto, Villa Los Tilos, Gultro...",
    )
    direccion_entrega = models.CharField(
        max_length=250,
        blank=True,
        verbose_name="Dirección o referencia",
        help_text="Ej: Los Álamos 123, casa esquina roja, frente al colegio",
    )

    # Datos del pedido
    balon = models.ForeignKey(
        TipoBalon, on_delete=models.PROTECT, verbose_name="Tipo de balón"
    )
    cantidad_balon = models.PositiveIntegerField(default=1, verbose_name="Cantidad")
    metodo_pago = models.CharField(
        max_length=20,
        choices=[
            ("efectivo", "Efectivo"),
            ("tarjeta", "Tarjeta"),
            ("transferencia", "Transferencia"),
        ],
        default="efectivo",
    )
    monto = models.DecimalField(
        max_digits=10, decimal_places=0, default=0, verbose_name="Monto total"
    )

    # Metadatos
    registrador = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pedidos_registrados",
        verbose_name="Registrado por",
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
