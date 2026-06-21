# Reporte de Análisis de Rendimiento — GasFácil / Kim Gas

**Proyecto:** Django 5.x + Bootstrap 5 + MySQL/PostgreSQL  
**Fecha del análisis:** 19 de junio de 2026  
**Última actualización:** 21 de junio de 2026 — Fase 1 cerrada (sobres AJAX 5 min, UI operativa)
**Rama de trabajo actual:** `refactor/cambios-criticos-validacion` (desde `develop-new`)  
**Alcance:** Revisión estática + registro de cambios aplicados en código  
**Objetivo:** Identificar problemas potenciales de rendimiento y proponer medidas futuras

**Leyenda de estado:** ✅ Implementado · 🔶 Parcial · ⏳ Pendiente

---

## Registro de cambios aplicados (19-jun-2026)

Optimizaciones de **bajo riesgo** implementadas en la rama `develop-new`.  
**Pendiente en despliegue:** ejecutar `python manage.py migrate` (migración `0030_performance_indexes`).

---

## Registro de cambios aplicados (21-jun-2026, mañana)

Rama `refactor/cambios-criticos-validacion` — commits `ca8c553`, `6acd524` (previos).

| Archivo(s) | Cambio |
|------------|--------|
| `mockups/tests_logica_negocio.py` | **9 tests** baseline: totales, kilos camionero, sync sobres, fechas Chile/UTC |
| `mockups/models.py` | Eliminada clase `LineaSobre` duplicada (M4) |
| `mockups/views.py`, `admin.py`, `forms.py`, `settings.py` | Limpieza cosmética (docstrings, comentarios obsoletos) |

## Registro de cambios aplicados (21-jun-2026, tarde)

Commit `bd63100` — sync AJAX de sobres operativos.

| Archivo(s) | Cambio |
|------------|--------|
| `mockups/views.py` | GET sin sync salvo sobre vacío; POST borrador guarda declarada y luego sync solo **calculada**; `refrescar_sobre_diario` devuelve `balon_id` |
| `mockups/templates/sobres/sobres.html` | AJAX al cargar, polling **5 min**, botón «Actualizar ahora», `visibilitychange`, fix emparejamiento filas DOM |
| `mockups/tests_logica_negocio.py` | **10 tests** — nuevo: declarada fija tras borrador aunque suba calculada |

**Regla de negocio confirmada:** `cantidad_declarada` la fija el bodeguero al guardar borrador; la sync solo actualiza `cantidad_calculada` en líneas existentes.

## Registro de cambios aplicados (21-jun-2026, cierre Fase 1)

Ajuste de producción en `sobres.html` — mismo commit que este informe (`chore(sobres): UI producción 5 min + informe rendimiento Fase 1`).

| Cambio | Detalle |
|--------|---------|
| Intervalo polling | `SOBRE_SYNC_INTERVAL_MS = 300000` (5 minutos) |
| Mensajes UI | Texto claro para bodeguero; sin etiquetas «debug» ni contadores técnicos (`8/8 líneas`) |
| Consola navegador | Eliminado `console.info('[SobreSync]')` |

**Hallazgo pendiente (C5):** `SobreDiario.calcular_desde_pedidos()` sigue roto en bodega; la sync real usa `get_pedidos_queryset_para_sobre`.

---

## Resumen ejecutivo

El proyecto ya incorpora varias buenas prácticas (uso de `select_related` / `prefetch_related` en vistas críticas, paginación en consultas de pedidos y auditoría, `conn_max_age=600` en producción, WhiteNoise con compresión). Sin embargo, hay cuellos de botella claros que empeorarán conforme crezca el volumen de pedidos, sobres y registros de auditoría.

Los tres riesgos más relevantes hoy son:

1. **Carga continua por polling AJAX** en pantallas operativas (camioneros y telefonistas), que genera tráfico y consultas aunque no haya cambios.
2. **Reportes mensuales que cargan todo el mes en memoria** y ejecutan agregaciones en Python en lugar de en la base de datos.
3. **Consultas N+1 y filtros costosos** (`icontains`, `distinct`, `.count()` repetidos) en vistas de reportes, sobres y búsqueda avanzada.

No se detectó un sistema de caché (`CACHES`) ni tareas asíncronas formales (Celery/RQ). La auditoría y el logging escriben de forma síncrona en cada acción relevante.

---

## Metodología

- Revisión de `mockups/views.py`, `mockups/models.py`, `mockups/forms.py`, `mockups/admin.py`, `gasmanager/settings.py`
- Revisión de plantillas Bootstrap y JavaScript (`base.html`, parciales AJAX, reportes, sobres)
- Búsqueda de patrones: `select_related`, `prefetch_related`, bucles con `.all()`, polling, índices, caché
- **No se ejecutaron pruebas de carga ni profiling en runtime** (recomendado como siguiente paso)

---

## Hallazgos por severidad

### 🔴 Crítico — Impacto alto con crecimiento de datos o usuarios concurrentes

| # | Estado | Área | Ubicación | Problema | Medida recomendada |
|---|--------|------|-----------|----------|-------------------|
| C1 | 🔶 Parcial | Frontend + Backend | Entregas/pedidos (45 s), `sobres.html` (5 min) | **Polling periódico** que golpea el servidor aunque no haya cambios. | ✅ Hecho: 45 s en entregas/pedidos con comparación de contadores. ✅ Sobres: AJAX al cargar + botón manual + polling **5 min** + pausa si pestaña oculta. ⏳ WebSockets/SSE como reemplazo definitivo a largo plazo. |
| C2 | ⏳ Pendiente | Vistas | `reporte_ventas` (`views.py` ~2445–2526) | Itera **todos los pedidos del mes** en Python para rendimiento por trabajador y ventas por día. Además, por cada trabajador ejecuta **2 consultas `count()`** adicionales (`registrador_id` y `entregador_id`) → patrón N+1. | Mover agregaciones a SQL con `values().annotate()` o `Case/When`. Precalcular métricas diarias en tabla materializada o job nocturno. |
| C3 | ⏳ Pendiente | Vistas | `reporte_sobres` (`views.py` ~2701–2894) | Aunque usa `prefetch_related`, las funciones `calcular_metricas` y la tabla final llaman `s.lineas.all()`, `s.gastos.all()`, `s.pagos.all()` en bucles. Con muchos sobres del mes, el prefetch ayuda pero sigue siendo costoso en CPU. Además, `Usuario.objects.get(id=clave)` dentro de bucles. | Agregar con `annotate(Sum(...))` a nivel de queryset. Precargar usuarios con un diccionario `{id: usuario}` en una sola consulta. |
| C4 | ⏳ Pendiente | Vistas | `consultas_pedidos` (`views.py` ~2142–2153) | Búsqueda con múltiples `icontains` sobre FKs + join a `detalles__balon__nombre` + `.distinct()`. En tablas grandes, esto fuerza scans y joins pesados. | Limitar campos buscables, usar índice full-text (MySQL FULLTEXT / PostgreSQL `tsvector`), o motor dedicado (Elasticsearch/Meilisearch) si la búsqueda es frecuente. |
| C5 | ⏳ Pendiente | Modelos | `SobreDiario.calcular_desde_pedidos` (`models.py` ~460–477) | Usa `fecha__date=...` (no usa índice en `fecha`) y filtro bodega incorrecto (`registrador=self.trabajador` con `trabajador=None` → siempre 0). Tests lo documentan en `tests_logica_negocio.py`. | Reutilizar lógica de `get_pedidos_queryset_para_sobre`; actualizar test que hoy espera el bug. **Próximo paso técnico recomendado.** |

---

### 🟠 Alto — Degradación notable en uso diario o reportes

| # | Estado | Área | Ubicación | Problema | Medida recomendada |
|---|--------|------|-----------|----------|-------------------|
| A1 | ✅ Optimizado | Vistas | `editar_sobre_diario`, `refrescar_sobre_diario`, `sobres.html` | Sync de sobres en cada apertura era costosa pero necesaria para cierre matutino. | **Hecho (bd63100):** GET sync solo si sobre sin líneas; **AJAX al cargar** + polling + botón manual cubren apertura matutina y pestaña abierta; POST borrador preserva declarada. Costo GET reducido en sobres ya armados. |
| A2 | ⏳ Pendiente | Vistas | `historial_sobres` modo `todo` (`views.py` ~3562–3600) | Carga **todos los sobres históricos** sin paginación ni límite temporal. | Paginar (50–100 por página) y limitar rango por defecto (ej. últimos 90 días). |
| A3 | ✅ Implementado | Base de datos | `SobreDiario`, `Pedido.sector` | Faltaban índices en filtros frecuentes de reportes y sobres. | **Hecho:** migración `0030_performance_indexes` — índices en `Pedido.sector`, `SobreDiario.fecha_correspondiente`, compuesto `(fecha_correspondiente, tipo, cerrado)` y `(trabajador, fecha_correspondiente)`. Ejecutar `migrate` en cada entorno. |
| A4 | 🔶 Parcial | Vistas | `exportar_pedidos_excel` (`views.py`) | Evaluaba el queryset **dos veces** (filas + totales). | **Hecho:** un solo recorrido con acumulación de totales y reutilización de `detalles` prefetched. ⏳ Pendiente: `iterator(chunk_size=500)` para exports muy grandes. |
| A5 | ✅ Implementado | Frontend | `mis_pedidos_hoy.html`, `mis_entregas_camionero.html` | Siempre reemplazaban el HTML aunque no hubiera cambios. | **Hecho:** comparación de `count` (y `total_monto_hoy` en pedidos) antes de actualizar el DOM. |
| A6 | ✅ Implementado | Service Worker | `mockups/static/sw.js` | Cacheaba toda respuesta GET 200 del origen. | **Hecho:** cache limitado a `/static/`; rutas dinámicas excluidas; `CACHE_NAME` actualizado a `gasfacil-v4`. |
| A7 | ⏳ Pendiente | Infraestructura | `settings.py` | **No hay configuración `CACHES`**. Catálogos estables (balones, sectores, choices) se consultan en cada request de formulario. | Redis o Memcached para: tipos de balón, sectores, contadores ligeros de dashboard. TTL 5–15 min. |
| A8 | ⏳ Pendiente | Auditoría | `AuditoriaAccion.registrar` + `LOGGING` a archivos | Cada login, pedido, permiso denegado, etc. hace **INSERT síncrono** + escritura a disco (`audit.log`, `security.log`). La tabla crecerá indefinidamente. | Política de retención (archivar > 6 meses), logging asíncrono, o cola de escritura. Índices ya existen, pero el volumen seguirá creciendo. |

---

### 🟡 Medio — Optimizaciones recomendadas

| # | Estado | Área | Ubicación | Problema | Medida recomendada |
|---|--------|------|-----------|----------|-------------------|
| M1 | ⏳ Pendiente | Modelos | `Pedido.calcular_totales` (`models.py` ~307–312) | Carga todos los detalles en Python y hace `save()` adicional. Se invoca tras crear/editar pedidos. | `aggregate(Sum(...))` en una consulta; evitar segundo `save` si los valores no cambian. |
| M2 | ⏳ Pendiente | Modelos | `SobreDiario.save` (`models.py` ~477–497) | Puede ejecutar **hasta 3 saves** por operación (crear, recalcular líneas, actualizar montos). | Calcular montos antes del save principal o usar señales con `update_fields` controlado. |
| M3 | ⏳ Pendiente | Modelos | `Sector.save` (`models.py` ~188–199) | Bucle `while ... filter(codigo=...).exists()` con consultas repetidas al generar slug único. | Generar código con sufijo UUID corto o usar `get_or_create` con retry acotado. |
| M4 | ✅ Implementado | Modelos | `LineaSobre` duplicado (`models.py`) | La clase `LineaSobre` estaba **definida dos veces** en el mismo archivo. | **Hecho (21-jun-2026):** eliminado bloque duplicado; se conservó la versión con campo `nota`. |
| M5 | ⏳ Pendiente | Formularios | `DetallePedidoForm.__init__` (`forms.py` ~218–228) | Consulta y anota **todos los balones activos** por cada formulario del formset (varias veces por página). | Cachear choices de balones por request o por sesión. Pasar queryset precargado desde la vista. |
| M6 | ⏳ Pendiente | Formularios | `get_sector_choices` (`forms.py` ~12–46) | `Sector.objects.all()` en cada render del formulario de pedido. | Cachear en memoria (per-request) o Redis con invalidación al editar sectores. |
| M7 | ✅ Implementado | Vistas | `camionero_entregas` (`views.py`) | Ejecutaba `.count()` y `.exists()` repetidos (vista + plantilla + API). | **Hecho:** conteos calculados una vez; plantilla parcial usa `count_en_ruta` / `count_pendientes`. |
| M8 | ✅ Implementado | Vistas | `consultas_pedidos` estadísticas (`views.py`) | Tres consultas separadas: `count()`, `Sum(monto_total)`, `Sum(ganancia_total)`. | **Hecho:** una sola llamada `aggregate(Count, Sum, Sum)`. |
| M9 | ✅ Implementado | Vistas | `auditoria_lista` (`views.py`) | `registros.count()` duplicado además de la paginación. | **Hecho:** usa `paginator.count`. |
| M10 | ⏳ Pendiente | Push | `push_notifications.py` (~214–230) | Envío **secuencial** de notificaciones HTTP a cada suscripción. Con muchos camioneros/dispositivos, demora el hilo background. | Cola de tareas (Celery) + envío paralelo con límite de concurrencia. |
| M11 | ⏳ Pendiente | Push | `views.py` (~1316–1332) | `threading.Thread` para notificaciones en lugar de worker dedicado. Bajo carga, muchos hilos pueden competir con Gunicorn. | Migrar a Celery/RQ/Django-Q. |
| M12 | ✅ Implementado | Admin Django | `PedidoAdmin` (`admin.py`) | Propiedad `resumen_productos` → **N+1** en listado del admin. | **Hecho:** `get_queryset` con `prefetch_related('detalles__balon')`. |
| M13 | ✅ Implementado | Admin Django | `SobreDiarioAdmin` (`admin.py`) | `aggregate` por cada fila del listado admin. | **Hecho:** `annotate` en `get_queryset` para totales en listado. |
| M14 | ✅ Implementado | Frontend | `base.html`, `consultas_pedidos.html` | Flatpickr cargado globalmente y duplicado. | **Hecho:** removido de `base.html`; solo en `consultas_pedidos.html` con `defer`. |
| M15 | ✅ Implementado | Frontend | `reporte_ventas.html`, `reporte_sobres.html` | Chart.js sin `defer` bloqueaba parsing. | **Hecho:** `defer` en script + inicialización dentro de `DOMContentLoaded`. |
| M16 | ⏳ Pendiente | Frontend | `base.html` | Bootstrap 5 e Icons desde **CDN externo** (jsdelivr). Latencia adicional y dependencia de terceros. | Servir assets versionados desde `/static/` con WhiteNoise también en staging; mantener CDN solo como fallback. |
| M17 | ⏳ Pendiente | Seguridad | `django-axes` middleware | Consulta BD en intentos de login para bloqueo por fuerza bruta. Impacto bajo pero constante en `/auth/login/`. | Aceptable por seguridad; monitorear tamaño de tablas de axes y configurar limpieza. |

---

### 🟢 Bajo — Mejoras menores o preventivas

| # | Área | Ubicación | Problema | Medida recomendada |
|---|------|-----------|----------|-------------------|
| B1 | Settings | `SESSION_COOKIE_AGE = 172800` (48 h) | Sesiones largas aumentan filas en tabla de sesiones si se usa backend de BD. | Evaluar `cached_db` o sesiones en Redis. |
| B2 | Settings | Estáticos solo optimizados con `DEBUG=False` | En desarrollo, Django sirve estáticos sin compresión/manifest. | No crítico; solo afecta entorno local. |
| B3 | Templates | `pedido.resumen_productos` en listados | Propiedad con caché por instancia (`_resumen_cache`), pero si no hay prefetch de detalles genera N+1. | Verificar prefetch en todas las vistas que muestran listados (la mayoría ya lo tiene). |
| B4 | Vistas | `require_roles` + `AuditoriaAccion.registrar` en accesos denegados | INSERT de auditoría incluso en intentos no autorizados (puede ser abusado). | Rate-limit o registrar solo en patrones sospechosos repetidos. |
| B5 | Índices | `HistorialEstadoPedido`, `HistorialCambioPedido` | Sin índices explícitos en `pedido_id` / `fecha` (FK suele indexar `pedido_id`). | Índice en `fecha_cambio` si se consulta historial con frecuencia. |

---

## Lo que ya está bien implementado

Estas prácticas **no requieren cambio inmediato** y deben mantenerse:

- `select_related('registrador', 'entregador')` + `prefetch_related('detalles__balon')` en vistas operativas de pedidos y entregas
- Paginación de 10 ítems en `consultas_pedidos` y 50 en `auditoria_lista`
- Rangos datetime con zona Chile (`get_rango_utc_para_fecha`, `get_pedidos_queryset_para_sobre`) en lógica de sobres
- Índices en `Pedido` (`fecha`, `estado`, `origen`, `registrador`, `entregador`, **`sector`** ✅) y `AuditoriaAccion`
- Índices en `SobreDiario` (`fecha_correspondiente`, compuestos con `tipo`/`cerrado`/`trabajador`) ✅
- WhiteNoise con `CompressedManifestStaticFilesStorage` en producción
- `conn_max_age=600` para reutilizar conexiones DB en PaaS
- Polling pausado cuando la pestaña no está visible (`visibilitychange`)
- Notificaciones push para reducir dependencia del polling en camioneros (parcialmente)
- **Sync híbrida de sobres** — AJAX al cargar + polling conservador (ver A1)
- **`cantidad_declarada` fija tras borrador** — solo el usuario la cambia al guardar

---

## Decisiones de diseño aceptadas (validación operativa)

### Sync de sobres (híbrida GET + AJAX)

**Contexto:** El bodeguero abre sobres a primera hora; durante el día siguen llegando entregas de camioneros.

| Momento | Qué pasa | Qué se actualiza |
|---------|----------|------------------|
| GET, sobre **sin líneas** | Sync server-side una vez | Crea líneas iniciales |
| GET, sobre **con líneas** | Sin sync server-side | HTML con último estado guardado |
| **AJAX al cargar** | `refrescar_sobre_diario` | Solo columna **Calculada (app)** |
| **Polling** (5 min prod) | Mismo endpoint | Calculada, con pestaña visible |
| **Guardar borrador** | Formset primero, luego sync | Calculada en BD; **declarada = lo que escribió el usuario** |
| **Cerrar sobre** | Sync antes de cerrar | Calculada al día |

**Regla:** La sync **nunca** modifica `cantidad_declarada` en líneas existentes (solo al crear línea nueva: declarada = calculada inicial).

---

## Plan de acción recomendado (priorizado)

### Fase 1 — Quick wins (1–2 semanas, bajo riesgo)

1. ✅ **Reducir polling** en entregas/pedidos (45 s + comparación DOM) y sobres (5 min + AJAX al cargar).
2. ✅ **Sync sobres optimizada** — GET liviano + AJAX + declarada fija tras borrador (`bd63100`).
3. ✅ **UI sobres** — mensajes operativos sin jerga técnica ni modo debug.
4. ✅ **Unificar consultas** en `consultas_pedidos` y conteos en `camionero_entregas`.
5. ✅ **Flatpickr / Chart.js** solo donde se usan.
6. ✅ **Service Worker** limitado a `/static/` (`sw.js` v4).
7. ✅ **Tests de lógica** (`tests_logica_negocio.py`, 10 tests).

### Fase 2 — Base de datos (2–4 semanas)

1. ✅ **Crear índices** en `SobreDiario` y `Pedido.sector` — migración `0030` creada; ⏳ falta `python manage.py migrate` en cada entorno.
2. ⏳ Reemplazar `fecha__date` por rangos UTC en `SobreDiario.calcular_desde_pedidos`.
3. ⏳ Refactorizar `reporte_ventas` y `reporte_sobres` para agregaciones 100% SQL.
4. ⏳ Política de retención/archivo para `AuditoriaAccion`.

### Fase 3 — Arquitectura (1–2 meses)

1. Introducir **Redis** para caché de catálogos y sesiones.
2. **Celery** (o similar) para push notifications, exports Excel y reportes pesados.
3. Evaluar **SSE/WebSockets** para actualizaciones en tiempo real en lugar de polling.
4. Tabla de **métricas diarias precalculadas** para dashboard de ventas.

---

## Cómo validar mejoras (siguiente paso técnico)

Antes y después de cada cambio, medir con:

```bash
# Activar logging de consultas SQL en desarrollo (settings local temporal)
LOGGING['loggers']['django.db.backends'] = {'level': 'DEBUG', 'handlers': ['console']}

# O usar django-debug-toolbar / silk en entorno de staging
pip install django-silk
```

Métricas clave a observar:

| Métrica | Herramienta sugerida |
|---------|---------------------|
| Consultas SQL por request | Django Silk, debug toolbar |
| Tiempo de respuesta p95 | Logs Gunicorn, Render metrics, Sentry |
| Tamaño de respuesta AJAX | DevTools → Network |
| Memoria en export Excel | `memory_profiler` o límites del worker |
| Consultas lentas en MySQL | `slow_query_log` |

**Prueba de carga sugerida:** simular 5 camioneros con polling activo + 2 telefonistas + 1 jefe abriendo reporte mensual. Comparar QPS y latencia antes/después.

---

## Matriz de prioridad vs esfuerzo

```
Impacto alto + Esfuerzo bajo  → Fase 1 (polling, sync sobres, assets)
Impacto alto + Esfuerzo medio → Fase 2 (índices, reportes SQL)
Impacto alto + Esfuerzo alto  → Fase 3 (Redis, Celery, WebSockets)
```

---

## Próximo paso recomendado (21-jun-2026)

**Fase 1 cerrada.** El trabajo inmediato pasa a despliegue operativo y Fase 2.

### Despliegue / ops (rápido)

| # | Tarea | Notas |
|---|-------|-------|
| D1 | `python manage.py migrate` (índices `0030`) | Staging y producción |
| D2 | Commit + merge `refactor/cambios-criticos-validacion` → `develop-new` | Incluye sobres AJAX + UI limpia |

### Fase 2 — prioridad técnica

| # | Ítem | Impacto | Esfuerzo |
|---|------|---------|----------|
| 1 | **C5** — `calcular_desde_pedidos` alineado con `get_pedidos_queryset_para_sobre` | Corrección de datos al crear sobres | Medio |
| 2 | **C2 / C3** — reportes mensuales a SQL | Alto con muchos pedidos/sobres | Medio–alto |
| 3 | **A2** — paginar `historial_sobres` modo `todo` | Evita cargar todo el historial | Bajo–medio |
| 4 | **M5 / M6** — cache per-request de balones y sectores en formularios | Menos consultas repetidas | Bajo |

---

## Conclusión

El sistema es funcional y optimizado en el flujo operativo diario. **Fase 1 cerrada:** polling sobres a 5 min, sync AJAX, declarada fija tras borrador, UI sin mensajes de debug, tests de lógica (10), deuda M4 resuelta.

Cuellos de botella a medio plazo: **reportes mensuales (C2, C3)**, **búsqueda `icontains` (C4)**, **caché Redis (A7)**. Pendiente operativo: **migrate 0030** en entornos desplegados.

**Resumen de estado global:**

| Categoría | Implementados | Parciales | Pendientes |
|-----------|---------------|-----------|------------|
| Críticos (C1–C5) | 0 | 1 (C1) | 4 |
| Altos (A1–A8) | 5 (incl. A1 optimizado) | 1 (A4) | 2 |
| Medios (M1–M17) | 8 | 0 | 9 |
| **Fase 1 (plan)** | **7/7 ítems** | — | — |
| **Total accionable** | **13** | **2** | **15** |

Este documento sirve como guía viva: actualizar la columna **Estado** al implementar cada ítem.

---

*Generado por análisis estático del repositorio `gas_project`.*  
*Registro de implementación: 19 y 21 de junio de 2026 — rama `refactor/cambios-criticos-validacion` (commits `ca8c553`, `bd63100`, cierre Fase 1).*