# mockups/management/commands/crear_pedidos.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from faker import Faker
import random

# IMPORT CORRECTO (¡este es el cambio clave!)
from mockups.models import Usuario, Cliente, Pedido, PrecioBalon

fake = Faker("es_CL")


class Command(BaseCommand):
    help = "Genera al menos 30 pedidos de prueba con datos realistas"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count", type=int, default=30, help="Cantidad de pedidos a generar"
        )

    def handle(self, *args, **options):
        count = max(options["count"], 30)
        self.stdout.write(f"Generando {count} pedidos de prueba...")

        # === 1. Asegurar precios de balones ===
        tipos = ["5kg", "11kg", "15kg", "45kg"]
        for tipo in tipos:
            PrecioBalon.objects.get_or_create(
                tipo=tipo,
                defaults={
                    "precio": random.choice([12990, 18990, 24990, 68990]),
                    "activo": True,
                },
            )

        # === 2. Crear telefonistas si no hay ===
        if Usuario.objects.filter(rol="telefonista").count() < 3:
            for i in range(1, 4):
                Usuario.objects.create_user(
                    username=f"tele{i}",
                    password="123",
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    rol="telefonista",
                )

        # === 3. Crear clientes si faltan ===
        if Cliente.objects.count() < 15:
            for _ in range(15):
                Cliente.objects.create(
                    nombre=fake.name(),
                    telefono=fake.phone_number()[:15],
                    direccion=fake.address().replace("\n", ", "),
                )

        # === 4. Generar pedidos ===
        telefonistas = list(Usuario.objects.filter(rol="telefonista"))
        clientes = list(Cliente.objects.all())
        precios = list(PrecioBalon.objects.filter(activo=True))
        estados = ["pendiente", "en_ruta", "entregado", "cancelado"]
        origenes = ["telefono", "web", "local"]

        creados = 0
        for _ in range(count):
            precio = random.choice(precios)
            cantidad = random.randint(1, 4)
            Pedido.objects.create(
                cliente=random.choice(clientes),
                telefonista=random.choice(telefonistas),
                direccion_entrega=fake.address().replace("\n", ", "),
                balon=precio.tipo,
                cantidad_balon=cantidad,
                monto=precio.precio * cantidad,
                metodo_pago=random.choice(["efectivo", "tarjeta"]),
                estado=random.choice(estados),
                origen=random.choice(origenes),
                fecha=fake.date_time_between("-30d", "now"),
            )
            creados += 1

        self.stdout.write(
            self.style.SUCCESS(f"¡Listo! {creados} pedidos generados correctamente.")
        )
