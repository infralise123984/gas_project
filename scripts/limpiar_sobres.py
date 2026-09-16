#!/usr/bin/env python
"""
Script para limpiar todos los sobres de la base de datos
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gasmanager.settings')
django.setup()

from mockups.models import SobreDiario, LineaSobre, LineaPago, LineaGasto

# Contar registros antes
sobres_count = SobreDiario.objects.count()
lineas_count = LineaSobre.objects.count()
pagos_count = LineaPago.objects.count()
gastos_count = LineaGasto.objects.count()

print(f"Antes de limpiar:")
print(f"  - Sobres: {sobres_count}")
print(f"  - Líneas de sobre: {lineas_count}")
print(f"  - Líneas de pago: {pagos_count}")
print(f"  - Líneas de gasto: {gastos_count}")

# Confirmar
confirmar = input("\n¿Seguro que quieres eliminar TODO? (escribe 'sí' para confirmar): ").strip().lower()

if confirmar == 'sí':
    # Eliminar en cascada (Django se encarga de las relaciones)
    SobreDiario.objects.all().delete()
    
    print("\n✅ Todos los sobres han sido eliminados correctamente.")
    print(f"   Se eliminaron {sobres_count} sobres")
else:
    print("\n❌ Operación cancelada.")
