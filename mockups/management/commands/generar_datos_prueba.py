"""
Genera pedidos y sobres de prueba para testeo de rendimiento y reportes.

Usa los balones activos y usuarios existentes en la base de datos.
Marca los registros con [DATOS-PRUEBA] para poder limpiarlos después.

Ejemplos:
    python manage.py generar_datos_prueba --dias 30
    python manage.py generar_datos_prueba --desde 2026-01-01 --hasta 2026-03-31 --pedidos-dia 20
    python manage.py generar_datos_prueba --dias 7 --sin-sobres
    python manage.py generar_datos_prueba --limpiar --dias 30
"""

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from mockups.models import (
    DetallePedido,
    LineaGasto,
    LineaPago,
    Pedido,
    Sector,
    SobreDiario,
    Usuario,
)
from mockups.views import get_balones_activos_ordenados, sincronizar_sobre_desde_pedidos

MARCADOR = '[DATOS-PRUEBA]'
TZ_CHILE = ZoneInfo('America/Santiago')
METODOS_PAGO = ['efectivo', 'tarjeta', 'transferencia']
TIPOS_PAGO_SOBRE = ['efectivo', 'transferencia', 'visa', 'abono']


def rango_utc_dia(fecha_objetivo):
    """Inicio y fin del día en UTC para una fecha Chile."""
    tz_utc = ZoneInfo('UTC')
    inicio = timezone.make_aware(datetime.combine(fecha_objetivo, time.min), TZ_CHILE).astimezone(tz_utc)
    fin = timezone.make_aware(datetime.combine(fecha_objetivo, time.max), TZ_CHILE).astimezone(tz_utc)
    return inicio, fin


def fecha_aleatoria_en_dia(fecha_objetivo, rng):
    """Datetime consciente de zona en un horario laboral aleatorio."""
    hora = rng.randint(8, 20)
    minuto = rng.randint(0, 59)
    dt_local = datetime.combine(fecha_objetivo, time(hora, minuto, rng.randint(0, 59)))
    return timezone.make_aware(dt_local, TZ_CHILE)


def pesos_balones(balones, rng):
    """Pesos relativos: más ventas de 11 y 15 kg."""
    pesos = []
    for b in balones:
        if b.peso_neto_gas in (11, 15):
            pesos.append(4)
        elif b.peso_neto_gas == 5:
            pesos.append(3)
        elif b.peso_neto_gas == 45:
            pesos.append(1)
        else:
            pesos.append(2)
    return pesos


class Command(BaseCommand):
    help = (
        'Genera ventas de prueba (domicilio, bodega, tarreo) y sobres diarios '
        'usando balones y usuarios existentes. Solo para entornos de desarrollo/test.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=30,
            help='Cantidad de días hacia atrás desde hoy (default: 30). Ignorado si usas --desde/--hasta.',
        )
        parser.add_argument(
            '--desde',
            type=str,
            default=None,
            help='Fecha inicio YYYY-MM-DD (opcional).',
        )
        parser.add_argument(
            '--hasta',
            type=str,
            default=None,
            help='Fecha fin YYYY-MM-DD (opcional, default: hoy Chile).',
        )
        parser.add_argument(
            '--pedidos-dia',
            type=int,
            default=12,
            help='Promedio de pedidos por día (se aplica variación aleatoria ±40%%).',
        )
        parser.add_argument(
            '--ratio-telefono',
            type=int,
            default=40,
            help='Porcentaje aproximado de pedidos domicilio/teléfono (default: 40).',
        )
        parser.add_argument(
            '--ratio-local',
            type=int,
            default=35,
            help='Porcentaje aproximado de ventas bodega/local (default: 35).',
        )
        parser.add_argument(
            '--ratio-tarreo',
            type=int,
            default=25,
            help='Porcentaje aproximado de ventas tarreo (default: 25).',
        )
        parser.add_argument(
            '--seed',
            type=int,
            default=None,
            help='Semilla aleatoria para datos reproducibles.',
        )
        parser.add_argument(
            '--sin-sobres',
            action='store_true',
            help='Solo genera pedidos, sin crear ni cerrar sobres.',
        )
        parser.add_argument(
            '--sobres-abiertos',
            action='store_true',
            help='Deja los sobres abiertos (por defecto se cierran).',
        )
        parser.add_argument(
            '--limpiar',
            action='store_true',
            help=f'Elimina pedidos y sobres marcados con {MARCADOR} antes de generar.',
        )
        parser.add_argument(
            '--solo-limpiar',
            action='store_true',
            help=f'Solo elimina datos de prueba ({MARCADOR}), sin generar nuevos.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra el plan sin escribir en la base de datos.',
        )

    def handle(self, *args, **options):
        rng = random.Random(options['seed'])

        if options['solo_limpiar']:
            if options['dry_run']:
                self.stdout.write(self.style.WARNING('Dry-run: no se elimina nada.'))
                return
            self._limpiar_datos_prueba()
            return

        if options['limpiar'] and not options['dry_run']:
            self._limpiar_datos_prueba()

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry-run: no se escribe en la base de datos.'))
            self._plan(options, rng)
            return

        balones = list(get_balones_activos_ordenados())
        if not balones:
            raise CommandError(
                'No hay balones activos. Crea balones primero '
                '(ej: python manage.py create_test_balones).'
            )

        usuarios = self._resolver_usuarios()
        sectores = self._resolver_sectores()
        fechas = self._resolver_fechas(options)

        ratios = self._normalizar_ratios(
            options['ratio_telefono'],
            options['ratio_local'],
            options['ratio_tarreo'],
        )

        self.stdout.write(self.style.SUCCESS('\n' + '=' * 72))
        self.stdout.write(self.style.SUCCESS('GENERACIÓN DE DATOS DE PRUEBA — Kim Gas'))
        self.stdout.write(self.style.SUCCESS('=' * 72))
        self.stdout.write(f'Balones activos: {len(balones)}')
        self.stdout.write(f'Camioneros: {len(usuarios["camioneros"])}')
        self.stdout.write(f'Días a generar: {len(fechas)} ({fechas[0]} → {fechas[-1]})')
        self.stdout.write(f'Promedio pedidos/día: {options["pedidos_dia"]}')
        self.stdout.write(f'Mix: teléfono {ratios[0]}% · local {ratios[1]}% · tarreo {ratios[2]}%')
        if options['seed'] is not None:
            self.stdout.write(f'Semilla: {options["seed"]}')
        self.stdout.write('')

        total_pedidos = 0
        total_detalles = 0
        total_sobres = 0
        pesos = pesos_balones(balones, rng)

        for fecha_dia in fechas:
            cantidad_dia = max(1, int(options['pedidos_dia'] * rng.uniform(0.6, 1.4)))
            pedidos_dia = 0

            with transaction.atomic():
                for _ in range(cantidad_dia):
                    tipo = rng.choices(
                        ['telefono', 'local', 'tarreo'],
                        weights=ratios,
                        k=1,
                    )[0]
                    pedido, n_det = self._crear_pedido(
                        fecha_dia=fecha_dia,
                        tipo=tipo,
                        balones=balones,
                        pesos=pesos,
                        usuarios=usuarios,
                        sectores=sectores,
                        rng=rng,
                    )
                    if pedido:
                        pedidos_dia += 1
                        total_detalles += n_det

                if not options['sin_sobres'] and pedidos_dia > 0:
                    total_sobres += self._crear_sobres_dia(
                        fecha_dia=fecha_dia,
                        usuarios=usuarios,
                        rng=rng,
                        cerrar=not options['sobres_abiertos'],
                    )

            total_pedidos += pedidos_dia
            self.stdout.write(
                f'  {fecha_dia:%Y-%m-%d} → {pedidos_dia} pedidos'
                + ('' if options['sin_sobres'] else ' + sobres')
            )

        self.stdout.write(self.style.SUCCESS('\n' + '-' * 72))
        self.stdout.write(self.style.SUCCESS('RESUMEN'))
        self.stdout.write(f'  Pedidos creados:   {total_pedidos}')
        self.stdout.write(f'  Líneas de detalle: {total_detalles}')
        if not options['sin_sobres']:
            self.stdout.write(f'  Sobres procesados: {total_sobres}')
        self.stdout.write(self.style.SUCCESS('-' * 72))
        self.stdout.write(
            self.style.HTTP_INFO(
                f'\nLos registros llevan el marcador {MARCADOR}. '
                f'Para eliminarlos: python manage.py generar_datos_prueba --limpiar\n'
            )
        )

    def _plan(self, options, rng):
        fechas = self._resolver_fechas(options)
        estimado = sum(
            max(1, int(options['pedidos_dia'] * rng.uniform(0.6, 1.4)))
            for _ in fechas
        )
        self.stdout.write(self.style.WARNING('DRY-RUN — plan de generación'))
        self.stdout.write(f'  Días: {len(fechas)} ({fechas[0]} → {fechas[-1]})')
        self.stdout.write(f'  Pedidos estimados: ~{estimado}')
        if not options['sin_sobres']:
            self.stdout.write(f'  Sobres estimados: ~{len(fechas) * 3} (bodega + camioneros con ventas)')

    def _limpiar_datos_prueba(self):
        pedidos_qs = Pedido.objects.filter(direccion_entrega__startswith=MARCADOR)
        n_pedidos = pedidos_qs.count()

        sobres_qs = SobreDiario.objects.filter(nota_cierre__contains=MARCADOR)
        n_sobres = sobres_qs.count()

        if n_pedidos == 0 and n_sobres == 0:
            self.stdout.write(self.style.WARNING(f'No hay datos marcados con {MARCADOR}.'))
            return

        with transaction.atomic():
            sobres_qs.delete()
            pedidos_qs.delete()

        self.stdout.write(self.style.SUCCESS(
            f'Eliminados: {n_pedidos} pedidos y {n_sobres} sobres de prueba.'
        ))

    def _resolver_fechas(self, options):
        hoy = timezone.now().astimezone(TZ_CHILE).date()

        if options['desde']:
            try:
                inicio = datetime.strptime(options['desde'], '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('--desde debe ser YYYY-MM-DD') from exc
        else:
            inicio = hoy - timedelta(days=max(1, options['dias']) - 1)

        if options['hasta']:
            try:
                fin = datetime.strptime(options['hasta'], '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('--hasta debe ser YYYY-MM-DD') from exc
        else:
            fin = hoy

        if inicio > fin:
            raise CommandError('La fecha --desde no puede ser posterior a --hasta.')

        fechas = []
        cursor = inicio
        while cursor <= fin:
            fechas.append(cursor)
            cursor += timedelta(days=1)
        return fechas

    def _normalizar_ratios(self, tel, loc, tar):
        total = tel + loc + tar
        if total <= 0:
            return [40, 35, 25]
        return [tel, loc, tar]

    def _resolver_usuarios(self):
        telefonistas = list(Usuario.objects.filter(rol='telefonista', is_active=True))
        bodegueros = list(Usuario.objects.filter(rol='bodeguero', is_active=True))
        camioneros = list(Usuario.objects.filter(rol='camionero', is_active=True))
        operadores = list(
            Usuario.objects.filter(rol__in=['bodeguero', 'jefe', 'admin'], is_active=True)
        )

        faltantes = []
        if not telefonistas:
            faltantes.append('telefonista')
        if not bodegueros:
            faltantes.append('bodeguero')
        if not camioneros:
            faltantes.append('camionero')
        if not operadores:
            faltantes.append('bodeguero/jefe/admin (para sobres)')

        if faltantes:
            raise CommandError(
                'Faltan usuarios activos: ' + ', '.join(faltantes) + '. '
                'Ejecuta: python manage.py crear_usuarios'
            )

        return {
            'telefonistas': telefonistas,
            'bodegueros': bodegueros,
            'camioneros': camioneros,
            'operador_sobres': operadores[0],
        }

    def _resolver_sectores(self):
        nombres = list(
            Sector.objects.filter(activo=True).order_by('nombre').values_list('nombre', flat=True)
        )
        if nombres:
            return nombres
        return [valor for valor, _etiqueta in Pedido.SECTORES if valor != 'Otro']

    def _crear_pedido(self, fecha_dia, tipo, balones, pesos, usuarios, sectores, rng):
        """Crea un pedido entregado con 1 a 3 líneas de detalle."""
        num_lineas = min(rng.choices([1, 2, 3], weights=[5, 3, 2], k=1)[0], len(balones))
        balones_elegidos = []
        pool = balones.copy()
        pool_pesos = pesos.copy()
        for _ in range(num_lineas):
            elegido = rng.choices(pool, weights=pool_pesos, k=1)[0]
            idx = pool.index(elegido)
            balones_elegidos.append(elegido)
            pool.pop(idx)
            pool_pesos.pop(idx)

        detalles_data = []
        monto_total = Decimal(0)
        ganancia_total = Decimal(0)

        for balon in balones_elegidos:
            cantidad = rng.choices([1, 2, 3], weights=[6, 3, 1], k=1)[0]
            if tipo == 'local':
                precio_venta = balon.precio_local
            else:
                precio_venta = balon.precio_domicilio

            subtotal = precio_venta * cantidad
            ganancia = (precio_venta - balon.precio_compra) * cantidad
            monto_total += subtotal
            ganancia_total += ganancia
            detalles_data.append((balon, cantidad, precio_venta, balon.precio_compra))

        camionero = rng.choice(usuarios['camioneros'])
        sector = rng.choice(sectores) if sectores else ''
        ref = rng.randint(100, 9999)

        if tipo == 'local':
            registrador = rng.choice(usuarios['bodegueros'])
            entregador = None
            origen = 'local'
            direccion = f'{MARCADOR} Venta local — cliente walk-in #{ref}'
        elif tipo == 'tarreo':
            registrador = camionero
            entregador = camionero
            origen = 'tarreo'
            direccion = f'{MARCADOR} Tarreo — venta en ruta #{ref}'
            sector = ''
        else:
            registrador = rng.choice(usuarios['telefonistas'])
            entregador = camionero
            origen = 'telefono'
            direccion = f'{MARCADOR} Domicilio — {sector or "sin sector"} #{ref}'

        pedido = Pedido.objects.create(
            sector=sector,
            direccion_entrega=direccion,
            registrador=registrador,
            entregador=entregador,
            fecha=fecha_aleatoria_en_dia(fecha_dia, rng),
            estado='entregado',
            origen=origen,
            metodo_pago=rng.choice(METODOS_PAGO),
            monto_total=monto_total,
            ganancia_total=ganancia_total,
        )

        DetallePedido.objects.bulk_create([
            DetallePedido(
                pedido=pedido,
                balon=balon,
                cantidad=cantidad,
                precio_venta_unitario=precio_venta,
                precio_compra_unitario=precio_compra,
            )
            for balon, cantidad, precio_venta, precio_compra in detalles_data
        ])

        return pedido, len(detalles_data)

    def _crear_sobres_dia(self, fecha_dia, usuarios, rng, cerrar=True):
        """Crea o actualiza sobres de bodega y camioneros para un día."""
        creado_por = usuarios['operador_sobres']
        inicio_utc, fin_utc = rango_utc_dia(fecha_dia)
        sobres_count = 0

        # ── Sobre bodega (todas las ventas local del día) ──
        hay_local = Pedido.objects.filter(
            fecha__gte=inicio_utc,
            fecha__lte=fin_utc,
            origen='local',
            estado='entregado',
        ).exists()

        if hay_local:
            sobre_bodega = self._obtener_o_crear_sobre(
                fecha_dia=fecha_dia,
                tipo='bodega',
                trabajador=None,
                creado_por=creado_por,
                rng=rng,
            )
            if sobre_bodega:
                sincronizar_sobre_desde_pedidos(sobre_bodega)
                self._enriquecer_sobre(sobre_bodega, rng, es_camion=False)
                if cerrar and not sobre_bodega.cerrado:
                    self._cerrar_sobre(sobre_bodega, rng, es_camion=False)
                sobres_count += 1

        # ── Sobres camioneros (entregas + tarreo del día) ──
        camioneros_con_ventas = (
            Pedido.objects.filter(
                fecha__gte=inicio_utc,
                fecha__lte=fin_utc,
                estado='entregado',
                entregador__isnull=False,
            )
            .values_list('entregador_id', flat=True)
            .distinct()
        )

        for camionero_id in camioneros_con_ventas:
            camionero = Usuario.objects.get(id=camionero_id)
            sobre_camion = self._obtener_o_crear_sobre(
                fecha_dia=fecha_dia,
                tipo='camion',
                trabajador=camionero,
                creado_por=creado_por,
                rng=rng,
            )
            if not sobre_camion:
                continue
            sincronizar_sobre_desde_pedidos(sobre_camion)
            self._enriquecer_sobre(sobre_camion, rng, es_camion=True)
            if cerrar and not sobre_camion.cerrado:
                self._cerrar_sobre(sobre_camion, rng, es_camion=True)
            sobres_count += 1

        return sobres_count

    def _obtener_o_crear_sobre(self, fecha_dia, tipo, trabajador, creado_por, rng):
        """
        Reutiliza un sobre de prueba del mismo día; si existe un sobre real (sin marcador), lo omite.
        """
        candidatos = SobreDiario.objects.filter(
            fecha_correspondiente=fecha_dia,
            tipo=tipo,
            trabajador=trabajador,
        ).order_by('-id')

        for sobre in candidatos:
            if MARCADOR in (sobre.nota_cierre or ''):
                if sobre.cerrado:
                    sobre.cerrado = False
                    sobre.declarado_el = None
                    sobre.save(update_fields=['cerrado', 'declarado_el'])
                return sobre
            self.stdout.write(self.style.WARNING(
                f'  Omitido sobre real #{sobre.id} ({tipo} {fecha_dia}) — no se modifica.'
            ))
            return None

        return SobreDiario.objects.create(
            fecha=fecha_aleatoria_en_dia(fecha_dia, rng),
            fecha_correspondiente=fecha_dia,
            tipo=tipo,
            trabajador=trabajador,
            creado_por=creado_por,
        )

    def _enriquecer_sobre(self, sobre, rng, es_camion):
        """
        Añade pagos no efectivo y gastos opcionales de forma realista.
        Pequeño ajuste aleatorio en cantidades declaradas (~10%% de los sobres).
        """
        if sobre.cerrado:
            return

        monto_declarado = int(sobre.monto_declarado or 0)
        if monto_declarado <= 0:
            return

        # Pagos no efectivo (~35% de los sobres)
        if rng.random() < 0.35:
            tipo = rng.choice(['transferencia', 'visa', 'abono'])
            monto = int(monto_declarado * rng.uniform(0.05, 0.25))
            if monto > 0:
                LineaPago.objects.create(
                    sobre=sobre,
                    tipo_pago=tipo,
                    monto=monto,
                    referencia=f'{MARCADOR} pago simulado',
                )

        # Gastos de camionero (~20%)
        if es_camion and rng.random() < 0.2:
            LineaGasto.objects.create(
                sobre=sobre,
                descripcion='Combustible / peajes',
                monto=rng.randint(3000, 15000),
                nota=f'{MARCADOR} gasto simulado',
            )

        # Ajuste manual simulado en una línea
        if rng.random() < 0.1:
            linea = sobre.lineas.filter(cantidad_calculada__gt=0).order_by('?').first()
            if linea and linea.cantidad_declarada == linea.cantidad_calculada:
                delta = rng.choice([-1, 1])
                nueva = max(0, int(linea.cantidad_declarada) + delta)
                if nueva != linea.cantidad_calculada:
                    linea.cantidad_declarada = nueva
                    linea.nota = f'{MARCADOR} ajuste simulado'
                    linea.save(update_fields=['cantidad_declarada', 'nota'])
                    sobre.save()  # recalcula montos vía SobreDiario.save

    def _cerrar_sobre(self, sobre, rng, es_camion):
        hora_cierre = rng.randint(18, 21)
        dt_cierre = timezone.make_aware(
            datetime.combine(sobre.fecha_correspondiente, time(hora_cierre, rng.randint(0, 59))),
            TZ_CHILE,
        )
        sobre.cerrado = True
        sobre.declarado_el = dt_cierre
        sobre.nota_cierre = f'{MARCADOR} Sobre generado por generar_datos_prueba'
        if es_camion:
            sobre.kilometraje_camion = rng.randint(80000, 195000)
        sobre.save()