"""
Management command para cerrar automáticamente sobres diarios abiertos de días anteriores.
Ejecutar con: python manage.py cerrar_sobres_automatico
O programar con Cron: 0 23 * * * python manage.py cerrar_sobres_automatico
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Count, Q
from mockups.models import SobreDiario, Usuario
from datetime import timedelta
from zoneinfo import ZoneInfo


class Command(BaseCommand):
    help = 'Cierra automáticamente sobres abiertos de días anteriores que tengan al menos 1 pedido'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias-atras',
            type=int,
            default=1,
            help='Número de días atrás a buscar para cierre automático (default: 1 = ayer)',
        )

    def handle(self, *args, **options):
        dias_atras = options['dias_atras']
        
        # Obtener fecha objetivo (ayer por defecto)
        tz_chile = ZoneInfo('America/Santiago')
        hoy_chile = timezone.now().astimezone(tz_chile).date()
        fecha_objetivo = hoy_chile - timedelta(days=dias_atras)

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS(
            f'CIERRE AUTOMÁTICO DE SOBRES - {fecha_objetivo.strftime("%d/%m/%Y")}'
        ))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))

        # Buscar sobres abiertos de esa fecha
        sobres_abiertos = SobreDiario.objects.filter(
            fecha_correspondiente=fecha_objetivo,
            cerrado=False
        ).select_related('trabajador', 'creado_por').prefetch_related('lineas')

        self.stdout.write(f'Sobres abiertos encontrados: {sobres_abiertos.count()}\n')

        if not sobres_abiertos.exists():
            self.stdout.write(self.style.WARNING('No hay sobres para cerrar.'))
            self.stdout.write(self.style.SUCCESS('\n' + '='*80 + '\n'))
            return

        # Usuario del sistema para registrar cierre automático
        usuario_sistema, _ = Usuario.objects.get_or_create(
            username='sistema_automatico',
            defaults={
                'first_name': 'Sistema',
                'last_name': 'Automático',
                'email': 'sistema@kimgas.local',
                'is_active': False,
                'rol': 'admin',
            }
        )

        cerrados = 0
        saltados = 0

        for sobre in sobres_abiertos:
            # Verificar si tiene al menos 1 línea con cantidad
            tiene_lineas_con_cantidad = sobre.lineas.filter(
                Q(cantidad_calculada__gt=0) | Q(cantidad_declarada__gt=0)
            ).exists()

            if not tiene_lineas_con_cantidad:
                self.stdout.write(
                    self.style.WARNING(
                        f'⏭️  Sobre #{sobre.id} ({sobre.get_tipo_display()}) - '
                        f'SIN PEDIDOS - Saltado'
                    )
                )
                saltados += 1
                continue

            # Cerrar el sobre
            sobre.cerrado = True
            sobre.declarado_el = timezone.now()
            sobre.nota_cierre = f'Cierre automático a las {timezone.now().astimezone(tz_chile).strftime("%H:%M:%S")}'
            sobre.save()

            trabajador_info = sobre.trabajador.get_full_name() if sobre.trabajador else "Bodega"
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ Sobre #{sobre.id} ({trabajador_info}) - CERRADO\n'
                    f'   - Líneas: {sobre.lineas.count()}\n'
                    f'   - Monto: ${sobre.monto_calculado_app:,}\n'
                    f'   - Cierre: {sobre.declarado_el.astimezone(tz_chile).strftime("%d/%m/%Y %H:%M:%S")}'
                )
            )
            cerrados += 1

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('RESUMEN DEL CIERRE AUTOMÁTICO:'))
        self.stdout.write(self.style.SUCCESS('='*80))
        self.stdout.write(f'✅ Cerrados:  {cerrados}')
        self.stdout.write(f'⏭️  Saltados: {saltados}')
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))
