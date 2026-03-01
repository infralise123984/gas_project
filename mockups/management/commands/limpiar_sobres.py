from django.core.management.base import BaseCommand
from django.utils import timezone
from zoneinfo import ZoneInfo
from datetime import datetime
from mockups.models import SobreDiario


class Command(BaseCommand):
    help = 'Elimina sobres de una fecha específica (útil para limpiar durante desarrollo)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fecha',
            type=str,
            default=None,
            help='Fecha en formato YYYY-MM-DD. Por defecto: hoy'
        )
        parser.add_argument(
            '--tipo',
            type=str,
            default=None,
            choices=['bodega', 'camion'],
            help='Tipo de sobre a eliminar (bodega, camion, o ambos si se omite)'
        )
        parser.add_argument(
            '--trabajador',
            type=int,
            default=None,
            help='ID del trabajador (para sobres de camión específico)'
        )

    def handle(self, *args, **options):
        # Determinar fecha
        fecha_str = options.get('fecha')
        if fecha_str:
            try:
                fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            except ValueError:
                self.stdout.write(self.style.ERROR('Formato de fecha inválido. Use YYYY-MM-DD'))
                return
        else:
            tz_chile = ZoneInfo('America/Santiago')
            fecha = timezone.now().astimezone(tz_chile).date()

        # Construir filtro
        filtro = {'fecha_correspondiente': fecha}
        
        if options.get('tipo'):
            filtro['tipo'] = options['tipo']
        
        if options.get('trabajador'):
            filtro['trabajador_id'] = options['trabajador']

        # Buscar sobres
        sobres = SobreDiario.objects.filter(**filtro)
        cantidad = sobres.count()

        if cantidad == 0:
            self.stdout.write(self.style.WARNING(f'No hay sobres para eliminar con los criterios especificados'))
            return

        # Mostrar resumen
        self.stdout.write(f'\n📋 Sobres encontrados: {cantidad}')
        for sobre in sobres:
            lineas = sobre.lineas.count()
            estado = "CERRADO" if sobre.cerrado else "ABIERTO"
            self.stdout.write(f'  - Sobre #{sobre.id} ({sobre.get_tipo_display()}) - {estado} - {lineas} líneas')

        # Confirmar
        respuesta = input('\n⚠️  ¿Estás seguro de que quieres eliminar estos sobres? (s/n): ')
        if respuesta.lower() != 's':
            self.stdout.write(self.style.WARNING('Operación cancelada'))
            return

        # Eliminar
        sobres.delete()
        self.stdout.write(self.style.SUCCESS(f'\n✅ {cantidad} sobres eliminados correctamente'))
