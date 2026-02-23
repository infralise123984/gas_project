"""
Management command para debugar pedidos
Ejecutar con: python manage.py debug_pedidos
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Count, Sum
from mockups.models import Pedido, TipoBalon, SobreDiario
from datetime import timedelta, datetime, time
from zoneinfo import ZoneInfo


def today_chile():
    """Retorna la fecha actual en zona horaria de Chile"""
    tz_chile = ZoneInfo('America/Santiago')
    return timezone.now().astimezone(tz_chile).date()


class Command(BaseCommand):
    help = 'Debug: Analiza por qué no aparecen los pedidos en el sobre de bodega'

    def handle(self, *args, **options):
        # Obtener fecha de hoy (Chile) - USAR LA MISMA QUE EN VIEWS.PY
        hoy = today_chile()
        ayer = hoy - timedelta(days=1)

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('DEBUG: ANÁLISIS DE PEDIDOS PARA SOBRE DE BODEGA'))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))

        # 1. Verificar si hay pedidos en la BD
        total_pedidos = Pedido.objects.count()
        self.stdout.write(f'[1] Total de pedidos en la BD: {total_pedidos}')

        # 2. Pedidos por estado
        self.stdout.write(f'\n[2] Pedidos por ESTADO:')
        for estado, label in Pedido.ESTADOS:
            count = Pedido.objects.filter(estado=estado).count()
            self.stdout.write(f'    - {estado}: {count}')

        # 3. Pedidos por origen
        self.stdout.write(f'\n[3] Pedidos por ORIGEN:')
        for origen, label in Pedido.ORIGENES:
            count = Pedido.objects.filter(origen=origen).count()
            self.stdout.write(f'    - {origen}: {count}')

        # 4. Pedidos hoy (comparación: fecha__date vs rango)
        self.stdout.write(f'\n[4] Pedidos HOY - MÉTODO fecha__date (fecha__date={hoy}):')
        pedidos_hoy_method1 = Pedido.objects.filter(fecha__date=hoy).count()
        self.stdout.write(f'    Total: {pedidos_hoy_method1}')

        # 4b. Pedidos hoy USANDO RANGO (como en la solución corregida)
        self.stdout.write(f'\n[4b] Pedidos HOY - MÉTODO RANGO (nuevo):')
        tz_chile = ZoneInfo('America/Santiago')
        tz_utc = ZoneInfo('UTC')
        inicio_dia = datetime.combine(hoy, time.min)
        inicio_dia = timezone.make_aware(inicio_dia, tz_chile).astimezone(tz_utc)
        fin_dia = datetime.combine(hoy, time.max)
        fin_dia = timezone.make_aware(fin_dia, tz_chile).astimezone(tz_utc)
        
        pedidos_hoy_method2 = Pedido.objects.filter(fecha__gte=inicio_dia, fecha__lte=fin_dia).count()
        self.stdout.write(f'    Total: {pedidos_hoy_method2}')
        self.stdout.write(f'    Rango UTC: {inicio_dia} a {fin_dia}')

        # 5. Pedidos ayer
        pedidos_ayer = Pedido.objects.filter(fecha__date=ayer).count()
        self.stdout.write(f'\n[5] Pedidos AYER (fecha__date={ayer}): {pedidos_ayer}')

        # 6. Pedidos LOCALES entregados (el filtro actual para bodega)
        self.stdout.write(f'\n[6] Pedidos LOCAL + ENTREGADO (sin límite de fecha):')
        qs_bodega = Pedido.objects.filter(origen='local', estado='entregado')
        self.stdout.write(f'    Total: {qs_bodega.count()}')
        for p in qs_bodega[:5]:
            detalles_count = p.detalles.count()
            self.stdout.write(
                f'    - Pedido #{p.id}: origen={p.origen}, estado={p.estado}, '
                f'fecha={p.fecha.date() if p.fecha else None}, detalles={detalles_count}'
            )

        # 7. Pedidos LOCALES entregados HOY (metodo antiguo)
        self.stdout.write(f'\n[7] Pedidos LOCAL + ENTREGADO + HOY (método fecha__date):')
        qs_bodega_hoy_old = Pedido.objects.filter(fecha__date=hoy, origen='local', estado='entregado')
        self.stdout.write(f'    Total: {qs_bodega_hoy_old.count()}')

        # 7b. Pedidos LOCALES entregados HOY (nuevo metodo)
        self.stdout.write(f'\n[7b] Pedidos LOCAL + ENTREGADO + HOY (método RANGO):')
        qs_bodega_hoy_new = Pedido.objects.filter(
            fecha__gte=inicio_dia, 
            fecha__lte=fin_dia, 
            origen='local', 
            estado='entregado'
        )
        self.stdout.write(f'    Total: {qs_bodega_hoy_new.count()}')
        for p in qs_bodega_hoy_new[:5]:
            detalles_count = p.detalles.count()
            self.stdout.write(
                f'    - Pedido #{p.id}: origen={p.origen}, estado={p.estado}, '
                f'fecha={p.fecha.date() if p.fecha else None}, detalles={detalles_count}'
            )

        # 8. Verificar si los pedidos tienen detalles
        self.stdout.write(f'\n[8] Pedidos SIN detalles:')
        pedidos_sin_detalles = Pedido.objects.annotate(detalle_count=Count('detalles')).filter(detalle_count=0)
        self.stdout.write(f'    Total: {pedidos_sin_detalles.count()}')

        # 9. Verificar balones disponibles
        self.stdout.write(f'\n[9] Balones ACTIVOS:')
        balones = TipoBalon.objects.filter(activo=True).order_by('peso_neto_gas')
        self.stdout.write(f'    Total: {balones.count()}')
        for b in balones:
            self.stdout.write(f'    - {b.nombre} ({b.peso_neto_gas}kg)')

        # 10. Detalles de pedido para cada balón (usando nuevo método)
        self.stdout.write(f'\n[10] Detalles por balón (LOCAL + ENTREGADO + HOY - MÉTODO RANGO):')
        for balon in balones:
            qty = qs_bodega_hoy_new.filter(detalles__balon=balon).aggregate(total=Sum('detalles__cantidad'))['total'] or 0
            self.stdout.write(f'    - {balon.nombre}: {qty} unidades')

        # 11. Verificar sobres de bodega
        self.stdout.write(f'\n[11] Sobres de BODEGA HOY:')
        sobres_bodega = SobreDiario.objects.filter(fecha__date=hoy, tipo='bodega')
        self.stdout.write(f'    Total: {sobres_bodega.count()}')
        for s in sobres_bodega:
            self.stdout.write(f'    - Sobre #{s.id}: lineas={s.lineas.count()}, cerrado={s.cerrado}')
            for linea in s.lineas.all():
                self.stdout.write(
                    f'      * {linea.balon.nombre}: calculada={linea.cantidad_calculada}, '
                    f'declarada={linea.cantidad_declarada}'
                )

        self.stdout.write(self.style.SUCCESS('\n' + '='*80 + '\n'))
