# mockups/management/commands/crear_usuarios_prueba.py

from django.core.management.base import BaseCommand
from mockups.models import Usuario
from django.contrib.auth import hashers   # ← CORREGIDO

class Command(BaseCommand):
    help = 'Crea un usuario de prueba para cada rol disponible'

    def handle(self, *args, **options):
        usuarios_data = [
            {'username': 'telefonista1', 'rol': 'telefonista', 'nombre': 'Ana', 'apellido': 'Gómez'},
            {'username': 'camionero1',   'rol': 'camionero',   'nombre': 'Carlos', 'apellido': 'Ruiz'},
            {'username': 'bodeguero1',   'rol': 'bodeguero',   'nombre': 'Luis', 'apellido': 'Pérez'},
            {'username': 'jefe1',        'rol': 'jefe',        'nombre': 'María', 'apellido': 'López'},
            {'username': 'admin1',       'rol': 'admin',       'nombre': 'Pedro', 'apellido': 'Soto'},
        ]

        contraseña = 'gas123'

        creados = 0
        for data in usuarios_data:
            username = data['username']
            if Usuario.objects.filter(username=username).exists():
                self.stdout.write(self.style.WARNING(f"Usuario {username} ya existe, saltando..."))
                continue

            Usuario.objects.create(
                username=username,
                first_name=data['nombre'],
                last_name=data['apellido'],
                rol=data['rol'],
                email=f"{username}@gasfacil.local",
                password=hashers.make_password(contraseña),  # ← CORREGIDO
                is_active=True
            )
            creados += 1
            self.stdout.write(self.style.SUCCESS(f"✓ Creado: {username} - Rol: {data['rol']}"))

        self.stdout.write(self.style.SUCCESS(f"\n¡Listo! {creados} usuarios creados. Contraseña: gas123"))