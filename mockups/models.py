from django.db import models

class Cliente(models.Model):
    """
    Modelo para almacenar datos de clientes frecuentes.
    Identificado por teléfono (único).
    """
    nombre = models.CharField(max_length=100)
    telefono = models.CharField(max_length=15, unique=True)  # Identificador único
    direccion = models.CharField(max_length=200, blank=True)  # Dirección por defecto
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nombre} ({self.telefono})"

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

class Usuario(models.Model):
    """
    Modelo para usuarios del sistema: telefonista, camionero, jefe, etc.
    """
    ROLES = [
        ('telefonista', 'Telefonista'),
        ('bodeguero', 'Bodeguero'),
        ('camionero', 'Camionero'),
        ('admin', 'Administrador'),
        ('jefe', 'Jefe'),
    ]

    nombre_usuario = models.CharField(max_length=50, unique=True)
    contrasena = models.CharField(max_length=128)  # Mejor usar hashing de Django
    rol = models.CharField(max_length=50, choices=ROLES)

    def __str__(self):
        return f"{self.nombre_usuario} ({self.rol})"

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

class Pedido(models.Model):
    """
    Modelo principal para registrar pedidos de gas.
    Puede estar asociado a un Cliente y a un Usuario (telefonista).
    """
    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('en_ruta', 'En ruta'),
        ('entregado', 'Entregado'),
        ('cancelado', 'Cancelado'),
    ]

    ORIGENES = [
        ('telefono', 'Teléfono'),
        ('web', 'Web'),
        ('local', 'Local'),
    ]

    # Relación con Cliente (puede ser nulo si es cliente no registrado)
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name='pedidos')

    # Dirección de entrega (puede ser diferente a la del cliente)
    direccion_entrega = models.CharField(max_length=200)

    # Datos del pedido
    balon = models.CharField(max_length=50)
    cantidad_balon = models.IntegerField(default=1)
    fecha = models.DateTimeField(auto_now_add=True)  # Fecha + hora
    estado = models.CharField(max_length=50, choices=ESTADOS, default='pendiente')
    origen = models.CharField(max_length=50, choices=ORIGENES)

    # Relación con el usuario que registró el pedido (opcional)
    telefonista = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name='pedidos_creados')

    def __str__(self):
        return f"Pedido {self.id} - {self.cliente.nombre if self.cliente else 'Cliente anónimo'}"

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"