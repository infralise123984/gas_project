# mockups/models.py
# GasFácil - Versión con múltiples balones por pedido + precios diferenciados
# Enero 2026 - KIM GAS Rancagua

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify
from django.core.exceptions import ValidationError

from mockups.fields import EncryptedTextField

import re


class Bodega(models.Model):
    """Sucursal o unidad de negocio independiente."""
    nombre = models.CharField(
        max_length=100, unique=True, verbose_name="Nombre"
    )
    direccion = models.CharField(
        max_length=250, blank=True, verbose_name="Dirección"
    )
    activo = models.BooleanField(default=True, verbose_name="Activo")
    creado_el = models.DateTimeField(auto_now_add=True, verbose_name="Creado el")

    class Meta:
        verbose_name = "Bodega"
        verbose_name_plural = "Bodegas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


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

    # 2FA (TOTP opcional)
    totp_secret = EncryptedTextField(
        blank=True, null=True,
        verbose_name="Secreto TOTP"
    )
    totp_activo = models.BooleanField(default=False, verbose_name="2FA activo")
    totp_ultimo_verificado = models.DateTimeField(
        blank=True, null=True, verbose_name="Último código 2FA verificado en"
    )

    # Multi-bodega
    bodega = models.ForeignKey(
        'Bodega',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='trabajadores',
        verbose_name="Bodega asignada"
    )

    def __str__(self):
        nombre = self.get_full_name().strip() or self.username
        return f"{nombre} ({self.get_rol_display()})"

    def clean(self):
        """Valida consistencia de TOTP: no permitir 2FA activo sin secreto."""
        if self.totp_activo and not self.totp_secret:
            raise ValidationError({
                'totp_activo': 'No se puede activar 2FA sin un secreto TOTP configurado.'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

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

    TIPOS_GAS = [
        ("normal", "Clásico"),
        ("catalitico", "Catalítico"),
        ("aluminio", "Aluminio"),
    ]
    
    tipo_gas = models.CharField(
        max_length=20,
        choices=TIPOS_GAS,
        default="normal",
        verbose_name="Tipo de gas"
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
        ordering = ["-peso_neto_gas", "tipo_gas"]


class HistorialPrecioBalon(models.Model):
    # Snapshot del nombre del balón en el momento del cambio (para que se entienda de qué se trata sin FK)
    nombre_balon = models.CharField(
        max_length=100,
        verbose_name="Nombre del Balón (snapshot)",
        null=True,
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


class Sector(models.Model):
    ZONAS = [
        ("sur", "Sur"),
        ("norte", "Norte"),
        ("otro", "Otro / Sin zona"),
    ]

    nombre = models.CharField(
        max_length=100,
        verbose_name="Nombre",
        help_text="Usa el mismo texto que hoy se guarda en Pedido.sector para facilitar la futura migración."
    )
    bodega = models.ForeignKey(
        'Bodega',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='sectores',
        verbose_name="Bodega"
    )
    codigo = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        verbose_name="Código interno",
        help_text="Identificador estable para integraciones futuras. Si lo dejas vacío, se genera automáticamente."
    )
    zona = models.CharField(
        max_length=20,
        choices=ZONAS,
        default="otro",
        verbose_name="Zona"
    )
    activo = models.BooleanField(default=True, verbose_name="Activo")
    creado_el = models.DateTimeField(auto_now_add=True, verbose_name="Creado el")
    actualizado_el = models.DateTimeField(auto_now=True, verbose_name="Actualizado el")

    def save(self, *args, **kwargs):
        if not self.codigo and self.nombre:
            base_codigo = slugify(self.nombre) or "sector"
            codigo = base_codigo[:120]
            sufijo = 2

            while Sector.objects.exclude(pk=self.pk).filter(codigo=codigo).exists():
                extra = f"-{sufijo}"
                codigo = f"{base_codigo[:120 - len(extra)]}{extra}"
                sufijo += 1

            self.codigo = codigo

        super().save(*args, **kwargs)

    @property
    def etiqueta(self):
        if self.zona == "otro":
            return self.nombre
        return f"[{self.get_zona_display().upper()}] {self.nombre}"

    def __str__(self):
        return self.etiqueta

    class Meta:
        verbose_name = "Sector"
        verbose_name_plural = "Sectores"
        ordering = ["zona", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=['nombre', 'bodega'],
                name='unique_nombre_bodega'
            )
        ]


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

    SECTORES = [
        # Sector Sur
        ("Población José Olivares",     "[SUR] Población José Olivares"),
        ("Población Raccevich",         "[SUR] Población Raccevich"),
        ("Población Valenzuela",        "[SUR] Población Valenzuela"),
        ("Quinta Nanito",               "[SUR] Quinta Nanito"),
        ("Población Rancagua Norte",    "[SUR] Población Rancagua Norte"),
        ("Población Urmeneta",          "[SUR] Población Urmeneta"),
        ("Población Recreo",            "[SUR] Población Recreo"),
        ("Buenos Amigos",               "[SUR] Buenos Amigos"),
        ("Avenida Circunvalación",      "[SUR] Avenida Circunvalación"),
        ("Los Alpes",                   "[SUR] Los Alpes"),
        ("Pucará",                      "[SUR] Pucará"),
        ("Manso de Velasco",            "[SUR] Manso de Velasco"),
        ("Villa Codemis",               "[SUR] Villa Codemis"),
        ("Villa Túpahue",               "[SUR] Villa Túpahue"),
        ("Los Cipreces",                "[SUR] Los Cipreces"),
        ("Villa Inglesa",               "[SUR] Villa Inglesa"),
        ("William Bradem",              "[SUR] William Bradem"),
        ("Centenario",                  "[SUR] Centenario"),
        ("Avenida Illanes",             "[SUR] Avenida Illanes"),
        ("Avenida República de Chile",  "[SUR] Avenida República de Chile"),
        ("Villa Don Enrique",           "[SUR] Villa Don Enrique"),
        ("Población Ramón Torres",      "[SUR] Población Ramón Torres"),
        ("Villa Diego de Almagro",      "[SUR] Villa Diego de Almagro"),
        ("Villa Los Andes",             "[SUR] Villa Los Andes"),
        ("Condominio Brisas del Sur",   "[SUR] Condominio Brisas del Sur"),
        ("Villa Shiaponi",              "[SUR] Villa Shiaponi"),
        ("Grecia",                      "[SUR] Grecia"),
        
        # Sector Norte
        ("villa las cañadas",           "[NORTE] Villa Las Cañadas"),
        ("Villa Tuniche",               "[NORTE] Villa Tuniche"),
        ("Villa Araucaria",             "[NORTE] Villa Araucaria"),
        ("Villa La Leonera",            "[NORTE] Villa La Leonera"),
        ("Villa La Capilla",            "[NORTE] Villa La Capilla"),
        ("Población Nelson Pereira",    "[NORTE] Población Nelson Pereira"),
        ("Villa El Sol",                "[NORTE] Villa El Sol"),
        ("Villa Girasoles",             "[NORTE] Villa Girasoles"),
        ("Villa Don Mateo",             "[NORTE] Villa Don Mateo"),
        ("Villa Los Arcángeles",        "[NORTE] Villa Los Arcángeles"),
        ("Villa Almonacid",             "[NORTE] Villa Almonacid"),
        ("Villa Don Vicente",           "[NORTE] Villa Don Vicente"),
        ("Villa Alodia",                "[NORTE] Villa Alodia"),
        ("Huertos de Kennedy",          "[NORTE] Huertos de Kennedy"),
        ("Otro",                        "Otro"),
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

    # Multi-bodega
    bodega = models.ForeignKey(
        'Bodega',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='pedidos',
        verbose_name="Bodega"
    )

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
            models.Index(fields=["fecha"], name="mockups_ped_fecha_49bdbd_idx"),
            models.Index(fields=["estado"], name="mockups_ped_estado_372ad8_idx"),
            models.Index(fields=["origen"], name="mockups_ped_origen_a47a8e_idx"),
            models.Index(fields=["registrador"], name="mockups_ped_registr_b26b12_idx"),
            models.Index(fields=["entregador"], name="mockups_ped_entrega_5c30fe_idx"),
            models.Index(fields=["sector"], name="mockups_ped_sector_37b274_idx"),
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
        if self.precio_venta_unitario is None:
            return None
        return self.precio_venta_unitario * self.cantidad

    @property
    def ganancia(self):
        if self.precio_venta_unitario is None or self.precio_compra_unitario is None:
            return None
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
    
    # Multi-bodega
    bodega = models.ForeignKey(
        'Bodega',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='sobres',
        verbose_name="Bodega"
    )
    
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
        indexes = [
            models.Index(fields=['fecha_correspondiente'], name='mockups_sob_fecha_c_da54a1_idx'),
            models.Index(
                fields=['fecha_correspondiente', 'tipo', 'cerrado'],
                name='mockups_sob_fecha_c_b292d1_idx',
            ),
            models.Index(
                fields=['trabajador', 'fecha_correspondiente'],
                name='mockups_sob_trabaja_f16ee6_idx',
            ),
        ]

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
        ('pits', 'Pits'),
        ('monedas', 'Monedas'),
        ('cupon', 'Cupón'),
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


# ──────────────────────────────────────────────────────────────
# MODELO PARA SUSCRIPCIONES PUSH (Web Push API)
# ──────────────────────────────────────────────────────────────
class PushSubscription(models.Model):
    """
    Almacena las suscripciones de Push Notifications para cada usuario.
    Permite enviar notificaciones incluso con la app cerrada.
    """
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
        verbose_name="Usuario"
    )
    # Usamos CharField con longitud fija para permitir índices en MySQL
    # Los endpoints de Web Push típicamente son ~300-500 caracteres
    endpoint = models.CharField(
        max_length=500,
        verbose_name="Endpoint URL",
        help_text="URL única del servicio push del navegador (truncado a 500 chars)"
    )
    p256dh = models.CharField(
        max_length=255,
        verbose_name="Clave P256DH",
        help_text="Clave pública del cliente para encriptación"
    )
    auth = models.CharField(
        max_length=255,
        verbose_name="Auth Secret",
        help_text="Secret de autenticación del cliente"
    )
    activa = models.BooleanField(
        default=True,
        verbose_name="Activa",
        help_text="Desactivar si la suscripción expira o falla"
    )
    user_agent = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="User Agent",
        help_text="Navegador/dispositivo del usuario"
    )
    creada_el = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creada el"
    )
    actualizada_el = models.DateTimeField(
        auto_now=True,
        verbose_name="Última actualización"
    )

    class Meta:
        verbose_name = "Suscripción Push"
        verbose_name_plural = "Suscripciones Push"
        ordering = ['-creada_el']
        # Nota: No usamos unique_together aquí porque los endpoints pueden
        # exceder el límite de índice de MySQL. La unicidad se maneja en views.py

    def __str__(self):
        dispositivo = 'Móvil' if 'Mobile' in self.user_agent else 'Desktop'
        estado = '✓' if self.activa else '✗'
        return f"{self.usuario.username} - {dispositivo} [{estado}]"

    def to_subscription_info(self):
        """Retorna dict en formato compatible con pywebpush"""
        return {
            'endpoint': self.endpoint,
            'keys': {
                'p256dh': self.p256dh,
                'auth': self.auth
            }
        }


# ──────────────────────────────────────────────────────────────
# MODELO DE AUDITORÍA
# ──────────────────────────────────────────────────────────────
class AuditoriaAccion(models.Model):
    """
    Registro de auditoría para acciones importantes en el sistema.
    Permite rastrear quién hizo qué y cuándo.
    """
    TIPOS = [
        ('LOGIN_OK', 'Inicio de sesión exitoso'),
        ('LOGIN_FAIL', 'Intento de login fallido'),
        ('LOGOUT', 'Cierre de sesión'),
        ('USER_CREATE', 'Usuario creado'),
        ('USER_UPDATE', 'Usuario modificado'),
        ('USER_DELETE', 'Usuario eliminado'),
        ('PASSWORD_CHANGE', 'Contraseña cambiada'),
        ('PEDIDO_CREATE', 'Pedido creado'),
        ('PEDIDO_UPDATE', 'Pedido modificado'),
        ('PEDIDO_DELETE', 'Pedido eliminado'),
        ('PEDIDO_ESTADO', 'Estado de pedido cambiado'),
        ('PRECIO_UPDATE', 'Precio actualizado'),
        ('BALON_CREATE', 'Balón creado'),
        ('BALON_UPDATE', 'Balón modificado'),
        ('BALON_DELETE', 'Balón eliminado'),
        ('SOBRE_CREATE', 'Sobre creado'),
        ('SOBRE_CLOSE', 'Sobre cerrado'),
        ('SOBRE_UPDATE', 'Sobre modificado'),
        ('PERM_DENIED', 'Acceso denegado'),
        ('SUSPICIOUS', 'Actividad sospechosa'),
        ('EXPORT_DATA', 'Exportación de datos'),
        ('CONFIG_CHANGE', 'Configuración cambiada'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPOS, db_index=True, verbose_name="Tipo de acción")
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    username = models.CharField(max_length=150, blank=True, verbose_name="Username (snapshot)",
                                help_text="Guardamos el username por si el usuario es eliminado")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Dirección IP")
    user_agent = models.TextField(blank=True, verbose_name="User Agent")
    descripcion = models.TextField(blank=True, verbose_name="Descripción detallada")
    objeto_tipo = models.CharField(max_length=100, blank=True, verbose_name="Tipo de objeto afectado",
                                   help_text="Ej: Pedido, Usuario, TipoBalon")
    objeto_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="ID del objeto afectado")
    objeto_repr = models.CharField(max_length=255, blank=True, verbose_name="Representación del objeto",
                                   help_text="Snapshot del __str__ del objeto")
    datos_anteriores = models.JSONField(null=True, blank=True, verbose_name="Datos anteriores (JSON)")
    datos_nuevos = models.JSONField(null=True, blank=True, verbose_name="Datos nuevos (JSON)")
    fecha = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Fecha y hora")

    class Meta:
        verbose_name = "Registro de Auditoría"
        verbose_name_plural = "Registros de Auditoría"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['tipo', 'fecha']),
            models.Index(fields=['usuario', 'fecha']),
            models.Index(fields=['objeto_tipo', 'objeto_id']),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.username or 'Anónimo'} - {self.fecha}"

    @classmethod
    def registrar(cls, request, tipo, descripcion='', objeto=None, datos_anteriores=None, datos_nuevos=None):
        """
        Método helper para registrar una acción de auditoría.
        
        Args:
            request: HttpRequest de Django
            tipo: Tipo de acción (ver TIPOS)
            descripcion: Descripción detallada
            objeto: Objeto afectado (cualquier modelo)
            datos_anteriores: Dict con datos antes del cambio
            datos_nuevos: Dict con datos después del cambio
        """
        # Obtener IP
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.META.get('REMOTE_ADDR')
        
        registro = cls(
            tipo=tipo,
            usuario=request.user if request.user.is_authenticated else None,
            username=request.user.username if request.user.is_authenticated else '',
            ip_address=ip,
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            descripcion=descripcion,
            datos_anteriores=datos_anteriores,
            datos_nuevos=datos_nuevos,
        )
        
        if objeto:
            registro.objeto_tipo = objeto.__class__.__name__
            registro.objeto_id = objeto.pk
            registro.objeto_repr = str(objeto)[:255]
        
        registro.save()
        return registro
