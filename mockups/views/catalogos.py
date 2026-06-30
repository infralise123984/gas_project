"""Vistas HTTP — catalogos."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from mockups.forms import (
    SectorForm,
    TipoBalonForm,
)
from mockups.models import (
    AuditoriaAccion,
    Bodega,
    DetallePedido,
    HistorialPrecioBalon,
    Pedido,
    Sector,
    TipoBalon,
    Usuario,
)
from mockups.services.catalogos import get_balones_activos_ordenados
from mockups.utils.permisos import require_roles, filtrar_por_bodega

security_logger = logging.getLogger('security')
audit_logger = logging.getLogger('audit')



@login_required
def historial_precios(request):
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para ver el historial de precios.")
    if resp:
        return resp

    balones = TipoBalon.objects.all().order_by('nombre')
    balon_id = request.GET.get('balon')
    balon_seleccionado = None
    historial = []
    precios_actuales = None

    if balon_id:
        try:
            balon_seleccionado = TipoBalon.objects.get(id=balon_id)
            historial = HistorialPrecioBalon.objects.filter(
                nombre_balon=balon_seleccionado.nombre
            ).order_by('-fecha_cambio')

            # Precios actuales del balón seleccionado
            precios_actuales = {
                'compra': balon_seleccionado.precio_compra,
                'local': balon_seleccionado.precio_local,
                'domicilio': balon_seleccionado.precio_domicilio,
                'activo': balon_seleccionado.activo,
            }

        except TipoBalon.DoesNotExist:
            messages.warning(request, "Balón no encontrado.")
    else:
        historial = HistorialPrecioBalon.objects.all().order_by('-fecha_cambio')[:50]

    # Preparar datos para Chart.js (igual que antes)
    chart_data = None
    if balon_seleccionado and historial:
        labels = []
        compra_data = []
        local_data = []
        domicilio_data = []

        for reg in historial.order_by('fecha_cambio'):  # cronológico
            labels.append(reg.fecha_cambio.strftime('%d/%m/%Y %H:%M'))
            compra_data.append(float(reg.precio_compra_anterior))
            local_data.append(float(reg.precio_local_anterior))
            domicilio_data.append(float(reg.precio_domicilio_anterior))

        chart_data = {
            'labels': labels,
            'compra': compra_data,
            'local': local_data,
            'domicilio': domicilio_data,
            'nombre_balon': balon_seleccionado.nombre
        }

    context = {
        'balones': balones,
        'balon_seleccionado': balon_seleccionado,
        'historial': historial,
        'chart_data': chart_data,
        'precios_actuales': precios_actuales,
        'title': 'Historial de Cambios de Precios' + (f' - {balon_seleccionado.nombre}' if balon_seleccionado else '')
    }

    return render(request, 'balones/historial_precios.html', context)



# ══════════════════════════════════════════════════════════════
# 4B. GESTIÓN DE TIPOS DE BALÓN
# ══════════════════════════════════════════════════════════════

# Admin/Jefe/Bodeguero: ver lista de balones
@login_required
def gestionar_balones_lista(request):
    """Listar todos los tipos de balones disponibles. Acceso: Admin/Jefe/Bodeguero."""
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para gestionar balones.")
    if resp:
        return resp

    balones = get_balones_activos_ordenados(solo_activos=False)

    # Procesar POST para edición masiva de precios
    if request.method == "POST":
        cambios_realizados = False
        
        # Verificar si el formulario incluye campos de disponibilidad (checkboxes activo_*)
        # Si no hay ningún checkbox en el form, mantener valores actuales de 'activo'
        form_incluye_activo = any(k.startswith('activo_') for k in request.POST)

        for balon in balones:
            # Claves de los campos del formulario
            compra_key    = f"precio_compra_{balon.id}"
            local_key     = f"precio_local_{balon.id}"
            dom_key       = f"precio_domicilio_{balon.id}"
            activo_key    = f"activo_{balon.id}"

            # Valores enviados
            nuevo_compra_str    = request.POST.get(compra_key)
            nuevo_local_str     = request.POST.get(local_key)
            nuevo_dom_str       = request.POST.get(dom_key)
            # Solo procesar 'activo' si el formulario incluye esos campos, sino mantener valor actual
            nuevo_activo        = activo_key in request.POST if form_incluye_activo else balon.activo

            try:
                nuevo_compra     = int(nuevo_compra_str) if nuevo_compra_str else balon.precio_compra
                nuevo_local      = int(nuevo_local_str)  if nuevo_local_str  else balon.precio_local
                nuevo_domicilio  = int(nuevo_dom_str)    if nuevo_dom_str    else balon.precio_domicilio

                if nuevo_compra < 0 or nuevo_local < 0 or nuevo_domicilio < 0:
                    raise ValueError("Precios no pueden ser negativos")
            except ValueError:
                messages.error(request, f"Precio inválido para {balon.nombre}. Se ignoraron cambios en esta fila.")
                continue

            # Detectar si realmente hay algún cambio
            hubo_cambio = (
                balon.precio_compra     != nuevo_compra or
                balon.precio_local      != nuevo_local or
                balon.precio_domicilio  != nuevo_domicilio or
                balon.activo            != nuevo_activo
            )

            if hubo_cambio:
                # Guardar historial antes de aplicar cambios
                HistorialPrecioBalon.objects.create(
                    nombre_balon       = balon.nombre,
                    precio_compra_anterior     = balon.precio_compra,
                    precio_local_anterior      = balon.precio_local,
                    precio_domicilio_anterior  = balon.precio_domicilio,
                    activo_anterior            = balon.activo,
                    actualizado_por            = request.user,
                )

                # Aplicar nuevos valores
                balon.precio_compra     = nuevo_compra
                balon.precio_local      = nuevo_local
                balon.precio_domicilio  = nuevo_domicilio
                balon.activo            = nuevo_activo
                balon.actualizado_por   = request.user
                balon.save()

                cambios_realizados = True

        if cambios_realizados:
            messages.success(request, "Precios y disponibilidad actualizados correctamente. Historial registrado.")
        else:
            messages.info(request, "No se detectaron cambios válidos.")

        return redirect("balones_lista")
    
    context = {
        'balones': balones,
        'title': 'Gestión de Balones',
        'puede_eliminar': request.user.rol in ['jefe', 'admin']
    }
    
    return render(request, 'balones/gestionar_balones.html', context)



# Admin/Jefe/Bodeguero: crear nuevo tipo de balón
@login_required
def gestionar_balones_crear(request):
    """Crear nuevo tipo de balón en el sistema. Acceso: admin, jefe, bodeguero."""
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para crear balones.")
    if resp:
        return resp
    
    if request.method == "POST":
        form = TipoBalonForm(request.POST)
        
        if form.is_valid():
            balon = form.save(commit=False)
            balon.actualizado_por = request.user
            balon.save()

            AuditoriaAccion.registrar(
                request=request,
                tipo='BALON_CREATE',
                descripcion=f"Creación de balón: {balon.nombre}",
                objeto=balon,
                datos_nuevos={
                    'nombre': balon.nombre,
                    'peso_neto_gas': balon.peso_neto_gas,
                    'tipo_gas': balon.tipo_gas,
                    'precio_compra': int(balon.precio_compra),
                    'precio_local': int(balon.precio_local),
                    'precio_domicilio': int(balon.precio_domicilio),
                    'activo': balon.activo,
                }
            )

            messages.success(request, f"Balón '{balon.nombre}' creado correctamente.")
            return redirect("balones_lista")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = TipoBalonForm()
    
    context = {
        'form': form,
        'title': 'Crear Nuevo Balón',
        'accion': 'Crear'
    }
    
    return render(request, 'balones/gestionar_balon_form.html', context)



# Admin/Jefe/Bodeguero: editar tipo de balón existente
@login_required
def gestionar_balones_editar(request, balon_id):
    """Modificar datos de un tipo de balón existente. Acceso: admin, jefe, bodeguero."""
    resp = require_roles(request, ["jefe", "admin", "bodeguero"], "index", "No tienes permiso para editar balones.")
    if resp:
        return resp
    
    balon = get_object_or_404(TipoBalon, id=balon_id)
    
    if request.method == "POST":
        form = TipoBalonForm(request.POST, instance=balon)
        
        if form.is_valid():
            # Guardar historial del cambio anterior
            balon_anterior = TipoBalon.objects.get(id=balon_id)
            
            balon_actualizado = form.save(commit=False)
            balon_actualizado.actualizado_por = request.user
            
            # Verificar si hay cambios reales
            hubo_cambio = (
                balon_anterior.precio_compra != balon_actualizado.precio_compra or
                balon_anterior.precio_local != balon_actualizado.precio_local or
                balon_anterior.precio_domicilio != balon_actualizado.precio_domicilio or
                balon_anterior.activo != balon_actualizado.activo
            )
            
            if hubo_cambio:
                HistorialPrecioBalon.objects.create(
                    nombre_balon=balon_anterior.nombre,
                    precio_compra_anterior=balon_anterior.precio_compra,
                    precio_local_anterior=balon_anterior.precio_local,
                    precio_domicilio_anterior=balon_anterior.precio_domicilio,
                    activo_anterior=balon_anterior.activo,
                    actualizado_por=request.user,
                )

            datos_anteriores = {
                'nombre': balon_anterior.nombre,
                'peso_neto_gas': balon_anterior.peso_neto_gas,
                'tipo_gas': balon_anterior.tipo_gas,
                'precio_compra': int(balon_anterior.precio_compra),
                'precio_local': int(balon_anterior.precio_local),
                'precio_domicilio': int(balon_anterior.precio_domicilio),
                'activo': balon_anterior.activo,
            }
            
            balon_actualizado.save()

            datos_nuevos = {
                'nombre': balon_actualizado.nombre,
                'peso_neto_gas': balon_actualizado.peso_neto_gas,
                'tipo_gas': balon_actualizado.tipo_gas,
                'precio_compra': int(balon_actualizado.precio_compra),
                'precio_local': int(balon_actualizado.precio_local),
                'precio_domicilio': int(balon_actualizado.precio_domicilio),
                'activo': balon_actualizado.activo,
            }

            if form.changed_data:
                AuditoriaAccion.registrar(
                    request=request,
                    tipo='BALON_UPDATE',
                    descripcion=f"Edición de balón: {balon_actualizado.nombre}",
                    objeto=balon_actualizado,
                    datos_anteriores=datos_anteriores,
                    datos_nuevos=datos_nuevos,
                )

            messages.success(request, f"Balón '{balon_actualizado.nombre}' actualizado correctamente.")
            return redirect("balones_lista")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = TipoBalonForm(instance=balon)
    
    context = {
        'form': form,
        'balon': balon,
        'title': f'Editar Balón - {balon.nombre}',
        'accion': 'Editar'
    }
    
    return render(request, 'balones/gestionar_balon_form.html', context)



# Admin/Jefe: eliminar tipo de balón
@login_required
@require_POST
def gestionar_balones_eliminar(request, balon_id):
    """Eliminar un tipo de balón sin pedidos asociados. Solo admin y jefe."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para eliminar balones.")
    if resp:
        return resp
    
    balon = get_object_or_404(TipoBalon, id=balon_id)
    
    # Verificar si el balón tiene detalles de pedidos
    detalles = DetallePedido.objects.filter(balon=balon)
    
    if detalles.exists():
        messages.error(
            request,
            f"No se puede eliminar '{balon.nombre}' porque tiene {detalles.count()} registro(s) de venta asociado(s). "
            "Desactívalo en lugar de eliminarlo."
        )
        return redirect("balones_lista")
    
    nombre_balon = balon.nombre

    AuditoriaAccion.registrar(
        request=request,
        tipo='BALON_DELETE',
        descripcion=f"Eliminación de balón: {nombre_balon}",
        objeto=balon,
        datos_anteriores={
            'nombre': balon.nombre,
            'peso_neto_gas': balon.peso_neto_gas,
            'tipo_gas': balon.tipo_gas,
            'precio_compra': int(balon.precio_compra),
            'precio_local': int(balon.precio_local),
            'precio_domicilio': int(balon.precio_domicilio),
            'activo': balon.activo,
        }
    )

    balon.delete()
    messages.success(request, f"Balón '{nombre_balon}' eliminado correctamente.")
    
    return redirect("balones_lista")



@login_required
def auditoria_lista(request):
    """Listado simple de todos los registros de auditoría. Solo admin."""
    if not request.user.is_superuser:
        resp = require_roles(request, ["admin"], "index", "No tienes permiso para ver la auditoría del sistema.")
        if resp:
            return resp

    registros = AuditoriaAccion.objects.select_related('usuario').all().order_by('-fecha')
    paginator = Paginator(registros, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'total_registros': paginator.count,
    }
    return render(request, 'auditoria/lista_auditoria.html', context)



@login_required
def gestionar_sectores_lista(request):
    """Listar sectores del catálogo administrable."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para gestionar sectores.")
    if resp:
        return resp

    sectores = list(
        filtrar_por_bodega(Sector.objects.all().order_by('zona', 'nombre'), request)
    )
    conteos_pedidos = {
        item['sector']: item['total']
        for item in Pedido.objects.exclude(sector='').values('sector').annotate(total=Count('id'))
    }

    for sector in sectores:
        sector.pedidos_existentes = conteos_pedidos.get(sector.nombre, 0)

    context = {
        'sectores': sectores,
        'title': 'Gestión de Sectores',
        'puede_eliminar': request.user.rol == 'admin',
    }
    return render(request, 'sectores/gestionar_sectores.html', context)



@login_required
def gestionar_sectores_crear(request):
    """Crear un sector en el catálogo administrable."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para crear sectores.")
    if resp:
        return resp

    if request.method == 'POST':
        form = SectorForm(request.POST)
        if form.is_valid():
            sector = form.save()
            messages.success(request, f"Sector '{sector.nombre}' creado correctamente.")
            return redirect('sectores_lista')
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    else:
        form = SectorForm()

    context = {
        'form': form,
        'title': 'Crear Sector',
        'accion': 'Crear',
    }
    return render(request, 'sectores/gestionar_sector_form.html', context)



@login_required
def gestionar_sectores_editar(request, sector_id):
    """Editar un sector del catálogo administrable."""
    resp = require_roles(request, ["jefe", "admin"], "index", "No tienes permiso para editar sectores.")
    if resp:
        return resp

    sector = get_object_or_404(Sector, id=sector_id)

    if request.method == 'POST':
        form = SectorForm(request.POST, instance=sector)
        if form.is_valid():
            sector = form.save()
            messages.success(request, f"Sector '{sector.nombre}' actualizado correctamente.")
            return redirect('sectores_lista')
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    else:
        form = SectorForm(instance=sector)

    context = {
        'form': form,
        'sector': sector,
        'title': f'Editar Sector - {sector.nombre}',
        'accion': 'Editar',
    }
    return render(request, 'sectores/gestionar_sector_form.html', context)



@login_required
@require_POST
def gestionar_sectores_eliminar(request, sector_id):
    """Eliminar un sector del catálogo administrable."""
    resp = require_roles(request, ["admin"], "index", "No tienes permiso para eliminar sectores.")
    if resp:
        return resp

    sector = get_object_or_404(Sector, id=sector_id)
    nombre = sector.nombre
    pedidos_asociados = Pedido.objects.filter(sector=nombre).count()
    sector.delete()

    if pedidos_asociados:
        messages.warning(
            request,
            f"Sector '{nombre}' eliminado del catálogo. Los {pedidos_asociados} pedido(s) históricos con ese texto no fueron modificados."
        )
    else:
        messages.success(request, f"Sector '{nombre}' eliminado correctamente.")

    return redirect('sectores_lista')


# ──────────────────────────────────────────────────────────────
# ADMIN: ASIGNACIÓN DE BODEGA A USUARIOS
# ──────────────────────────────────────────────────────────────

@login_required
def admin_usuarios_bodega(request):
    """Lista usuarios y permite asignar/editar su bodega. Solo admin."""
    resp = require_roles(request, ["admin"], "index", "No tienes permiso.")
    if resp:
        return resp

    bodegas = Bodega.objects.filter(activo=True).order_by('nombre')
    usuarios = Usuario.objects.all().order_by('rol', 'username')

    if request.method == 'POST':
        for usuario in usuarios:
            bodega_id_key = f'bodega_{usuario.id}'
            if bodega_id_key in request.POST:
                new_bodega_id = request.POST.get(bodega_id_key)
                if new_bodega_id:
                    usuario.bodega_id = int(new_bodega_id)
                else:
                    usuario.bodega = None
                usuario.save(update_fields=['bodega'])

        messages.success(request, "Bodegas asignadas correctamente.")
        return redirect('admin_usuarios_bodega')

    return render(request, 'admin/usuarios_bodega.html', {
        'usuarios': usuarios,
        'bodegas': bodegas,
    })


@login_required
def admin_crear_bodega(request):
    """Crear nueva bodega. Solo admin."""
    resp = require_roles(request, ["admin"], "index", "No tienes permiso.")
    if resp:
        return resp

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        copiar_precios_de = request.POST.get('copiar_precios_de')

        if not nombre:
            messages.error(request, "El nombre es obligatorio.")
            return redirect('admin_crear_bodega')

        if Bodega.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, f"Ya existe una bodega llamada '{nombre}'.")
            return redirect('admin_crear_bodega')

        bodega = Bodega.objects.create(nombre=nombre, direccion=direccion)

        # Opcional: copiar sectores de otra bodega
        if copiar_precios_de:
            bodega_origen = Bodega.objects.filter(id=copiar_precios_de).first()
            if bodega_origen:
                for sector in bodega_origen.sectores.all():
                    Sector.objects.create(
                        nombre=sector.nombre,
                        codigo=f"{slugify(nombre)}-{sector.codigo}"[:120],
                        zona=sector.zona,
                        activo=sector.activo,
                        bodega=bodega,
                    )

        messages.success(request, f"Bodega '{nombre}' creada correctamente.")
        return redirect('admin_usuarios_bodega')

    bodegas_existentes = Bodega.objects.filter(activo=True).order_by('nombre')
    return render(request, 'admin/crear_bodega.html', {
        'bodegas_existentes': bodegas_existentes,
    })


@login_required
def admin_lista_bodegas(request):
    """Listar y gestionar bodegas (activar/desactivar, editar). Admin y jefe."""
    resp = require_roles(request, ["admin", "jefe"], "index", "No tienes permiso.")
    if resp:
        return resp

    bodegas = Bodega.objects.all().order_by('nombre')

    if request.method == 'POST':
        bodega_id = request.POST.get('bodega_id')
        accion = request.POST.get('accion')
        bodega = get_object_or_404(Bodega, id=bodega_id)

        if accion == 'toggle_activo' and request.user.rol == 'admin':
            bodega.activo = not bodega.activo
            bodega.save(update_fields=['activo'])
            estado = "activada" if bodega.activo else "desactivada"
            messages.success(request, f"Bodega '{bodega.nombre}' {estado}.")
        elif accion == 'editar' and request.user.rol == 'admin':
            nombre = request.POST.get('nombre', '').strip()
            direccion = request.POST.get('direccion', '').strip()
            if nombre:
                bodega.nombre = nombre
                bodega.direccion = direccion
                bodega.save(update_fields=['nombre', 'direccion'])
                messages.success(request, f"Bodega '{bodega.nombre}' actualizada.")
            else:
                messages.error(request, "El nombre es obligatorio.")
        return redirect('admin_lista_bodegas')

    # Stats por bodega
    for b in bodegas:
        b.total_pedidos = b.pedidos.count()
        b.total_usuarios = b.trabajadores.count()
        b.total_sobres = b.sobres.count()

    return render(request, 'admin/lista_bodegas.html', {
        'bodegas': bodegas,
    })

