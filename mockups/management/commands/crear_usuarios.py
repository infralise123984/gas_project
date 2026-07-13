# mockups/management/commands/create_test_users.py
from django.core.management.base import BaseCommand
from mockups.models import Usuario  # Asegúrate de que el import sea correcto según tu app

class Command(BaseCommand):
    help = 'Crea usuarios de prueba para cada rol con contraseña "Contraseña@123" (solo para testing local)'

    def handle(self, *args, **kwargs):
        roles = [
            ("telefonista", "Telefonista", "Test"),
            ("bodeguero", "Bodeguero", "Test"),
            ("camionero", "Camionero", "Test"),
            ("jefe", "Jefe", "Test"),
            ("admin", "Admin", "Test"),
        ]
        
        password = "Contraseña@123"
        
        for rol, first_name, last_name in roles:
            username = f"{rol}_test"
            
            if Usuario.objects.filter(username=username).exists():
                self.stdout.write(self.style.WARNING(f"Usuario {username} ya existe. Saltando..."))
                continue
            
            user = Usuario.objects.create_user(
                username=username,
                first_name=first_name,
                last_name=last_name,
                password=password,
                rol=rol
            )
            
            # Para admin y jefe: acceso al admin site
            if rol in ['jefe', 'admin']:
                user.is_staff = True
            if rol == 'admin':
                user.is_superuser = True
            
            user.save()
            
            self.stdout.write(self.style.SUCCESS(f"Usuario creado: {username} ({rol})"))

        self.stdout.write(self.style.SUCCESS("Todos los usuarios de prueba creados exitosamente."))