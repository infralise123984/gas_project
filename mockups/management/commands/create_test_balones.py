# mockups/management/commands/create_test_balones.py

from django.core.management.base import BaseCommand
from mockups.models import TipoBalon
from decimal import Decimal


class Command(BaseCommand):
    help = 'Crea balones de prueba realistas para Kim Gas (incluye catalíticos 5–45 kg) - solo testing local'

    def handle(self, *args, **kwargs):
        balones_data = [
            # ── Normales ─────────────────────────────────────────────
            {
                'nombre': '5KG',
                'peso_neto_gas': 5,
                'precio_compra': Decimal('6200'),
                'precio_local': Decimal('9500'),
                'precio_domicilio': Decimal('10500'),
                'activo': True,
            },
            {
                'nombre': '11KG',
                'peso_neto_gas': 11,
                'precio_compra': Decimal('13200'),
                'precio_local': Decimal('17500'),
                'precio_domicilio': Decimal('18900'),
                'activo': True,
            },
            {
                'nombre': '15KG',
                'peso_neto_gas': 15,
                'precio_compra': Decimal('18200'),
                'precio_local': Decimal('21500'),      # ~$18.490–$21.990 promo Kim Gas
                'precio_domicilio': Decimal('23900'),
                'activo': True,
            },
            {
                'nombre': '45KG',
                'peso_neto_gas': 45,
                'precio_compra': Decimal('51500'),
                'precio_local': Decimal('67990'),       # ~$67–70k visto en ofertas
                'precio_domicilio': Decimal('72900'),
                'activo': True,
            },

            # ── Catalíticos (5 a 45 kg) ──────────────────────────────
            {
                'nombre': '5KG cat',
                'peso_neto_gas': 5,
                'precio_compra': Decimal('7800'),      # +~25% por catalítico
                'precio_local': Decimal('12500'),
                'precio_domicilio': Decimal('13500'),
                'activo': True,
            },
            {
                'nombre': '11KG cat',
                'peso_neto_gas': 11,
                'precio_compra': Decimal('16800'),
                'precio_local': Decimal('22900'),
                'precio_domicilio': Decimal('24900'),
                'activo': True,
            },
            {
                'nombre': '15KG cat',
                'peso_neto_gas': 15,
                'precio_compra': Decimal('22800'),
                'precio_local': Decimal('28900'),      # ~$25–30k real catalítico
                'precio_domicilio': Decimal('31900'),
                'activo': True,
            },
            {
                'nombre': '45KG cat',
                'peso_neto_gas': 45,
                'precio_compra': Decimal('64800'),
                'precio_local': Decimal('89900'),      # ~$85–95k estimado cat
                'precio_domicilio': Decimal('95900'),
                'activo': True,
            },
        ]

        creados = 0
        for data in balones_data:
            nombre = data['nombre']
            if TipoBalon.objects.filter(nombre=nombre).exists():
                self.stdout.write(self.style.WARNING(f"Balón '{nombre}' ya existe → saltando"))
                continue

            TipoBalon.objects.create(**data)
            creados += 1
            self.stdout.write(self.style.SUCCESS(f"Balón creado: {nombre} ({data['peso_neto_gas']} kg)"))

        if creados == 0:
            self.stdout.write(self.style.NOTICE("Todos los balones ya existían. No se creó ninguno nuevo."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nSe crearon {creados} balones de prueba (incluyendo catalíticos 5–45 kg)."))

        self.stdout.write(self.style.HTTP_INFO(
            "Precios aproximados feb/2026 (Rancagua/Kim Gas). "
            "Actualiza desde Gestión de Precios según valores reales diarios/semanal."
        ))