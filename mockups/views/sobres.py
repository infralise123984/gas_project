"""Vistas HTTP — sobres."""

import logging
from calendar import monthrange
from datetime import date, datetime

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Sum, Case, When, Value, IntegerField
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from mockups.forms import (
    LineaGastoFormSet,
    LineaPagoFormSet,
    LineaSobreFormSet,
)
from mockups.models import (
    LineaSobre,
    SobreDiario,
    Usuario,
)
from mockups.services.sobres import sincronizar_sobre_desde_pedidos
from mockups.utils.fechas import (
    get_rango_utc_para_fecha,
    now_chile,
    today_chile,
)
from mockups.utils.permisos import require_roles, require_roles_api

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')




# ══════════════════════════════════════════════════════════════
# 8. GESTIÓN DE SOBRES DIARIOS (Cierre de Caja y Rendición)
# ══════════════════════════════════════════════════════════════

# Jefe/Bodeguero: listar sobres diarios creados
@login_required
def lista_sobres_diarios(request):
    """Listar sobres operativos filtrados por fecha de creación.

    Permite seleccionar qué sobre abrir/editar (Bodega o Camionero).
    Si hay más de un sobre del día, muestra lista para elegir.
    """
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "Acceso no permitido.")
    if resp:
        return resp

    hoy = today_chile()
    fecha_filtro_str = (request.GET.get('fecha') or '').strip()
    try:
        fecha_filtro = datetime.strptime(fecha_filtro_str, "%Y-%m-%d").date() if fecha_filtro_str else hoy
    except ValueError:
        fecha_filtro = hoy
        messages.warning(request, "La fecha indicada no es valida. Se mostro la fecha de hoy.")

    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    inicio_dia, fin_dia = get_rango_utc_para_fecha(fecha_filtro)

    sobres_del_dia = SobreDiario.objects.filter(
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia,
    ).select_related('trabajador', 'creado_por').order_by('-fecha', '-id')

    # Sobres de bodega creados en la fecha seleccionada
    sobres_bodega = list(sobres_del_dia.filter(tipo='bodega'))

    # Sobres de camioneros creados en la fecha seleccionada
    sobres_camioneros = list(
        sobres_del_dia.filter(tipo='camion').order_by('trabajador__first_name', '-fecha', '-id')
    )

    return render(request, 'sobres/lista_sobres.html', {
        'hoy': hoy,
        'fecha_filtro': fecha_filtro,
        'camioneros': camioneros,
        'sobres_bodega': sobres_bodega,
        'sobres_camioneros': sobres_camioneros,
    })


# Jefe/Bodeguero: editar sobre diario
@login_required
def editar_sobre_diario(request):
    """Editar monto declarado, gastos y notas de un sobre diario.

    URL preferida: /sobres/editar/?sobre_id=123

    Apertura/creación desde lista (flujo operativo en producción):
    - lista_sobres.html modal #modalAbrirSobre → GET aquí con bodega/camionero,
      fecha, fecha_creacion y opcional forzar_nuevo=1 (crea sobre adicional).
    - No usa crear_sobre_nuevo ni página aparte; es popup en la misma UI.

    URLs legacy (deprecadas, soportadas por compatibilidad):
    - /sobres/editar/?bodega=1&fecha=...
    - /sobres/editar/?camionero=5&fecha=...
    """
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para editar sobres.")
    if resp:
        return resp

    usuario = request.user
    sobre_id = request.GET.get('sobre_id')
    
    # CASO 1: Si viene sobre_id, cargar directamente ese sobre
    if sobre_id:
        sobre = get_object_or_404(SobreDiario, id=sobre_id)
        if sobre.tipo == 'bodega':
            titulo = "Sobre Diario - Bodega"
        else:
            titulo = f"Sobre Diario - {sobre.trabajador.get_full_name() or sobre.trabajador.username}"
        tipo_sobre = sobre.tipo
        trabajador = sobre.trabajador
        creado = False
    
    # CASO 2: Si no viene sobre_id pero viene bodega/camionero (legacy)
    # → Buscar/crear el sobre del día y redirigir con sobre_id
    else:
        es_bodega = request.GET.get('bodega') == '1'
        camionero_id = request.GET.get('camionero')
        fecha_query = (request.GET.get('fecha') or '').strip()
        fecha_creacion_query = (request.GET.get('fecha_creacion') or '').strip()
        
        if es_bodega:
            tipo_sobre = 'bodega'
            trabajador = None
            titulo = "Sobre Diario - Bodega"
        elif camionero_id:
            tipo_sobre = 'camion'
            trabajador = get_object_or_404(Usuario, id=camionero_id, rol='camionero')
            titulo = f"Sobre Diario - {trabajador.get_full_name() or trabajador.username}"
        else:
            messages.error(request, "Parámetros inválidos. Use: ?sobre_id=123")
            return redirect('sobres_lista')
        
        # Buscar sobre de la fecha solicitada (abierto o cerrado) o crear uno nuevo
        try:
            fecha_objetivo = datetime.strptime(fecha_query, "%Y-%m-%d").date() if fecha_query else today_chile()
        except ValueError:
            messages.warning(request, "La fecha indicada no es valida. Se usara la fecha de hoy.")
            fecha_objetivo = today_chile()

        try:
            fecha_creacion_objetivo = (
                datetime.strptime(fecha_creacion_query, "%Y-%m-%d").date()
                if fecha_creacion_query else today_chile()
            )
        except ValueError:
            messages.warning(request, "La fecha de creacion indicada no es valida. Se usara la fecha de hoy.")
            fecha_creacion_objetivo = today_chile()

        inicio_creacion, fin_creacion = get_rango_utc_para_fecha(fecha_creacion_objetivo)
        
        forzar_nuevo = request.GET.get('forzar_nuevo') == '1'

        if forzar_nuevo:
            sobre = None
        else:
            sobres_candidatos = SobreDiario.objects.filter(
                fecha_correspondiente=fecha_objetivo,
                tipo=tipo_sobre,
                trabajador=trabajador
            )

            # Preferir sobres creados en la jornada desde donde el usuario esta trabajando.
            sobre = sobres_candidatos.filter(
                fecha__gte=inicio_creacion,
                fecha__lte=fin_creacion,
            ).order_by('-fecha', '-id').first()

            # Compatibilidad: si no existe uno creado en esa jornada, usar el mas reciente historico.
            if not sobre:
                sobre = sobres_candidatos.order_by('-fecha', '-id').first()

        if sobre:
            # Existe un sobre del día, usarlo (el usuario decide si crear otro)
            creado = False
        else:
            # No existe ningún sobre para esa fecha, o se forzó uno nuevo
            sobre = SobreDiario.objects.create(
                fecha=now_chile(),
                tipo=tipo_sobre,
                trabajador=trabajador,
                creado_por=usuario,
                fecha_correspondiente=fecha_objetivo,
            )
            creado = True
        
        # REDIRECT CON sobre_id (forma correcta)
        return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre.id}")

    # Sobres con líneas: la actualización desde pedidos va por AJAX al cargar y cada 5 min.
    # Sobres vacíos (recién creados): una sync inicial para armar el formset de balones.
    if request.method != "POST" and not sobre.lineas.exists():
        sincronizar_sobre_desde_pedidos(sobre)

    # Preparar los formsets
    formset_lineas = LineaSobreFormSet(
        request.POST or None,
        instance=sobre,
        prefix='lineas'
    )

    formset_pagos = LineaPagoFormSet(
        request.POST or None,
        instance=sobre,
        prefix='pagos'
    )

    formset_gastos = LineaGastoFormSet(
        request.POST or None,
        instance=sobre,
        prefix='gastos'
    )

    if request.method == "POST":
        
        if not sobre.pk:
            sobre.save()
        
        # Actualizar campos adicionales del sobre
        if "kilometraje_camion" in request.POST:
            try:
                km = int(request.POST["kilometraje_camion"])
                if 0 <= km <= 999999:
                    sobre.kilometraje_camion = km
                else:
                    messages.warning(request, "Kilometraje fuera de rango (0–999999). Se guardó el valor anterior.")
            except (ValueError, TypeError):
                pass  # Campo vacío o inválido: se conserva el valor existente
        
        # Si se especifica fecha correspondiente
        fecha_correspondiente_str = request.POST.get("fecha_correspondiente")
        if fecha_correspondiente_str:
            try:
                sobre.fecha_correspondiente = datetime.strptime(fecha_correspondiente_str, "%Y-%m-%d").date()
            except ValueError:
                messages.warning(request, "Fecha correspondiente inválida. Se conservó la fecha anterior.")

        sobre.creado_por = request.user
        sobre.save()

        # Ahora sí validar y guardar los formsets
        if all([
            formset_lineas.is_valid(),
            formset_pagos.is_valid(),
            formset_gastos.is_valid()
        ]):
            # Guardar todos los formsets en una transacción atómica
            with transaction.atomic():
                formset_lineas.save()
                formset_pagos.save()
                formset_gastos.save()

                # Actualizar solo cantidades calculadas; preservar declaradas guardadas en borrador
                sincronizar_sobre_desde_pedidos(sobre)
                sobre.save()

            # Si se presionó el botón "Cerrar"
            if "cerrar" in request.POST:
                if sobre.cerrado:
                    messages.warning(request, "Este sobre ya estaba cerrado.")
                else:
                    sobre.cerrado = True
                    sobre.declarado_el = timezone.now()
                    sobre.nota_cierre = request.POST.get("nota_cierre", "")
                    
                    # Capturar kilometraje del camión (solo para camioneros)
                    if sobre.tipo != 'bodega':
                        try:
                            km = request.POST.get("kilometraje_camion", "").strip()
                            if km:
                                sobre.kilometraje_camion = int(km)
                        except (ValueError, TypeError):
                            messages.warning(request, "Kilometraje inválido. Se guardará el valor anterior.")
                    
                    sobre.save()
                    
                    if sobre.tipo == 'bodega':
                        km_msg = ""
                    else:
                        km_msg = f" Kilometraje: {sobre.kilometraje_camion} km."
                    
                    messages.success(
                        request,
                        f"Sobre cerrado correctamente el {sobre.declarado_el.strftime('%d/%m/%Y %H:%M')}.{km_msg}"
                    )
                    
                    # Redirigir a la lista para que el usuario elija qué hacer
                    return redirect('sobres_lista')
            else:
                messages.success(request, "Cambios guardados correctamente (borrador).")
                # Redirigir al mismo sobre (ya tenemos sobre_id)
                return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre.id}")

        else:
            messages.error(request, "Hay errores en el formulario. Revise los campos marcados.")

    # Contexto para el template
    ahora_chile = now_chile()
    hora_actual = ahora_chile.hour
    
    # ⏰ Determinar si mostrar alerta de "hora recomendada para cerrar"
    # (ej: después de las 20:00 = 8 PM)
    hora_recomendada_cierre = 18  # 
    mostrar_alerta_cierre = hora_actual >= hora_recomendada_cierre and not sobre.cerrado
    
    # Determinar si es bodega (para el template)
    es_bodega = sobre.tipo == 'bodega'
    
    context = {
        'sobre': sobre,
        'formset_lineas': formset_lineas,
        'formset_pagos': formset_pagos,
        'formset_gastos': formset_gastos,
        'hoy': today_chile(),
        'titulo': titulo,
        'es_bodega': es_bodega,
        'mostrar_alerta_cierre': mostrar_alerta_cierre,
        'hora_recomendada_cierre': f"{hora_recomendada_cierre}:00",
        'hora_actual': f"{hora_actual}:{ahora_chile.minute:02d}",
    }

    return render(request, 'sobres/sobres.html', context)


@login_required
@require_POST
def refrescar_sobre_diario(request, sobre_id):
    """Sincroniza un sobre abierto con los pedidos y retorna sus cantidades actualizadas."""
    resp = require_roles_api(request, ["bodeguero", "jefe", "admin"])
    if resp:
        return resp

    sobre = get_object_or_404(SobreDiario, id=sobre_id)

    if sobre.cerrado:
        return JsonResponse({'ok': True, 'cerrado': True, 'lineas': []})

    sincronizar_sobre_desde_pedidos(sobre)
    sobre.refresh_from_db()

    lineas = list(
        sobre.lineas.values('id', 'balon_id', 'cantidad_calculada', 'cantidad_declarada')
    )

    return JsonResponse({
        'ok': True,
        'cerrado': False,
        'lineas': lineas,
        'monto_calculado_app': int(sobre.monto_calculado_app or 0),
        'sincronizado_en': now_chile().strftime('%H:%M:%S'),
    })


# Jefe/Bodeguero: crear nuevo sobre diario
@login_required
def crear_sobre_nuevo(request):
    """Formulario página completa (crear_sobre.html) — legacy, sin URL en urls.py.

    El flujo real de bodega es el modal en lista_sobres → editar_sobre_diario
    (forzar_nuevo) o modales en sobres.html → crear_sobre_post_cierre.
    """
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "Acceso no permitido.")
    if resp:
        return resp

    if request.method == "POST":
        es_bodega = request.POST.get('tipo') == 'bodega'
        camionero_id = request.POST.get('camionero')
        fecha_correspondiente_str = request.POST.get('fecha_correspondiente')

        if not fecha_correspondiente_str:
            messages.error(request, "Debe especificar una fecha para el sobre.")
            return redirect('sobres_lista')

        try:
            fecha_correspondiente = datetime.strptime(fecha_correspondiente_str, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Fecha inválida.")
            return redirect('sobres_lista')

        if es_bodega:
            tipo_sobre = 'bodega'
            trabajador = None
        elif camionero_id:
            tipo_sobre = 'camion'
            trabajador = get_object_or_404(Usuario, id=camionero_id, rol='camionero')
        else:
            messages.error(request, "Debe seleccionar un tipo de sobre válido.")
            return redirect('sobres_lista')

        # Crear el sobre
        sobre = SobreDiario.objects.create(
            fecha=today_chile(),
            fecha_correspondiente=fecha_correspondiente,
            tipo=tipo_sobre,
            trabajador=trabajador,
            creado_por=request.user,
        )
        messages.success(request, f"Sobre creado para la fecha {fecha_correspondiente.strftime('%d/%m/%Y')}.")
        
        # Redirigir a editar ese sobre
        return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre.id}")

    # Formulario
    hoy = today_chile()
    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    return render(request, 'sobres/crear_sobre.html', {
        'hoy': hoy,
        'camioneros': camioneros,
    })



# Jefe/Bodeguero: crear sobre para cierre posterior
@login_required
def crear_sobre_post_cierre(request, sobre_id):
    """Crear nuevo sobre basado en uno anterior (modal en sobres.html, misma vista).

    POST desde #modalCrearNuevoSobre / #modalCrearSobreAdicional → URL
    /sobres/<id>/crear-nuevo/. Hereda tipo y trabajador del sobre anterior.
    Parámetro POST opcional: fecha_correspondiente (default = hoy).
    """
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para crear un nuevo sobre.")
    if resp:
        return resp

    # Obtener el sobre anterior
    sobre_anterior = get_object_or_404(SobreDiario, id=sobre_id)
    
    # Validar que el sobre anterior esté CERRADO (salvo que se fuerce por jefe/admin)
    forzar = request.POST.get('forzar_creacion') == '1'
    puede_forzar = request.user.rol in ['jefe', 'admin']

    if not sobre_anterior.cerrado and not (forzar and puede_forzar):
        messages.error(request, "Solo puedes crear un nuevo sobre después de cerrar el anterior.")
        return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre_anterior.id}")
    
    if request.method == "POST":
        # Obtener fecha correspondiente del formulario
        fecha_correspondiente_str = request.POST.get('fecha_correspondiente', '')
        
        try:
            fecha_correspondiente = datetime.strptime(fecha_correspondiente_str, "%Y-%m-%d").date()
        except ValueError:
            fecha_correspondiente = today_chile()
        
        # Crear el nuevo sobre con los mismos datos del anterior
        nuevo_sobre = SobreDiario.objects.create(
            fecha=now_chile(),
            fecha_correspondiente=fecha_correspondiente,
            tipo=sobre_anterior.tipo,
            trabajador=sobre_anterior.trabajador,
            creado_por=request.user,
            cerrado=False,  # IMPORTANTE: Asegurar que el nuevo sobre está ABIERTO
        )
        
        # SAFEGUARD: Asegurar que el nuevo sobre está COMPLETAMENTE VACÍO
        # (aunque no debería tener nada, ya que se acaba de crear)
        nuevo_sobre.lineas.all().delete()
        nuevo_sobre.pagos.all().delete()
        nuevo_sobre.gastos.all().delete()
        
        if forzar and puede_forzar and not sobre_anterior.cerrado:
            messages.warning(
                request,
                f"Sobre adicional creado para {fecha_correspondiente.strftime('%d/%m/%Y')}. "
                f"El sobre anterior (#{sobre_anterior.id}) sigue abierto."
            )
        else:
            messages.success(
                request,
                f"Nuevo sobre creado para {fecha_correspondiente.strftime('%d/%m/%Y')}. "
                f"El anterior estaba cerrado el {sobre_anterior.declarado_el.strftime('%d/%m/%Y %H:%M')}."
            )
        
        # Redirigir a editar el nuevo sobre
        # IMPORTANTE: Solo usar sobre_id para evitar que se cargue otro sobre por búsqueda de fecha
        return redirect(f"{reverse('sobres_editar')}?sobre_id={nuevo_sobre.id}")
    
    # Si no es POST, redirigir al sobre anterior
    return redirect(f"{reverse('sobres_editar')}?sobre_id={sobre_anterior.id}")



# Jefe: historial de sobres
@login_required
def historial_sobres(request):
    """Ver historial completo de sobres cerrados con métricas diarias."""
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para ver historial de sobres.")
    if resp:
        return resp

    modo_historial = request.GET.get('modo', 'dia')
    if modo_historial not in ['dia', 'mes', 'todo']:
        modo_historial = 'dia'

    hoy = today_chile()
    fecha_str = request.GET.get('fecha', '')
    mes_str = request.GET.get('mes', '')

    if fecha_str:
        try:
            fecha_seleccionada = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            fecha_seleccionada = hoy
            messages.warning(request, 'La fecha indicada no es valida. Se usara hoy.')
    else:
        fecha_seleccionada = hoy

    if mes_str:
        try:
            anio_seleccionado, mes_seleccionado_num = map(int, mes_str.split('-'))
            primer_dia_mes = date(anio_seleccionado, mes_seleccionado_num, 1)
        except (TypeError, ValueError):
            primer_dia_mes = date(hoy.year, hoy.month, 1)
            messages.warning(request, 'El mes indicado no es valido. Se usara el mes actual.')
    else:
        primer_dia_mes = date(hoy.year, hoy.month, 1)

    ultimo_dia_mes = date(
        primer_dia_mes.year,
        primer_dia_mes.month,
        monthrange(primer_dia_mes.year, primer_dia_mes.month)[1],
    )

    todos_sobres = SobreDiario.objects.select_related('trabajador', 'creado_por').prefetch_related('lineas')

    if modo_historial == 'mes':
        todos_sobres = todos_sobres.filter(
            fecha_correspondiente__gte=primer_dia_mes,
            fecha_correspondiente__lte=ultimo_dia_mes,
        )
        titulo_periodo = primer_dia_mes.strftime('%B de %Y').capitalize()
        descripcion_periodo = 'Resumen mensual de sobres segun su fecha correspondiente.'
    elif modo_historial == 'todo':
        titulo_periodo = 'Todo el historial'
        descripcion_periodo = 'Todos los sobres registrados, ordenados por fecha correspondiente.'
    else:
        todos_sobres = todos_sobres.filter(fecha_correspondiente=fecha_seleccionada)
        titulo_periodo = fecha_seleccionada.strftime('%A %d de %B de %Y').capitalize()
        descripcion_periodo = 'Sobres de una fecha especifica, agrupados por tipo.'
    
    # Agrupar: Bodega vs Camioneros
    sobres_bodega = todos_sobres.filter(tipo='bodega').order_by('-fecha_correspondiente', '-cerrado', '-fecha')
    
    # Para camioneros: agrupar por trabajador
    sobres_camionero = todos_sobres.filter(tipo='camion').order_by('trabajador__first_name', '-fecha_correspondiente', '-cerrado', '-fecha')
    
    # Crear diccionario {camionero: [sobres]}
    sobres_por_camionero = {}
    for sobre in sobres_camionero:
        camionero = sobre.trabajador
        if camionero not in sobres_por_camionero:
            sobres_por_camionero[camionero] = []
        sobres_por_camionero[camionero].append(sobre)

    grupos_camioneros = []
    for camionero, sobres in sobres_por_camionero.items():
        cerrados = sum(1 for sobre in sobres if sobre.cerrado)
        grupos_camioneros.append({
            'camionero': camionero,
            'sobres': sobres,
            'total': len(sobres),
            'cerrados': cerrados,
            'abiertos': len(sobres) - cerrados,
            'total_declarado': sum(int(sobre.monto_declarado or 0) for sobre in sobres),
        })

    total_sobres = todos_sobres.count()
    total_cerrados = todos_sobres.filter(cerrado=True).count()
    total_bodega = sobres_bodega.count()
    total_camion = sobres_camionero.count()
    total_declarado = sum(int(sobre.monto_declarado or 0) for sobre in todos_sobres)
    
    context = {
        'modo_historial': modo_historial,
        'fecha_seleccionada': fecha_seleccionada,
        'mes_seleccionado': primer_dia_mes.strftime('%Y-%m'),
        'hoy': hoy,
        'titulo_periodo': titulo_periodo,
        'descripcion_periodo': descripcion_periodo,
        'sobres_bodega': sobres_bodega,
        'grupos_camioneros': grupos_camioneros,
        'total_sobres': total_sobres,
        'total_cerrados': total_cerrados,
        'total_abiertos': total_sobres - total_cerrados,
        'total_bodega': total_bodega,
        'total_camion': total_camion,
        'total_declarado': total_declarado,
    }

    return render(request, 'sobres/historial_sobres.html', context)


# Jefe/Bodeguero: imprimir sobre para descarga/impresión
@login_required
def imprimir_sobre_diario(request, sobre_id):
    """Generar versión imprimible del sobre (HTML compacto, estilo Excel)."""
    resp = require_roles(request, ["bodeguero", "jefe", "admin"], "index", "No tienes permiso para imprimir sobres.")
    if resp:
        return resp

    sobre = get_object_or_404(SobreDiario, id=sobre_id)
    lineas = sobre.lineas.all().annotate(
        tipo_orden=Case(
            When(balon__tipo_gas='normal', then=Value(0)),
            When(balon__tipo_gas='catalitico', then=Value(1)),
            When(balon__tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by('tipo_orden', '-balon__peso_neto_gas')
    pagos = sobre.pagos.all()
    gastos = sobre.gastos.all()

    # Calcular totales y subtotales por línea
    lineas_data = []
    total_venta = 0
    total_cantidad_calc = 0
    total_cantidad_decl = 0
    total_diferencia = 0
    total_kilos = 0
    
    for linea in lineas:
        subtotal = linea.cantidad_declarada * linea.precio_venta_unitario
        diferencia = linea.cantidad_declarada - linea.cantidad_calculada
        kilos = linea.cantidad_declarada * linea.balon.peso_neto_gas
        
        lineas_data.append({
            'linea': linea,
            'subtotal': subtotal,
            'diferencia': diferencia
        })
        
        total_venta += subtotal
        total_cantidad_calc += linea.cantidad_calculada
        total_cantidad_decl += linea.cantidad_declarada
        total_diferencia += diferencia
        total_kilos += kilos
    
    total_pagos = sum(pago.monto for pago in pagos)
    total_gastos = sum(gasto.monto for gasto in gastos)
    total_contabilizado = total_pagos + total_gastos
    diferencia = total_venta - total_contabilizado

    # Kilos acumulados en el mes (camioneros y bodega)
    total_kilos_mes = 0
    if sobre.tipo != 'bodega' and sobre.trabajador:
        from django.db.models import DecimalField as DField
        sobres_mes = SobreDiario.objects.filter(
            tipo='camion',
            trabajador=sobre.trabajador,
            cerrado=True,
            fecha_correspondiente__year=sobre.fecha_correspondiente.year,
            fecha_correspondiente__month=sobre.fecha_correspondiente.month,
        )
        total_kilos_mes = LineaSobre.objects.filter(
            sobre__in=sobres_mes
        ).aggregate(
            total=Sum(
                F('cantidad_declarada') * F('balon__peso_neto_gas'),
                output_field=DField()
            )
        )['total'] or 0
    elif sobre.tipo == 'bodega':
        from django.db.models import DecimalField as DField
        sobres_mes = SobreDiario.objects.filter(
            tipo='bodega',
            cerrado=True,
            fecha_correspondiente__year=sobre.fecha_correspondiente.year,
            fecha_correspondiente__month=sobre.fecha_correspondiente.month,
        )
        total_kilos_mes = LineaSobre.objects.filter(
            sobre__in=sobres_mes
        ).aggregate(
            total=Sum(
                F('cantidad_declarada') * F('balon__peso_neto_gas'),
                output_field=DField()
            )
        )['total'] or 0
    elif sobre.tipo == 'bodega':
        from django.db.models import DecimalField as DField
        sobres_mes = SobreDiario.objects.filter(
            tipo='bodega',
            fecha_correspondiente__year=sobre.fecha_correspondiente.year,
            fecha_correspondiente__month=sobre.fecha_correspondiente.month,
        )
        total_kilos_mes = LineaSobre.objects.filter(
            sobre__in=sobres_mes
        ).aggregate(
            total=Sum(
                F('cantidad_declarada') * F('balon__peso_neto_gas'),
                output_field=DField()
            )
        )['total'] or 0

    context = {
        'sobre': sobre,
        'lineas_data': lineas_data,
        'pagos': pagos,
        'gastos': gastos,
        'total_venta': total_venta,
        'total_pagos': total_pagos,
        'total_gastos': total_gastos,
        'total_contabilizado': total_contabilizado,
        'diferencia': diferencia,
        'total_cantidad_calc': total_cantidad_calc,
        'total_cantidad_decl': total_cantidad_decl,
        'total_diferencia': total_diferencia,
        'total_kilos': total_kilos,
        'total_kilos_mes': total_kilos_mes,
    }

    return render(request, 'sobres/imprimir_sobre.html', context)

    ws = wb.active
    ws.title = "Sobre Diario"

    font_title = Font(name='Arial', size=14, bold=True)
    font_header = Font(name='Arial', size=10, bold=True)
    font_normal = Font(name='Arial', size=10)
    font_label = Font(name='Arial', size=10, bold=True)

    fill_yellow = PatternFill(start_color='FFFF00', fill_type='solid')
    fill_light_yellow = PatternFill(start_color='FFFFCC', fill_type='solid')
    fill_green = PatternFill(start_color='C6EFCE', fill_type='solid')
    fill_red = PatternFill(start_color='FFC7CE', fill_type='solid')
    fill_gray = PatternFill(start_color='D3D3D3', fill_type='solid')
    fill_blue_light = PatternFill(start_color='DDEBF7', fill_type='solid')

    border_thin = Border(left=Side(style='thin'), right=Side(style='thin'),
                         top=Side(style='thin'), bottom=Side(style='thin'))
    border_medium = Border(left=Side(style='medium'), right=Side(style='medium'),
                           top=Side(style='medium'), bottom=Side(style='medium'))

    align_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    align_right = Alignment(horizontal='right', vertical='center')

    ws.merge_cells('A1:K1')
    ws['A1'] = "CUENTAS SOBRES - KIM GAS"
    ws['A1'].font = font_title
    ws['A1'].alignment = align_center
    ws['A1'].fill = fill_blue_light

    ws['A2'] = "Responsable:"
    ws['A2'].font = font_header
    ws['A2'].alignment = align_right

    chofer_nombre = "BODEGA" if sobre.tipo == 'bodega' else (sobre.trabajador.get_full_name() or sobre.trabajador.username).upper()
    ws['B2'] = chofer_nombre
    ws['B2'].font = font_header

    ws['D2'] = "FECHA:"
    ws['D2'].font = font_header
    ws['D2'].alignment = align_right

    ws['E2'] = sobre.fecha.day
    ws['F2'] = sobre.fecha.strftime("%b").upper()
    ws['G2'] = sobre.fecha.year
    for col in 'EFG':
        ws[f'{col}2'].font = font_header
        ws[f'{col}2'].alignment = align_center
        ws[f'{col}2'].fill = fill_yellow
        ws[f'{col}2'].border = border_thin

    start_col = 1
    col = start_col + 1

    ws.cell(row=4, column=start_col).value = "TIPO BALÓN"
    ws.cell(row=4, column=start_col).font = font_label
    ws.cell(row=4, column=start_col).fill = fill_blue_light
    ws.cell(row=4, column=start_col).alignment = align_center
    ws.cell(row=4, column=start_col).border = border_thin

    for linea in lineas:
        ws.cell(row=4, column=col).value = f"{linea.balon.peso_neto_gas} KG"
        ws.cell(row=4, column=col).font = font_header
        ws.cell(row=4, column=col).alignment = align_center
        ws.cell(row=4, column=col).fill = fill_blue_light
        ws.cell(row=4, column=col).border = border_thin
        col += 1

    end_col_balones = col - 1

    ws.cell(row=5, column=start_col).value = "PRECIO UNITARIO"
    ws.cell(row=5, column=start_col).font = font_label
    ws.cell(row=5, column=start_col).fill = fill_yellow
    ws.cell(row=5, column=start_col).alignment = align_center
    ws.cell(row=5, column=start_col).border = border_thin

    col = start_col + 1
    for linea in lineas:
        cell = ws.cell(row=5, column=col)
        cell.value = int(linea.precio_venta_unitario or 0)
        cell.fill = fill_yellow
        cell.font = font_header
        cell.alignment = align_center
        cell.border = border_thin
        cell.number_format = '#,##0'
        col += 1

    ws.cell(row=6, column=start_col).value = "CANT. CALCULADA (APP)"
    ws.cell(row=6, column=start_col).font = font_label
    ws.cell(row=6, column=start_col).fill = fill_gray
    ws.cell(row=6, column=start_col).alignment = align_center
    ws.cell(row=6, column=start_col).border = border_thin

    col = start_col + 1
    for linea in lineas:
        cell = ws.cell(row=6, column=col)
        cell.value = linea.cantidad_calculada or 0
        cell.alignment = align_center
        cell.border = border_thin
        cell.font = font_normal
        col += 1

    ws.cell(row=7, column=start_col).value = "CANT. DECLARADA (REAL)"
    ws.cell(row=7, column=start_col).font = font_label
    ws.cell(row=7, column=start_col).fill = fill_gray
    ws.cell(row=7, column=start_col).alignment = align_center
    ws.cell(row=7, column=start_col).border = border_thin

    col = start_col + 1
    for linea in lineas:
        cell = ws.cell(row=7, column=col)
        cell.value = linea.cantidad_declarada or 0
        cell.alignment = align_center
        cell.border = border_thin
        cell.font = Font(bold=True)
        if (linea.cantidad_declarada or 0) == (linea.cantidad_calculada or 0):
            cell.fill = fill_green
        else:
            cell.fill = fill_light_yellow
        col += 1

    ws.cell(row=8, column=start_col).value = "DIFERENCIA"
    ws.cell(row=8, column=start_col).font = font_label
    ws.cell(row=8, column=start_col).fill = fill_gray
    ws.cell(row=8, column=start_col).alignment = align_center
    ws.cell(row=8, column=start_col).border = border_thin

    col = start_col + 1
    for linea in lineas:
        diff = (linea.cantidad_declarada or 0) - (linea.cantidad_calculada or 0)
        cell = ws.cell(row=8, column=col)
        cell.value = diff
        cell.alignment = align_center
        cell.border = border_thin
        if diff != 0:
            cell.font = Font(bold=True, color="FF0000")
            cell.fill = fill_red
        else:
            cell.font = Font(bold=True)
        col += 1

    ws.cell(row=9, column=start_col).value = "SUBTOTAL VENTA"
    ws.cell(row=9, column=start_col).font = font_label
    ws.cell(row=9, column=start_col).fill = fill_light_yellow
    ws.cell(row=9, column=start_col).alignment = align_center
    ws.cell(row=9, column=start_col).border = border_thin

    col = start_col + 1
    for _ in range(len(lineas)):
        precio_coord = f"{get_column_letter(col)}5"
        decl_coord = f"{get_column_letter(col)}7"
        cell = ws.cell(row=9, column=col)
        cell.value = f"={decl_coord}*{precio_coord}"
        cell.number_format = '#,##0'
        cell.alignment = align_center
        cell.border = border_thin
        cell.font = Font(bold=True)
        cell.fill = fill_light_yellow
        col += 1

    ventas_col = end_col_balones + 2

    ws.cell(row=5, column=ventas_col).value = "TOTAL VENTAS"
    ws.cell(row=5, column=ventas_col).font = Font(bold=True, size=11)
    ws.cell(row=5, column=ventas_col).alignment = align_right
    ws.cell(row=5, column=ventas_col).border = border_thin

    if lineas.exists():
        ws.cell(row=5, column=ventas_col + 1).value = f"=SUM({get_column_letter(start_col+1)}9:{get_column_letter(end_col_balones)}9)"
    else:
        ws.cell(row=5, column=ventas_col + 1).value = 0
    ws.cell(row=5, column=ventas_col + 1).fill = fill_yellow
    ws.cell(row=5, column=ventas_col + 1).number_format = '#,##0'
    ws.cell(row=5, column=ventas_col + 1).font = Font(bold=True, size=12)
    ws.cell(row=5, column=ventas_col + 1).alignment = align_right
    ws.cell(row=5, column=ventas_col + 1).border = border_medium

    ws.cell(row=6, column=ventas_col).value = "TOTAL KILOS"
    ws.cell(row=6, column=ventas_col).font = Font(bold=True, size=11)
    ws.cell(row=6, column=ventas_col).alignment = align_right
    ws.cell(row=6, column=ventas_col).border = border_thin

    if lineas.exists():
        kilos_parts = [f"({get_column_letter(c)}7*{lineas[i].balon.peso_neto_gas})"
                      for i, c in enumerate(range(start_col+1, end_col_balones+1))]
        ws.cell(row=6, column=ventas_col + 1).value = "=" + "+".join(kilos_parts)
    else:
        ws.cell(row=6, column=ventas_col + 1).value = 0
    ws.cell(row=6, column=ventas_col + 1).fill = fill_yellow
    ws.cell(row=6, column=ventas_col + 1).number_format = '#,##0'
    ws.cell(row=6, column=ventas_col + 1).font = Font(bold=True, size=12)
    ws.cell(row=6, column=ventas_col + 1).alignment = align_right
    ws.cell(row=6, column=ventas_col + 1).border = border_medium

    pagos_col_label = ventas_col
    pagos_col_monto = ventas_col + 1

    pagos_row_start = 10
    ws.cell(row=pagos_row_start, column=pagos_col_label).value = "TIPO PAGO"
    ws.cell(row=pagos_row_start, column=pagos_col_monto).value = "MONTO"
    for c in [pagos_col_label, pagos_col_monto]:
        ws.cell(row=pagos_row_start, column=c).font = font_header
        ws.cell(row=pagos_row_start, column=c).fill = fill_blue_light
        ws.cell(row=pagos_row_start, column=c).alignment = align_center
        ws.cell(row=pagos_row_start, column=c).border = border_thin

    row_p = pagos_row_start + 1
    total_pagos = 0

    tipo_map = {
        'abono': 'Abono Caja',
        'transferencia': 'Transferencia',
        'visa': 'Visa/POS',
        'cheque': 'Cheque',
        'efectivo': 'Efectivo',
        'otro': 'Otro',
    }

    for pago in sobre.pagos.all():
        tipo = tipo_map.get(pago.tipo_pago, pago.tipo_pago.upper())
        ws.cell(row=row_p, column=pagos_col_label).value = tipo
        ws.cell(row=row_p, column=pagos_col_label).border = border_thin
        ws.cell(row=row_p, column=pagos_col_monto).value = pago.monto
        ws.cell(row=row_p, column=pagos_col_monto).number_format = '#,##0'
        ws.cell(row=row_p, column=pagos_col_monto).alignment = align_right
        ws.cell(row=row_p, column=pagos_col_monto).border = border_thin
        total_pagos += pago.monto
        row_p += 1

    ws.cell(row=row_p, column=pagos_col_label).value = "TOTAL PAGOS"
    ws.cell(row=row_p, column=pagos_col_label).font = font_header
    ws.cell(row=row_p, column=pagos_col_label).fill = fill_blue_light
    ws.cell(row=row_p, column=pagos_col_label).border = border_thin
    ws.cell(row=row_p, column=pagos_col_monto).value = total_pagos
    ws.cell(row=row_p, column=pagos_col_monto).font = Font(bold=True, size=11)
    ws.cell(row=row_p, column=pagos_col_monto).number_format = '#,##0'
    ws.cell(row=row_p, column=pagos_col_monto).alignment = align_right
    ws.cell(row=row_p, column=pagos_col_monto).fill = fill_blue_light
    ws.cell(row=row_p, column=pagos_col_monto).border = border_medium

    gastos_row_start = row_p + 2
    ws.cell(row=gastos_row_start, column=pagos_col_label).value = "DESCRIPCIÓN GASTO"
    ws.cell(row=gastos_row_start, column=pagos_col_monto).value = "MONTO"
    for c in [pagos_col_label, pagos_col_monto]:
        ws.cell(row=gastos_row_start, column=c).font = font_header
        ws.cell(row=gastos_row_start, column=c).fill = fill_blue_light
        ws.cell(row=gastos_row_start, column=c).alignment = align_center
        ws.cell(row=gastos_row_start, column=c).border = border_thin

    row_g = gastos_row_start + 1
    total_gastos = 0

    for gasto in sobre.gastos.all():
        ws.cell(row=row_g, column=pagos_col_label).value = gasto.descripcion[:40]
        ws.cell(row=row_g, column=pagos_col_label).border = border_thin
        ws.cell(row=row_g, column=pagos_col_monto).value = gasto.monto
        ws.cell(row=row_g, column=pagos_col_monto).number_format = '#,##0'
        ws.cell(row=row_g, column=pagos_col_monto).alignment = align_right
        ws.cell(row=row_g, column=pagos_col_monto).border = border_thin
        total_gastos += gasto.monto
        row_g += 1

    ws.cell(row=row_g, column=pagos_col_label).value = "TOTAL GASTOS"
    ws.cell(row=row_g, column=pagos_col_label).font = font_header
    ws.cell(row=row_g, column=pagos_col_label).fill = fill_blue_light
    ws.cell(row=row_g, column=pagos_col_label).border = border_thin
    ws.cell(row=row_g, column=pagos_col_monto).value = total_gastos
    ws.cell(row=row_g, column=pagos_col_monto).font = Font(bold=True, size=11)
    ws.cell(row=row_g, column=pagos_col_monto).number_format = '#,##0'
    ws.cell(row=row_g, column=pagos_col_monto).alignment = align_right
    ws.cell(row=row_g, column=pagos_col_monto).fill = fill_blue_light
    ws.cell(row=row_g, column=pagos_col_monto).border = border_medium

    diff_row = row_g + 2
    ws.cell(row=diff_row, column=pagos_col_label).value = "DIFERENCIA FINAL"
    ws.cell(row=diff_row, column=pagos_col_label).font = Font(bold=True, size=12)
    ws.cell(row=diff_row, column=pagos_col_label).border = border_medium
    ws.cell(row=diff_row, column=pagos_col_monto).value = f"={get_column_letter(ventas_col + 1)}5 - {get_column_letter(pagos_col_monto)}{row_p} - {get_column_letter(pagos_col_monto)}{row_g}"
    ws.cell(row=diff_row, column=pagos_col_monto).fill = fill_yellow
    ws.cell(row=diff_row, column=pagos_col_monto).font = Font(bold=True, size=14)
    ws.cell(row=diff_row, column=pagos_col_monto).number_format = '#,##0;[Red]-#,##0'
    ws.cell(row=diff_row, column=pagos_col_monto).alignment = align_right
    ws.cell(row=diff_row, column=pagos_col_monto).border = border_medium

    km_row = diff_row + 3
    ws.cell(row=km_row, column=1).value = "KM RECORRIDOS:"
    ws.cell(row=km_row, column=1).font = font_header
    if sobre.kilometraje_camion:
        ws.cell(row=km_row, column=2).value = sobre.kilometraje_camion
        ws.cell(row=km_row, column=2).fill = fill_yellow
        ws.cell(row=km_row, column=2).font = Font(bold=True)
        ws.cell(row=km_row, column=2).border = border_thin

    ws.cell(row=km_row + 3, column=2).value = "FIRMA RESPONSABLE"
    ws.cell(row=km_row + 3, column=2).font = font_header
    ws.cell(row=km_row + 4, column=2).value = "_" * 40
    ws.cell(row=km_row + 4, column=2).font = Font(size=14)

    ws.column_dimensions['A'].width = 22
    for c in range(start_col + 1, end_col_balones + 1):
        ws.column_dimensions[get_column_letter(c)].width = 12
    ws.column_dimensions[get_column_letter(pagos_col_label)].width = 24
    ws.column_dimensions[get_column_letter(pagos_col_monto)].width = 16

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    tipo = "BODEGA" if sobre.tipo == 'bodega' else (sobre.trabajador.username.upper() or "CAM")
    filename = f"Sobre_{tipo}_{sobre.fecha.strftime('%d%m%Y')}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    wb.save(response)
    return response


# Jefe/Admin: consulta simple de sobres con tabla y resaltado de inconsistencias
@login_required
def consulta_sobres_jefe(request):
    """Tabla condensada de sobres para jefe/admin con filtros y alertas visuales.

    Filtros vía GET: fecha (día), mes (YYYY-MM), trabajador (camionero),
    estado (todos/abiertos/cerrados), tipo (todos/bodega/camion).

    Resalta visualmente sobres con:
    - Diferencia de dinero != 0 (monto declarado no cuadra con app) → rojo.
    - Líneas con cantidad declarada != calculada → naranja.
    """
    resp = require_roles(request, ["jefe", "admin"], "index", "Solo jefes y administradores pueden consultar sobres.")
    if resp:
        return resp

    hoy = today_chile()

    # ── Filtros ────────────────────────────────────────────────
    fecha_str = (request.GET.get('fecha') or '').strip()
    mes_str = (request.GET.get('mes') or '').strip()
    trabajador_str = (request.GET.get('trabajador') or '').strip()
    estado = request.GET.get('estado', 'todos')
    tipo = request.GET.get('tipo', 'todos')
    if estado not in ['todos', 'abiertos', 'cerrados']:
        estado = 'todos'
    if tipo not in ['todos', 'bodega', 'camion']:
        tipo = 'todos'

    sobres_qs = SobreDiario.objects.select_related('trabajador', 'creado_por').prefetch_related('lineas')

    # Filtro por periodo: prioridad mes > fecha
    if mes_str:
        try:
            anio_mes, mes_mes = map(int, mes_str.split('-'))
            inicio_mes = date(anio_mes, mes_mes, 1)
            fin_mes = date(anio_mes, mes_mes, monthrange(anio_mes, mes_mes)[1])
            sobres_qs = sobres_qs.filter(
                fecha_correspondiente__gte=inicio_mes,
                fecha_correspondiente__lte=fin_mes,
            )
        except (TypeError, ValueError):
            messages.warning(request, 'El mes indicado no es valido. Se muestran todos los sobres.')
    elif fecha_str:
        try:
            fecha_sel = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            sobres_qs = sobres_qs.filter(fecha_correspondiente=fecha_sel)
        except ValueError:
            messages.warning(request, 'La fecha indicada no es valida. Se muestran todos los sobres.')

    if trabajador_str:
        try:
            sobres_qs = sobres_qs.filter(trabajador_id=int(trabajador_str))
        except ValueError:
            pass

    if estado == 'cerrados':
        sobres_qs = sobres_qs.filter(cerrado=True)
    elif estado == 'abiertos':
        sobres_qs = sobres_qs.filter(cerrado=False)

    if tipo == 'bodega':
        sobres_qs = sobres_qs.filter(tipo='bodega')
    elif tipo == 'camion':
        sobres_qs = sobres_qs.filter(tipo='camion')

    # Orden por creación (id desc): la columna # queda siempre correlativa.
    sobres_qs = sobres_qs.order_by('-id')

    # ── Métricas del resultado ─────────────────────────────────
    total_sobres = sobres_qs.count()
    total_cerrados = sobres_qs.filter(cerrado=True).count()
    total_abiertos = total_sobres - total_cerrados

    # ── Paginación y flags por página ──────────────────────────
    paginator = Paginator(sobres_qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    filas = []
    total_dinero_alertas = 0
    total_lineas_alertas = 0
    total_declarado = 0
    for sobre in page_obj.object_list:
        flag_dinero = (sobre.diferencia or 0) != 0
        flag_lineas = any(
            (linea.cantidad_declarada or 0) != (linea.cantidad_calculada or 0)
            for linea in sobre.lineas.all()
        )
        if flag_dinero:
            total_dinero_alertas += 1
        if flag_lineas:
            total_lineas_alertas += 1
        total_declarado += int(sobre.monto_declarado or 0)
        filas.append({
            'sobre': sobre,
            'flag_dinero': flag_dinero,
            'flag_lineas': flag_lineas,
        })

    camioneros = Usuario.objects.filter(rol='camionero', is_active=True).order_by('first_name', 'last_name')

    context = {
        'filas': filas,
        'page_obj': page_obj,
        'total_sobres': total_sobres,
        'total_cerrados': total_cerrados,
        'total_abiertos': total_abiertos,
        'total_declarado': total_declarado,
        'total_dinero_alertas': total_dinero_alertas,
        'total_lineas_alertas': total_lineas_alertas,
        'camioneros': camioneros,
        'fecha_str': fecha_str,
        'mes_str': mes_str,
        'trabajador_str': trabajador_str,
        'estado': estado,
        'tipo': tipo,
        'hoy': hoy,
    }

    return render(request, 'sobres/consulta_sobres.html', context)

