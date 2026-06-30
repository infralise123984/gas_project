"""Vistas HTTP — reportes."""

import logging
from calendar import monthrange
from datetime import date, datetime
from json import dumps
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q, Sum
from django.shortcuts import render

from mockups.models import (
    DetallePedido,
    Pedido,
    SobreDiario,
    Usuario,
)
from mockups.utils.fechas import (
    today_chile,
)
from mockups.utils.permisos import require_roles, filtrar_por_bodega

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')




# Admin/Jefe: dashboard de ventas completo
@login_required
def reporte_ventas(request):
    """Dashboard mensal: métricas, gráficos, análisis por balón, origen y trabajador."""
    resp = require_roles(request, ["jefe", "admin"], "index",
                         "Solo jefes y administradores pueden acceder a los reportes.")
    if resp:
        return resp
    
    # ═══════════════════════════════════════════════════════════
    # 1. PROCESAR FILTROS
    # ═══════════════════════════════════════════════════════════
    tz_chile = ZoneInfo('America/Santiago')

    # Navegación por mes
    hoy = today_chile()
    mes_param = request.GET.get("mes", "").strip()
    
    try:
        if mes_param:
            mes_parts = mes_param.split("-")
            año = int(mes_parts[0])
            mes = int(mes_parts[1])
        else:
            año = hoy.year
            mes = hoy.month
    except (ValueError, IndexError):
        año = hoy.year
        mes = hoy.month
    
    # Calcular fechas del mes
    fecha_inicio = date(año, mes, 1)
    fecha_fin = date(año, mes, monthrange(año, mes)[1])
    mes_display = fecha_inicio.strftime('%B %Y').capitalize()
    
    # Mes anterior y siguiente
    if mes == 1:
        mes_anterior = date(año - 1, 12, 1).strftime('%Y-%m')
    else:
        mes_anterior = date(año, mes - 1, 1).strftime('%Y-%m')
    
    if mes == 12:
        mes_siguiente = date(año + 1, 1, 1).strftime('%Y-%m')
    else:
        mes_siguiente = date(año, mes + 1, 1).strftime('%Y-%m')
    
    mes_actual = date(año, mes, 1).strftime('%Y-%m')
    es_mes_actual = (año == hoy.year and mes == hoy.month)
    
    # Base para comparar fechas
    desde_str = fecha_inicio.strftime("%Y-%m-%d")
    hasta_str = fecha_fin.strftime("%Y-%m-%d")

    dt_inicio = datetime(fecha_inicio.year, fecha_inicio.month, fecha_inicio.day,
                         0, 0, 0, tzinfo=tz_chile)
    dt_fin    = datetime(fecha_fin.year, fecha_fin.month, fecha_fin.day,
                         23, 59, 59, 999999, tzinfo=tz_chile)

    # Otros filtros
    filtro_origen = request.GET.get("origen", "")
    filtro_metodo = request.GET.get("metodo_pago", "")
    filtro_sector = request.GET.get("sector", "").strip()
    filtro_registrador = request.GET.get("registrador", "")
    filtro_trabajador = request.GET.get("trabajador", "")  # Para filtrar por camionero
    
    # ═══════════════════════════════════════════════════════════
    # 2. CONSULTA BASE
    # ═══════════════════════════════════════════════════════════
    pedidos = filtrar_por_bodega(Pedido.objects.filter(
        estado="entregado",
        fecha__gte=dt_inicio,
        fecha__lte=dt_fin,
    ), request).select_related('registrador', 'entregador').prefetch_related('detalles__balon')
    
    if filtro_origen:
        pedidos = pedidos.filter(origen=filtro_origen)
    if filtro_metodo:
        pedidos = pedidos.filter(metodo_pago=filtro_metodo)
    if filtro_sector:
        pedidos = pedidos.filter(sector=filtro_sector)
    if filtro_registrador:
        pedidos = pedidos.filter(registrador_id=filtro_registrador)
    if filtro_trabajador:
        # Filtrar por camionero/entregador
        pedidos = pedidos.filter(entregador_id=filtro_trabajador)
    
    # ═══════════════════════════════════════════════════════════
    # 3. MÉTRICAS PRINCIPALES
    # ═══════════════════════════════════════════════════════════
    
    total_ventas = pedidos.aggregate(total=Sum('monto_total'))['total'] or 0
    total_ganancias = pedidos.aggregate(total=Sum('ganancia_total'))['total'] or 0
    total_pedidos = pedidos.count()
    promedio_pedido = total_ventas / total_pedidos if total_pedidos > 0 else 0
    margen_promedio = (total_ganancias / total_ventas * 100) if total_ventas > 0 else 0
    
    # Calcular total de kilos vendidos
    total_kilos = DetallePedido.objects.filter(
        pedido__in=pedidos
    ).aggregate(
        total=Sum(F('cantidad') * F('balon__peso_neto_gas'))
    )['total'] or 0
    
    # ═══════════════════════════════════════════════════════════
    # 4. ANÁLISIS POR BALÓN
    # ═══════════════════════════════════════════════════════════
    
    por_balon = DetallePedido.objects.filter(
        pedido__in=pedidos
    ).values(
        'balon__nombre',
        'balon__peso_neto_gas'
    ).annotate(
        unidades_vendidas=Sum('cantidad'),
        kilos_vendidos=Sum(F('cantidad') * F('balon__peso_neto_gas')),
        monto_vendido=Sum(F('cantidad') * F('precio_venta_unitario')),
        ganancia_total=Sum(F('cantidad') * (F('precio_venta_unitario') - F('precio_compra_unitario')))
    ).order_by('-monto_vendido')
    
    # ═══════════════════════════════════════════════════════════
    # 5. ANÁLISIS POR ORIGEN
    # ═══════════════════════════════════════════════════════════
    
    por_origen = pedidos.values('origen').annotate(
        cantidad=Count('id'),
        total_vendido=Sum('monto_total'),
        total_ganancia=Sum('ganancia_total')
    ).order_by('-total_vendido')
    
    # ═══════════════════════════════════════════════════════════
    # 6. ANÁLISIS POR MÉTODO DE PAGO
    # ═══════════════════════════════════════════════════════════
    
    por_metodo = pedidos.values('metodo_pago').annotate(
        cantidad=Count('id'),
        total_vendido=Sum('monto_total')
    ).order_by('-total_vendido')
    
    # ═══════════════════════════════════════════════════════════
    # 7. TOP SECTORES
    # ═══════════════════════════════════════════════════════════
    
    top_sectores = pedidos.exclude(
        Q(sector='') | Q(sector__isnull=True)
    ).values('sector').annotate(
        cantidad_pedidos=Count('id'),
        total_vendido=Sum('monto_total')
    ).order_by('-total_vendido')[:10]
    
    # ═══════════════════════════════════════════════════════════
    # 8. RENDIMIENTO CONSOLIDADO POR TRABAJADOR (EN KILOS)
    #
    # Calcula kilos vendidos por cada trabajador, desglosado por tipo:
    #  - Kilos registrados (telefonista/bodeguero)
    #  - Kilos entregados (camionero en domicilios)
    #  - Kilos tarreo/extra (camionero en tarreo)
    # ═══════════════════════════════════════════════════════════
    
    rendimiento_trabajadores = {}
    
    for pedido in pedidos:
        # Calcular kilos del pedido
        kilos_pedido = sum(
            float(det.cantidad) * float(det.balon.peso_neto_gas or 0)
            for det in pedido.detalles.all()
        )
        
        # --- Opción 1: Registrador (telefonista/bodeguero) ---
        if pedido.registrador_id:
            registrador = pedido.registrador
            if registrador.id not in rendimiento_trabajadores:
                rendimiento_trabajadores[registrador.id] = {
                    'id': registrador.id,
                    'nombre_trabajador': registrador.get_full_name() or registrador.username,
                    'rol': registrador.rol,
                    'kilos_registrados': 0,
                    'kilos_entregados': 0,
                    'kilos_tarreo': 0,
                    'total_pedidos': 0,
                    'total_kilos': 0,
                }
            
            # Distinto destino según origen
            if pedido.origen in ['telefono', 'local']:
                rendimiento_trabajadores[registrador.id]['kilos_registrados'] += kilos_pedido
            elif pedido.origen in ['tarreo', 'venta_extra']:
                # Si es tarreo/extra, el registrador es el dueño de la venta
                rendimiento_trabajadores[registrador.id]['kilos_tarreo'] += kilos_pedido
        
        # --- Opción 2: Entregador (camionero) ---
        if pedido.entregador_id and pedido.origen not in ['tarreo', 'venta_extra']:
            entregador = pedido.entregador
            if entregador.id not in rendimiento_trabajadores:
                rendimiento_trabajadores[entregador.id] = {
                    'id': entregador.id,
                    'nombre_trabajador': entregador.get_full_name() or entregador.username,
                    'rol': entregador.rol,
                    'kilos_registrados': 0,
                    'kilos_entregados': 0,
                    'kilos_tarreo': 0,
                    'total_pedidos': 0,
                    'total_kilos': 0,
                }
            
            rendimiento_trabajadores[entregador.id]['kilos_entregados'] += kilos_pedido
    
    # Consolidar y contar pedidos
    for uid, w in rendimiento_trabajadores.items():
        # Contar cuántos pedidos participó
        pedidos_registrador = pedidos.filter(registrador_id=uid).count() if uid else 0
        pedidos_entregador = pedidos.filter(entregador_id=uid).count() if uid else 0
        w['total_pedidos'] = pedidos_registrador + pedidos_entregador
        w['total_kilos'] = w['kilos_registrados'] + w['kilos_entregados'] + w['kilos_tarreo']
    
    rendimiento_trabajadores_lista = sorted(
        rendimiento_trabajadores.values(),
        key=lambda x: x['total_kilos'],
        reverse=True
    )

    # ═══════════════════════════════════════════════════════════
    # 9. ANÁLISIS TEMPORAL (ventas por día)
    # ═══════════════════════════════════════════════════════════
    
    ventas_por_dia = {}
    
    for pedido in pedidos:
        fecha_local = pedido.fecha.astimezone(tz_chile).date()
        fecha_str = fecha_local.strftime('%Y-%m-%d')
        
        if fecha_str not in ventas_por_dia:
            ventas_por_dia[fecha_str] = {
                'fecha': fecha_local,
                'pedidos': 0,
                'monto': 0,
                'ganancia': 0
            }
        
        ventas_por_dia[fecha_str]['pedidos'] += 1
        ventas_por_dia[fecha_str]['monto'] += float(pedido.monto_total or 0)
        ventas_por_dia[fecha_str]['ganancia'] += float(pedido.ganancia_total or 0)
    
    ventas_por_dia_lista = sorted(ventas_por_dia.values(), key=lambda x: x['fecha'])
    
    # ═══════════════════════════════════════════════════════════
    # 10. PREPARAR DATOS PARA GRÁFICOS
    # ═══════════════════════════════════════════════════════════
    
    chart_balones_labels = dumps([item['balon__nombre'] for item in por_balon[:8]])
    chart_balones_unidades = dumps([int(item['unidades_vendidas']) for item in por_balon[:8]])
    chart_balones_kilos = dumps([float(item['kilos_vendidos']) for item in por_balon[:8]])
    chart_balones_monto = dumps([float(item['monto_vendido']) for item in por_balon[:8]])
    
    chart_origen_labels = dumps([dict(Pedido.ORIGENES).get(item['origen'], item['origen']) for item in por_origen])
    chart_origen_data = dumps([float(item['total_vendido']) for item in por_origen])
    
    metodos_dict = dict([("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia")])
    chart_metodo_labels = dumps([metodos_dict.get(item['metodo_pago'], item['metodo_pago']) for item in por_metodo])
    chart_metodo_data = dumps([float(item['total_vendido']) for item in por_metodo])
    
    chart_dias_labels = dumps([item['fecha'].strftime('%d/%m') for item in ventas_por_dia_lista])
    chart_dias_ventas = dumps([item['monto'] for item in ventas_por_dia_lista])
    chart_dias_ganancias = dumps([item['ganancia'] for item in ventas_por_dia_lista])
    
    chart_sectores_labels = dumps([item['sector'] for item in top_sectores])
    chart_sectores_data = dumps([float(item['total_vendido']) for item in top_sectores])

    # Gráfico de barras: rendimiento de trabajadores por kilos
    chart_trabajadores_labels = dumps([w['nombre_trabajador'] for w in rendimiento_trabajadores_lista[:10]])
    chart_trabajadores_registrado = dumps([float(w['kilos_registrados']) for w in rendimiento_trabajadores_lista[:10]])
    chart_trabajadores_entregado = dumps([float(w['kilos_entregados']) for w in rendimiento_trabajadores_lista[:10]])
    chart_trabajadores_tarreo = dumps([float(w['kilos_tarreo']) for w in rendimiento_trabajadores_lista[:10]])
    
    # ═══════════════════════════════════════════════════════════
    # 11. LISTA DE TRABAJADORES PARA FILTRO
    # ═══════════════════════════════════════════════════════════
    
    trabajadores = Usuario.objects.filter(
        is_active=True,
        rol__in=['telefonista', 'bodeguero', 'jefe', 'admin']
    ).order_by('first_name', 'last_name')
    
    # ═══════════════════════════════════════════════════════════
    # 12. CONTEXT
    # ═══════════════════════════════════════════════════════════
    
    context = {
        # Métricas principales
        'total_ventas': total_ventas,
        'total_ganancias': total_ganancias,
        'total_pedidos': total_pedidos,
        'total_kilos': total_kilos,
        'promedio_pedido': promedio_pedido,
        'margen_promedio': margen_promedio,
        
        # Tablas de análisis
        'por_balon': por_balon,
        'por_origen': por_origen,
        'por_metodo': por_metodo,
        'top_sectores': top_sectores,
        'ventas_por_dia': ventas_por_dia_lista,

        # Rendimiento consolidado por trabajador
        'rendimiento_trabajadores': rendimiento_trabajadores_lista,
        
        # Datos para gráficos
        'chart_balones_labels': chart_balones_labels,
        'chart_balones_unidades': chart_balones_unidades,
        'chart_balones_kilos': chart_balones_kilos,
        'chart_balones_monto': chart_balones_monto,
        
        'chart_origen_labels': chart_origen_labels,
        'chart_origen_data': chart_origen_data,
        
        'chart_metodo_labels': chart_metodo_labels,
        'chart_metodo_data': chart_metodo_data,
        
        'chart_dias_labels': chart_dias_labels,
        'chart_dias_ventas': chart_dias_ventas,
        'chart_dias_ganancias': chart_dias_ganancias,
        
        'chart_sectores_labels': chart_sectores_labels,
        'chart_sectores_data': chart_sectores_data,

        'chart_trabajadores_labels': chart_trabajadores_labels,
        'chart_trabajadores_registrado': chart_trabajadores_registrado,
        'chart_trabajadores_entregado': chart_trabajadores_entregado,
        'chart_trabajadores_tarreo': chart_trabajadores_tarreo,
        
        # Navegación de mes
        'mes_display': mes_display,
        'mes_anterior': mes_anterior,
        'mes_siguiente': mes_siguiente,
        'mes_actual': mes_actual,
        'es_mes_actual': es_mes_actual,
        
        # Filtros
        'filtro_origen': filtro_origen,
        'filtro_metodo': filtro_metodo,
        'filtro_sector': filtro_sector,
        'filtro_registrador': filtro_registrador,
        'filtro_trabajador': filtro_trabajador,
        
        # Choices para filtros
        'origen_choices': Pedido.ORIGENES,
        'metodo_choices': [("efectivo", "Efectivo"), ("tarjeta", "Tarjeta"), ("transferencia", "Transferencia")],
        'trabajadores': trabajadores,
        'sectores_choices': Pedido.SECTORES,
        'camioneros': Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name'),
    }
    
    return render(request, "reportes/reporte_ventas.html", context)


# Admin/Jefe: reporte de sobres diarios
@login_required
def reporte_sobres(request):
    """Reporte mensual de sobres cerrados: dinero, kilos, gastos, pagos por tipo."""
    resp = require_roles(request, ["jefe", "admin"], "index",
                         "Solo jefes y administradores pueden acceder al reporte de sobres.")
    if resp:
        return resp

    # ═══════════════════════════════════════════════════════════
    # 1. PERÍODO — siempre un mes completo, navegable con ?mes=YYYY-MM
    # ═══════════════════════════════════════════════════════════
    hoy = today_chile()

    mes_str = request.GET.get("mes", "").strip()
    try:
        if mes_str:
            anio, mes = int(mes_str[:4]), int(mes_str[5:7])
        else:
            anio, mes = hoy.year, hoy.month
    except (ValueError, IndexError):
        anio, mes = hoy.year, hoy.month

    fecha_inicio = date(anio, mes, 1)
    fecha_fin    = date(anio, mes, monthrange(anio, mes)[1])
    mes_display  = fecha_inicio.strftime('%B %Y').capitalize()
    mes_actual   = f"{anio:04d}-{mes:02d}"

    mes_anterior  = f"{anio-1:04d}-12" if mes == 1  else f"{anio:04d}-{mes-1:02d}"
    mes_siguiente = f"{anio+1:04d}-01" if mes == 12 else f"{anio:04d}-{mes+1:02d}"
    es_mes_actual = (anio == hoy.year and mes == hoy.month)

    # Filtro opcional por camionero
    filtro_trabajador = request.GET.get("trabajador", "").strip()

    # ═══════════════════════════════════════════════════════════
    # 2. QUERYSET BASE — solo sobres cerrados del mes
    # ═══════════════════════════════════════════════════════════
    sobres_qs = SobreDiario.objects.filter(
        fecha_correspondiente__gte=fecha_inicio,
        fecha_correspondiente__lte=fecha_fin,
        cerrado=True,
    ).select_related('trabajador').prefetch_related(
        'lineas__balon', 'pagos', 'gastos'
    )

    if filtro_trabajador:
        sobres_qs = sobres_qs.filter(trabajador_id=filtro_trabajador)

    # Evaluar una sola vez y separar por tipo
    lista_bodega = list(sobres_qs.filter(tipo='bodega').order_by('fecha_correspondiente'))
    lista_camion = list(sobres_qs.filter(tipo='camion').order_by('fecha_correspondiente'))
    lista_todos  = lista_bodega + lista_camion

    # ═══════════════════════════════════════════════════════════
    # 3. FUNCIÓN AUXILIAR — métricas de un grupo de sobres
    # ═══════════════════════════════════════════════════════════
    TIPO_PAGO_DISPLAY = {
        'abono': 'Abono Caja', 'transferencia': 'Transferencia',
        'visa': 'Visa/POS', 'cheque': 'Cheque',
        'efectivo': 'Efectivo', 'otro': 'Otro',
    }

    def calcular_metricas(sobres_lista):
        total_declarado = 0.0
        total_kilos     = 0
        total_balones   = 0
        total_gastos    = 0.0
        total_no_ef     = 0.0
        pagos_por_tipo  = {}
        balones_dict    = {}

        for s in sobres_lista:
            total_declarado += float(s.monto_declarado or 0)

            for linea in s.lineas.all():
                decl  = int(linea.cantidad_declarada or 0)
                peso  = int(linea.balon.peso_neto_gas or 0)
                nombre = linea.balon.nombre
                total_balones += decl
                total_kilos   += decl * peso
                if nombre not in balones_dict:
                    balones_dict[nombre] = {'nombre': nombre, 'peso': peso, 'unidades': 0, 'kilos': 0}
                balones_dict[nombre]['unidades'] += decl
                balones_dict[nombre]['kilos']    += decl * peso

            for g in s.gastos.all():
                total_gastos += float(g.monto or 0)

            for p in s.pagos.all():
                monto = float(p.monto or 0)
                # Solo contar como "no efectivo" si NO es efectivo
                if p.tipo_pago != 'efectivo':
                    total_no_ef += monto
                label = TIPO_PAGO_DISPLAY.get(p.tipo_pago, p.tipo_pago)
                pagos_por_tipo[label] = pagos_por_tipo.get(label, 0) + monto

        return {
            'total_declarado':   total_declarado,
            'total_kilos':       total_kilos,
            'total_balones':     total_balones,
            'total_gastos':      total_gastos,
            'total_no_ef':       total_no_ef,
            'efectivo_estimado': total_declarado - total_gastos - total_no_ef,
            'pagos_por_tipo':    pagos_por_tipo,
            'balones_lista':     sorted(balones_dict.values(), key=lambda x: x['peso']),
            'count':             len(sobres_lista),
        }

    # ═══════════════════════════════════════════════════════════
    # 3a. AGRUPAR POR TRABAJADOR — para tarjetas individuales
    # ═══════════════════════════════════════════════════════════
    trabajadores_dict = {}  # trabajador_id (o 'bodega') → lista de sobres

    for s in lista_todos:
        clave = 'bodega' if s.tipo == 'bodega' else s.trabajador_id
        if clave not in trabajadores_dict:
            trabajadores_dict[clave] = []
        trabajadores_dict[clave].append(s)

    # Calcular métricas por trabajador
    resumen_trabajadores = []
    for clave, sobres_trab in trabajadores_dict.items():
        metricas = calcular_metricas(sobres_trab)
        
        if clave == 'bodega':
            nombre = 'Bodega/Local'
            tipo_mostrar = 'bodega'
            usuario_obj = None
        else:
            usuario_obj = Usuario.objects.get(id=clave)
            nombre = usuario_obj.get_full_name() or usuario_obj.username
            tipo_mostrar = 'camion'
        
        resumen_trabajadores.append({
            'clave': clave,
            'nombre': nombre,
            'tipo': tipo_mostrar,
            'usuario_obj': usuario_obj,
            'sobres': sobres_trab,
            **metricas  # expande todas las métricas
        })

    # Ordenar: bodega primero, luego camioneros por nombre
    resumen_trabajadores.sort(key=lambda x: (x['tipo'] == 'camion', x['nombre']))

    m_bodega = calcular_metricas(lista_bodega)
    m_camion = calcular_metricas(lista_camion)

    # Totales globales
    total_declarado   = m_bodega['total_declarado'] + m_camion['total_declarado']
    total_kilos       = m_bodega['total_kilos']     + m_camion['total_kilos']
    total_balones     = m_bodega['total_balones']   + m_camion['total_balones']
    total_gastos      = m_bodega['total_gastos']    + m_camion['total_gastos']
    total_no_ef       = m_bodega['total_no_ef']     + m_camion['total_no_ef']
    efectivo_estimado = total_declarado - total_gastos - total_no_ef

    # Pagos globales consolidados
    pagos_global = {}
    for d in (m_bodega['pagos_por_tipo'], m_camion['pagos_por_tipo']):
        for tipo, monto in d.items():
            pagos_global[tipo] = pagos_global.get(tipo, 0) + monto

    # Balones globales consolidados
    balones_global_dict = {}
    for b in m_bodega['balones_lista'] + m_camion['balones_lista']:
        n = b['nombre']
        if n not in balones_global_dict:
            balones_global_dict[n] = {'nombre': n, 'peso': b['peso'], 'unidades': 0, 'kilos': 0}
        balones_global_dict[n]['unidades'] += b['unidades']
        balones_global_dict[n]['kilos']    += b['kilos']
    balones_global_lista = sorted(balones_global_dict.values(), key=lambda x: x['peso'])

    # ═══════════════════════════════════════════════════════════
    # 4. EVOLUCIÓN DIARIA (para gráfico)
    # ═══════════════════════════════════════════════════════════
    dias_dict = {}
    for s in lista_todos:
        d = s.fecha_correspondiente
        if d not in dias_dict:
            dias_dict[d] = {'fecha': d, 'bodega': 0.0, 'camion': 0.0}
        monto = float(s.monto_declarado or 0)
        dias_dict[d]['bodega' if s.tipo == 'bodega' else 'camion'] += monto

    dias_lista = sorted(dias_dict.values(), key=lambda x: x['fecha'])

    # ═══════════════════════════════════════════════════════════
    # 5.5 GASTOS DESGLOSADOS — por trabajador y bodega
    # ═══════════════════════════════════════════════════════════
    gastos_por_trabajador = {}
    gastos_globales = []

    for s in lista_todos:
        clave = 'bodega' if s.tipo == 'bodega' else s.trabajador_id
        for gasto in s.gastos.all():
            gasto_dict = {
                'fecha': s.fecha_correspondiente,
                'descripcion': gasto.descripcion,
                'monto': float(gasto.monto or 0),
                'nota': gasto.nota,
                'trabajador_clave': clave,
            }
            gastos_globales.append(gasto_dict)
            if clave not in gastos_por_trabajador:
                gastos_por_trabajador[clave] = []
            gastos_por_trabajador[clave].append(gasto_dict)

    # Sumarizar gastos por trabajador
    gastos_resumen = []
    for clave, gastos_list in gastos_por_trabajador.items():
        if clave == 'bodega':
            nombre = 'Bodega/Local'
        else:
            try:
                usuario_obj = Usuario.objects.get(id=clave)
                nombre = usuario_obj.get_full_name() or usuario_obj.username
            except:
                nombre = f'Trabajador #{clave}'
        
        monto_total = sum(g['monto'] for g in gastos_list)
        gastos_resumen.append({
            'nombre': nombre,
            'clave': clave,
            'monto_total': monto_total,
            'gastos': sorted(gastos_list, key=lambda x: x['fecha'], reverse=True),
        })

    gastos_resumen.sort(key=lambda x: x['monto_total'], reverse=True)

    # ═══════════════════════════════════════════════════════════
    # 5. TABLA DETALLE — una fila por sobre
    # ═══════════════════════════════════════════════════════════
    sobres_tabla = []
    for s in sorted(lista_todos, key=lambda x: (x.fecha_correspondiente, x.tipo), reverse=True):
        kilos_s    = sum(int(l.cantidad_declarada or 0) * int(l.balon.peso_neto_gas or 0) for l in s.lineas.all())
        balones_s  = sum(int(l.cantidad_declarada or 0) for l in s.lineas.all())
        gastos_s   = sum(float(g.monto or 0) for g in s.gastos.all())
        no_ef_s    = sum(float(p.monto or 0) for p in s.pagos.all())
        declarado  = float(s.monto_declarado or 0)

        nombre_trabajador = 'Bodega' if s.tipo == 'bodega' else (
            (s.trabajador.get_full_name() or s.trabajador.username) if s.trabajador else '—'
        )

        sobres_tabla.append({
            'id':                s.id,
            'fecha':             s.fecha_correspondiente,
            'tipo':              s.tipo,
            'nombre_trabajador': nombre_trabajador,
            'balones':           balones_s,
            'kilos':             kilos_s,
            'declarado':         declarado,
            'gastos':            gastos_s,
            'no_efectivo':       no_ef_s,
            'dinero_neto':       declarado - gastos_s,
            'km':                s.kilometraje_camion or 0,
        })

    # Subtotales por tipo (para filas de resumen al final de cada grupo)
    def subtotal(tipo):
        filas = [f for f in sobres_tabla if f['tipo'] == tipo]
        if not filas:
            return None
        return {
            'count':             len(filas),
            'balones':           sum(f['balones']           for f in filas),
            'kilos':             sum(f['kilos']             for f in filas),
            'declarado':         sum(f['declarado']         for f in filas),
            'gastos':            sum(f['gastos']            for f in filas),
            'no_efectivo':       sum(f['no_efectivo']       for f in filas),
            'dinero_neto':       sum(f['dinero_neto']       for f in filas),
            'km':                sum(f['km']                for f in filas),
        }

    sub_bodega = subtotal('bodega')
    sub_camion = subtotal('camion')

    # ═══════════════════════════════════════════════════════════
    # 6. DATOS PARA GRÁFICOS
    # ═══════════════════════════════════════════════════════════
    chart_dias_labels    = dumps([d['fecha'].strftime('%d/%m')  for d in dias_lista])
    chart_dias_bodega    = dumps([d['bodega']                   for d in dias_lista])
    chart_dias_camion    = dumps([d['camion']                   for d in dias_lista])

    chart_balon_labels   = dumps([b['nombre']   for b in balones_global_lista])
    chart_balon_unidades = dumps([b['unidades'] for b in balones_global_lista])
    chart_balon_kilos    = dumps([b['kilos']    for b in balones_global_lista])

    chart_pagos_labels   = dumps(list(pagos_global.keys()))
    chart_pagos_data     = dumps(list(pagos_global.values()))

    # ═══════════════════════════════════════════════════════════
    # 7. CONTEXT
    # ═══════════════════════════════════════════════════════════
    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    context = {
        # Navegación
        'mes_display':    mes_display,
        'mes_actual':     mes_actual,
        'mes_anterior':   mes_anterior,
        'mes_siguiente':  mes_siguiente,
        'es_mes_actual':  es_mes_actual,

        # Filtros
        'filtro_trabajador': filtro_trabajador,
        'camioneros':        camioneros,

        # Resumen por trabajador (NEW)
        'resumen_trabajadores': resumen_trabajadores,

        # Métricas globales
        'total_declarado':    total_declarado,
        'total_kilos':        total_kilos,
        'total_balones':      total_balones,
        'total_gastos':       total_gastos,
        'total_no_ef':        total_no_ef,
        'efectivo_estimado':  efectivo_estimado,
        'total_dinero_neto':  total_declarado - total_gastos,
        'pagos_global':       pagos_global,
        'balones_global':     balones_global_lista,

        # Métricas por tipo
        'm_bodega': m_bodega,
        'm_camion': m_camion,

        # Tabla detalle
        'sobres_tabla': sobres_tabla,
        'sub_bodega':   sub_bodega,
        'sub_camion':   sub_camion,

        # Gastos desglosados (NEW)
        'gastos_resumen': gastos_resumen,
        'gastos_globales': gastos_globales,

        # Gráficos
        'chart_dias_labels':    chart_dias_labels,
        'chart_dias_bodega':    chart_dias_bodega,
        'chart_dias_camion':    chart_dias_camion,
        'chart_balon_labels':   chart_balon_labels,
        'chart_balon_unidades': chart_balon_unidades,
        'chart_balon_kilos':    chart_balon_kilos,
        'chart_pagos_labels':   chart_pagos_labels,
        'chart_pagos_data':     chart_pagos_data,
    }

    return render(request, "sobres/reporte_sobres.html", context)

