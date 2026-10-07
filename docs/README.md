# Documentación del proyecto

Índice de la documentación técnica. El `README.md` de la raíz queda como punto de entrada (instalación, ejecución y despliegue); acá vive el detalle.

| Documento | De qué trata | Cuándo leerlo |
|---|---|---|
| [`PUSH_NOTIFICATIONS.md`](PUSH_NOTIFICATIONS.md) | Web Push con VAPID: suscripciones, endpoints, service worker, diagnóstico | Cuando alguien diga "no me llegan las notificaciones" |
| [`SECURITY_TESTING.md`](SECURITY_TESTING.md) | Checklist de seguridad, comandos de verificación, vulnerabilidades revisadas | Antes de un deploy a producción |
| [`REPORTE_RENDIMIENTO.md`](REPORTE_RENDIMIENTO.md) | Análisis de rendimiento con hallazgos y mejoras priorizadas (M1, M2, …) | Antes de optimizar o cuando la app se sienta lenta |
| [`CIERRE_AUTOMATICO_SOBRES.md`](CIERRE_AUTOMATICO_SOBRES.md) | Diseño y reglas del cierre automático nocturno de sobres | Al tocar sobres, cierres o cuadratura de dinero |
| [`PLAN_AUDITORIA_OPERATIVA.md`](PLAN_AUDITORIA_OPERATIVA.md) | **Plan de trabajo pendiente**: telemetría de cliente, evidencia de notificaciones, vista forense por pedido y alertas de cancelación | Antes de implementar auditoría operativa; contiene la sección de restricciones (qué no tocar) |
| [`API_MOVIL.md`](API_MOVIL.md) | **Contrato de la API JSON** para la app Flutter del camionero: rutas, formato de respuestas, autenticación por sesión, idempotencia, permisos y qué se reutiliza | Antes de escribir cualquier endpoint de `/api/v1/`. Contiene las restricciones heredadas y el orden de trabajo entre los dos repos |
| [`CLEAN-V2.txt`](CLEAN-V2.txt) | Nota histórica de limpieza de `__pycache__`. Sin contenido relevante | Candidato a eliminar |

---

## Nomenclatura de archivos

Estos documentos eran originalmente los únicos `.md` del proyecto y usaban nombres en mayúsculas con guion bajo (`PUSH_NOTIFICATIONS.md`), en lugar de `kebab-case`. **Se mantienen esos nombres a propósito**: aparecen referenciados en el historial de git, en conversaciones previas y en las memorias del asistente, así que renombrarlos rompe referencias sin aportar valor.

Al crear documentos nuevos, usar el mismo formato (`MAYUSCULAS_CON_GUION_BAJO.md`).
