# 📋 Cierre Automático de Sobres - Guía de Configuración

## Cómo Funciona

### 1. **Alerta Visual de "Hora Recomendada"** 🕐
Después de las **20:00 (8 PM)**, el sistema muestra una alerta visual en el sobre:
```
⏰ Hora recomendada para cerrar el sobre
20:45 - Habitualmente se cierra después de las 20:00...
```

Esto es un **recordatorio**, pero el cierre sigue siendo **manual**.

---

### 2. **Cierre Automático Nocturno** 🤖
El sistema puede cerrar automáticamente sobres abiertos de días anteriores que:
- ✅ Tengan **al menos 1 pedido** (línea con cantidad > 0)
- ✅ Estén **abiertos** (cerrado = False)
- ✅ Sean de **fecha anterior** (por defecto: ayer)

---

## Cómo Ejecutar el Cierre Automático

### **Opción 1: Manual (Para Probar)**
```bash
python manage.py cerrar_sobres_automatico
```

**Con opciones:**
```bash
# Cerrar sobres de 2 días atrás
python manage.py cerrar_sobres_automatico --dias-atras 2

# Cerrar sobres de 1 día atrás (default)
python manage.py cerrar_sobres_automatico --dias-atras 1
```

**Salida esperada:**
```
================================================================================
CIERRE AUTOMÁTICO DE SOBRES - 22/02/2026
================================================================================

Sobres abiertos encontrados: 2

✅ Sobre #3 (Juan - Camionero) - CERRADO
   - Líneas: 8
   - Monto: $150,000
   - Cierre: 22/02/2026 23:00:00

✅ Sobre #2 (Bodega) - CERRADO
   - Líneas: 5
   - Monto: $89,500
   - Cierre: 22/02/2026 23:00:00

================================================================================
RESUMEN DEL CIERRE AUTOMÁTICO:
================================================================================
✅ Cerrados:  2
⏭️  Saltados: 0
================================================================================
```

---

### **Opción 2: Cierre Automático Programado (RECOMENDADO)**

#### **En Linux/Mac (usando Cron)**

1. Abre el editor de cron:
```bash
crontab -e
```

2. Agrega esta línea (cierra a las 23:30 cada noche):
```bash
30 23 * * * cd /ruta/al/proyecto && python manage.py cerrar_sobres_automatico
```

3. Explica cada parte:
   - `30 23` = 23:30 (11:30 PM)
   - `* * *` = Todos los días del mes, mes y día de semana
   - `cd /ruta/al/proyecto` = Navegar al directorio del proyecto
   - `python manage.py cerrar_sobres_automatico` = Ejecutar el comando

#### **En Windows (usando Tareas Programadas)**

1. Abre "Tareas Programadas" (Task Scheduler)

2. Crea una tarea nueva:
   - **Nombre:** Cierre Automático KIM GAS
   - **Trigger:** Diario a las 23:30
   - **Acción:** Ejecutar programa
     - Programa: `python.exe`
     - Argumentos: `manage.py cerrar_sobres_automatico`
     - Carpeta inicial: `c:\ruta\al\proyecto`

---

### **Opción 3: Con Celery Beat (Si usas Celery)**

#### Agregar a `settings.py`:
```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'cerrar_sobres_automatico': {
        'task': 'mockups.tasks.cerrar_sobres_tarea',
        'schedule': crontab(hour=23, minute=30),  # 23:30 cada noche
    },
}
```

#### Crear `mockups/tasks.py`:
```python
from celery import shared_task
from mockups.management.commands.cerrar_sobres_automatico import Command

@shared_task
def cerrar_sobres_tarea():
    cmd = Command()
    cmd.handle(dias_atras=1)
    return "Sobres cerrados automáticamente"
```

---

## Comportamiento del Cierre Automático

### **¿Qué se cierra?**
- Sobres abiertos de **ayer** (--dias-atras 1)
- Que tengan **al menos 1 línea con cantidad > 0**
- De cualquier trabajador (bodega o camionero)

### **¿Qué NO se cierra?**
- Sobres que **ya están cerrados**
- Sobres **sin pedidos** (todas las líneas en 0)
- Sobres de **hoy** (para que el usuario pueda editar)

### **Campos que se actualizan:**
```python
sobre.cerrado = True
sobre.declarado_el = timezone.now()  # Timestamp actual
sobre.nota_cierre = "Cierre automático a las HH:MM:SS"
```

---

## Auditoría y Logs

Cada cierre automático se registra en:
- **Base de datos:** `SobreDiario.declarado_el` y `SobreDiario.nota_cierre`
- **Terminal:** Consola muestra detalles de cada sobre cerrado
- **Logs:** Puedes capturar stdout a un archivo

### **Capturar salida en archivo:**
```bash
python manage.py cerrar_sobres_automatico >> /var/log/kim_gas_cerres.log 2>&1
```

---

## Recomendaciones de Uso

1. **Hora sugerida para cierre:** 23:30 (11:30 PM)
   - Permite que se agreguen los últimos pedidos del día
   - Deja tiempo para que el usuario cierre manualmente si lo desea

2. **En caso de urgencia:** El usuario puede cerrar manualmente en cualquier momento con el botón "Cerrar sobre"

3. **Reapertura:** Si un sobre se cerró y llega un pedido nuevo, tendrías que reabrirlo manualmente desde la BD o agregar una funcionalidad en la interfaz

4. **Auditoría:** Verifica que el usuario del sistema (`sistema_automatico`) se cree correctamente en la BD

---

## Troubleshooting

### Error: `ModuleNotFoundError`
```
Asegúrate de que estás en el directorio correcto:
cd c:\Users\tmuru\Escritorio\github\gas_project
```

### Error: `No attribute 'make_aware'`
```
Verifica que tengas importados:
from django.utils import timezone
from zoneinfo import ZoneInfo
```

### No cierra ningún sobre
```
Ejecuta:
python manage.py shell
>>> from mockups.models import SobreDiario
>>> SobreDiario.objects.filter(cerrado=False)
Verifica que existan sobres abiertos y que tengan líneas con cantidad.
```

---

## Próximas Mejoras (Opcional)

- [ ] Enviar notificación SMS/Email cuando se cierra automáticamente
- [ ] Opción de "reapertura" si llega un pedido después del cierre
- [ ] Dashboard mostrando sobres cerrados automáticamente vs manualmente
- [ ] Excepciones (días festivos, domingos sin trabajo, etc.)
