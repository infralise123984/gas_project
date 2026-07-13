"""
Script de debugging para verificar por qué no aparecen los pedidos en el sobre de bodega
Ejecutar con: python manage.py shell < debug_pedidos.py
"""

from django.utils import timezone
from mockups.models import Pedido, TipoBalon, SobreDiario, DetallePedido
from datetime import date, timedelta

# Obtener fecha de hoy (Chile)
hoy = timezone.now().date()
ayer = hoy - timedelta(days=1)

print("\n" + "="*80)
print("DEBUG: ANÁLISIS DE PEDIDOS PARA SOBRE DE BODEGA")
print("="*80)

# 1. Verificar si hay pedidos en la BD
print(f"\n[1] Total de pedidos en la BD: {Pedido.objects.count()}")

# 2. Pedidos por estado
print(f"\n[2] Pedidos por ESTADO:")
for estado, _ in Pedido.ESTADOS:
    count = Pedido.objects.filter(estado=estado).count()
    print(f"    - {estado}: {count}")

# 3. Pedidos por origen
print(f"\n[3] Pedidos por ORIGEN:")
for origen, _ in Pedido.ORIGENES:
    count = Pedido.objects.filter(origen=origen).count()
    print(f"    - {origen}: {count}")

# 4. Pedidos hoy
print(f"\n[4] Pedidos HOJA (fecha__date={hoy}): {Pedido.objects.filter(fecha__date=hoy).count()}")

# 5. Pedidos ayer
print(f"\n[5] Pedidos AYER (fecha__date={ayer}): {Pedido.objects.filter(fecha__date=ayer).count()}")

# 6. Pedidos LOCALES entregados (el filtro actual para bodega)
print(f"\n[6] Pedidos LOCAL + ENTREGADO (sin fecha):")
qs_bodega = Pedido.objects.filter(origen='local', estado='entregado')
print(f"    Total: {qs_bodega.count()}")
for p in qs_bodega[:5]:
    print(f"    - Pedido #{p.id}: origen={p.origen}, estado={p.estado}, fecha={p.fecha.date()}, detalles={p.detalles.count()}")

# 7. Pedidos LOCALES entregados HOY
print(f"\n[7] Pedidos LOCAL + ENTREGADO + HOY (fecha__date={hoy}):")
qs_bodega_hoy = Pedido.objects.filter(fecha__date=hoy, origen='local', estado='entregado')
print(f"    Total: {qs_bodega_hoy.count()}")
for p in qs_bodega_hoy[:5]:
    print(f"    - Pedido #{p.id}: origen={p.origen}, estado={p.estado}, fecha={p.fecha.date()}, detalles={p.detalles.count()}")

# 8. Verificar si los pedidos tienen detalles
print(f"\n[8] Pedidos SIN detalles:")
pedidos_sin_detalles = Pedido.objects.annotate(detalle_count=Count('detalles')).filter(detalle_count=0)
print(f"    Total: {pedidos_sin_detalles.count()}")

# 9. Verificar balones disponibles
print(f"\n[9] Balones ACTIVOS:")
balones = TipoBalon.objects.filter(activo=True).order_by('peso_neto_gas')
print(f"    Total: {balones.count()}")
for b in balones:
    print(f"    - {b.nombre} ({b.peso_neto_gas}kg)")

# 10. Detalles de pedido para cada balón
print(f"\n[10] Detalles de pedidos por balón (LOCAL + ENTREGADO + HOY):")
for balon in balones:
    qty = qs_bodega_hoy.filter(detalles__balon=balon).aggregate(total=Sum('detalles__cantidad'))['total'] or 0
    print(f"    - {balon.nombre}: {qty} unidades")

# 11. Verificar sobres de bodega
print(f"\n[11] Sobres de BODEGA del día:")
sobres_bodega = SobreDiario.objects.filter(fecha__date=hoy, tipo='bodega')
print(f"    Total: {sobres_bodega.count()}")
for s in sobres_bodega:
    print(f"    - Sobre #{s.id}: lineas={s.lineas.count()}, cerrado={s.cerrado}")
    for linea in s.lineas.all():
        print(f"      * {linea.balon.nombre}: calculada={linea.cantidad_calculada}, declarada={linea.cantidad_declarada}")

print("\n" + "="*80 + "\n")

# Importar Count y Sum
from django.db.models import Count, Sum
