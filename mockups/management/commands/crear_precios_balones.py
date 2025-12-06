# mockups/management/commands/crear_precios_balones.py

from django.core.management.base import BaseCommand
from mockups.models import PrecioBalon


class Command(BaseCommand):
    help = "Crea los precios iniciales de los balones para Chile (solo si no existen)"

    def handle(self, *args, **options):
        precios_iniciales = [
            {"tipo": "5kg", "precio": "7990"},
            {"tipo": "11kg", "precio": "15990"},
            {"tipo": "15kg", "precio": "21990"},
            {"tipo": "45kg", "precio": "54990"},
        ]

        creados = 0
        for item in precios_iniciales:
            obj, created = PrecioBalon.objects.get_or_create(
                tipo=item["tipo"], defaults={"precio": item["precio"], "activo": True}
            )
            if created:
                creados += 1
                precio_formateado = f"$ {int(item['precio']):,}".replace(",", ".")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Creado: {item['tipo']} → {precio_formateado}"
                    )
                )
            else:
                self.stdout.write(self.style.WARNING(f"Ya existe: {item['tipo']}"))

        self.stdout.write(
            self.style.SUCCESS(f"\n¡Listo! {creados} precios creados correctamente.")
        )
        self.stdout.write("\nPuedes modificarlos en /admin/mockups/preciobalon/")
