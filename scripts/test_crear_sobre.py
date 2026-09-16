"""
Script para probar la creación del sobre de bodega
Ejecutar: python manage.py shell < scripts/test_crear_sobre.py
"""

from mockups.models import SobreDiario, LineaSobre, TipoBalon, Pedido, Usuario
from django.utils import timezone
from django.db.models import Sum
from datetime import datetime, time
from zoneinfo import ZoneInfo

# Obtener usuario y fecha
usuario = Usuario.objects.filter(is_active=True).first()
tz_chile = ZoneInfo('America/Santiago')
hoy = timezone.now().astimezone(tz_chile).date()

print(f"\n{'='*80}")
print(f"TEST: Creando sobre de bodega para {hoy}")
print(f"{'='*80}\n")

# Crear o buscar el sobre
sobre, creado = SobreDiario.objects.get_or_create(
    fecha=hoy,
    tipo='bodega',
    trabajador=None,
    defaults={
        'creado_por': usuario,
        'fecha_correspondiente': hoy,
    }
)

if creado:
    print(f"✅ Sobre #{sobre.id} CREADO\n")
else:
    print(f"ℹ️ Sobre #{sobre.id} ya existía (actualizando líneas...)\n")

# Obtener balones activos
balones_activos = TipoBalon.objects.filter(activo=True).order_by('peso_neto_gas')
print(f"Balones activos a procesar: {balones_activos.count()}\n")

# Crear rango de tiempo (mismo método de views.py)
tz_utc = ZoneInfo('UTC')
inicio_dia = datetime.combine(hoy, time.min)
inicio_dia = timezone.make_aware(inicio_dia, tz_chile).astimezone(tz_utc)
fin_dia = datetime.combine(hoy, time.max)
fin_dia = timezone.make_aware(fin_dia, tz_chile).astimezone(tz_utc)

print(f"Rango búsqueda: {inicio_dia} a {fin_dia}\n")

# Procesar cada balón
for balon in balones_activos:
    # Buscar pedidos de bodega (local)
    qs_pedidos = Pedido.objects.filter(
        fecha__gte=inicio_dia,
        fecha__lte=fin_dia,
        origen='local',
        estado='entregado'
    )
    
    qty_calc = qs_pedidos.filter(detalles__balon=balon).aggregate(total=Sum('detalles__cantidad'))['total'] or 0
    
    if qty_calc > 0 or True:  # Mostrar todos
        # Buscar si ya existe
        linea_existente = LineaSobre.objects.filter(sobre=sobre, balon=balon).first()
        
        if linea_existente:
            # Actualizar
            linea_existente.cantidad_calculada = qty_calc
            linea_existente.cantidad_declarada = qty_calc
            linea_existente.precio_venta_unitario = balon.precio_local
            linea_existente.save()
            print(f"  🔄 {balon.nombre}: {qty_calc} unidades (ACTUALIZADO)")
        else:
            # Crear nueva
            if creado:
                linea = LineaSobre.objects.create(
                    sobre=sobre,
                    balon=balon,
                    cantidad_calculada=qty_calc,
                    cantidad_declarada=qty_calc,
                    precio_venta_unitario=balon.precio_local
                )
                print(f"  ✅ {balon.nombre}: {qty_calc} unidades (CREADO)")
            else:
                print(f"  ⏭️  {balon.nombre}: {qty_calc} unidades (ya existe, sin crear)")

print(f"\n{'='*80}")
print(f"RESUMEN DEL SOBRE #{sobre.id}:")
print(f"{'='*80}\n")

lineas = sobre.lineas.all().order_by('balon__peso_neto_gas')
print(f"Total líneas: {lineas.count()}\n")

for linea in lineas:
    print(f"  {linea.balon.nombre}:")
    print(f"    - Calculada: {linea.cantidad_calculada}")
    print(f"    - Declarada: {linea.cantidad_declarada}")
    print(f"    - Precio: ${linea.precio_venta_unitario}")
    print()

total_balones = sum(l.cantidad_calculada for l in lineas)
print(f"TOTAL BALONES CALCULADOS: {total_balones}")
print(f"\n{'='*80}\n")
