from django.core.management.base import BaseCommand
from django.db import transaction

from mockups.models import Pedido, Sector


class Command(BaseCommand):
    help = (
        "Importa al catálogo Sector los sectores hardcodeados actuales de Pedido.SECTORES. "
        "No modifica pedidos existentes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra lo que se haría sin guardar cambios en base de datos.',
        )
        parser.add_argument(
            '--actualizar',
            action='store_true',
            help='Actualiza zona, orden y activo en sectores ya existentes según Pedido.SECTORES.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        actualizar = options['actualizar']

        sectores_fuente = self._obtener_sectores_fuente()
        nombres = [item['nombre'] for item in sectores_fuente]
        duplicados = sorted({nombre for nombre in nombres if nombres.count(nombre) > 1})

        if duplicados:
            self.stdout.write(self.style.ERROR('Se detectaron nombres de sector duplicados en Pedido.SECTORES:'))
            for nombre in duplicados:
                self.stdout.write(self.style.ERROR(f' - {nombre}'))
            return

        self.stdout.write(self.style.SUCCESS('\n' + '=' * 80))
        self.stdout.write(self.style.SUCCESS('IMPORTACION DE SECTORES ACTUALES'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(f'Sectores fuente: {len(sectores_fuente)}')
        self.stdout.write(f'Dry-run: {"si" if dry_run else "no"}')
        self.stdout.write(f'Actualizar existentes: {"si" if actualizar else "no"}\n')

        creados = 0
        actualizados = 0
        sin_cambios = 0

        with transaction.atomic():
            for item in sectores_fuente:
                sector, creado = Sector.objects.get_or_create(
                    nombre=item['nombre'],
                    defaults={
                        'zona': item['zona'],
                        'activo': True,
                    }
                )

                if creado:
                    creados += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"+ Creado: {sector.nombre} | zona={item['zona']}"
                        )
                    )
                    continue

                if not actualizar:
                    sin_cambios += 1
                    self.stdout.write(self.style.WARNING(f'= Ya existe: {sector.nombre}'))
                    continue

                cambios = []
                if sector.zona != item['zona']:
                    cambios.append(f'zona: {sector.zona} -> {item["zona"]}')
                    sector.zona = item['zona']
                if not sector.activo:
                    cambios.append('activo: False -> True')
                    sector.activo = True

                if cambios:
                    sector.save(update_fields=['zona', 'activo', 'actualizado_el'])
                    actualizados += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"~ Actualizado: {sector.nombre} | {'; '.join(cambios)}")
                    )
                else:
                    sin_cambios += 1
                    self.stdout.write(self.style.WARNING(f'= Sin cambios: {sector.nombre}'))

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING('\nDry-run activado: se revirtieron todos los cambios.'))

        self.stdout.write(self.style.SUCCESS('\n' + '=' * 80))
        self.stdout.write(self.style.SUCCESS('RESUMEN'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(f'Creados:      {creados}')
        self.stdout.write(f'Actualizados: {actualizados}')
        self.stdout.write(f'Sin cambios:  {sin_cambios}')
        self.stdout.write(self.style.SUCCESS('=' * 80 + '\n'))

    def _obtener_sectores_fuente(self):
        sectores = []
        for nombre, etiqueta in Pedido.SECTORES:
            sectores.append({
                'nombre': nombre,
                'zona': self._inferir_zona(etiqueta),
            })
        return sectores

    def _inferir_zona(self, etiqueta):
        etiqueta = (etiqueta or '').upper()
        if etiqueta.startswith('[SUR]'):
            return 'sur'
        if etiqueta.startswith('[NORTE]'):
            return 'norte'
        return 'otro'