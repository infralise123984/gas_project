# Plan de trabajo — Auditoría Operativa y Alertas de Cancelación

> **Documento de traspaso.** Está escrito para que otra persona pueda implementarlo sin contexto previo.
> Última actualización: 2026-09-15.

---

## 1. Objetivo

Hoy la auditoría es **solo del lado del servidor**: `AuditoriaAccion` registra qué usuario hizo qué acción, con IP y User-Agent. Eso alcanza para saber *quién*, pero no alcanza para responder reclamos del tipo:

- *"La app no me dejaba apretar el botón"*
- *"No me llegó la notificación"*
- *"La app se volvió loca y canceló los pedidos"*

**Caso real que motivó este trabajo (15/09/2026):** un camionero anuló 3 pedidos en 55 segundos (`#4101` a las 19:05:37, `#4100` a las 19:06:20, `#4099` a las 19:06:32), todos desde la misma IP y cuenta, y alegó que fue un "error" de la aplicación. La evidencia de servidor demostró que fueron 6 toques deliberados (cada anulación exige abrir el modal + confirmar), pero **no existía evidencia del lado del teléfono** para probar qué vio el usuario en pantalla, y **nadie fue avisado** cuando ocurrió.

**Resultado esperado de este plan:**

1. Evidencia de cliente: qué tocó, cuándo, si la app estaba abierta/visible, si tenía red, y si la notificación push **llegó, se mostró, se clickeó o se cerró**.
2. Vista forense: una línea de tiempo por pedido que une datos de servidor + cliente.
3. Alertas: el telefonista (y jefe/admin) recibe push + campanita al instante cuando un camionero anula una entrega, más una alerta si hay patrón (≥2 en 10 min).
4. Quitar las excusas que hoy **sí** son reales (modal que no se cierra, polling que reemplaza el DOM).

---

## 2. Reglas del repo (leer antes de escribir código)

| Tema | Cómo es en este proyecto |
|---|---|
| Framework | Django **5.1.3** (ver `requirements.txt`), servidor `gunicorn`, estáticos con WhiteNoise |
| Apps | Proyecto `gasmanager/`, app única `mockups/` |
| Vista de vistas | `mockups/views/` es un **paquete** con `from mockups.views.X import *` en `__init__.py`. `gasmanager/urls.py` importa `from mockups import views` y referencia `views.<funcion>` |
| Estilo de vistas | **Function-Based Views** + decoradores `@login_required`, `@require_POST`, `@never_cache`. No hay CBV ni DRF |
| Permisos | `require_roles(request, [...], redirect_to, mensaje)` para HTML y `require_roles_api(request, [...])` para JSON. **Ambas auditan `PERM_DENIED` automáticamente**. Usar siempre estas, no `if user.rol == ...` |
| Auditoría | `AuditoriaAccion.registrar(request=..., tipo=..., descripcion=..., objeto=..., datos_anteriores=..., datos_nuevos=...)` — guarda IP y User-Agent del request |
| Logs | Loggers `audit` (INFO) y `security` (WARNING), configurados en `gasmanager/settings.py`. Patrón: `audit_logger.info("EVENTO | dato | dato")` y `security_logger.warning("EVENTO_FAIL | ...")` |
| Tests | `django.test.TestCase` (ver `mockups/tests/pedidos/test_logica_negocio.py`). Comando: `python manage.py test <ruta> -v 2` |
| Timezone | `America/Santiago`. Para fechas usar helpers de `mockups/utils/fechas.py` (`today_chile`, `now_chile`, `rango_dia_chile`) — **no** `datetime.now()` a secas |
| Base de datos | Local SQLite; producción según `DB_ENGINE` en variables de entorno. **Las migraciones deben ser compatibles con MySQL y PostgreSQL**: nada de campos específicos de un motor |
| Migraciones | Última aplicada: `0031_rename_mockups_ped_sector_...`. **Nunca editar migraciones existentes** |
| Deploy | Render ejecuta `bash build.sh`, que ya incluye `python manage.py migrate` |
| Anti-duplicado | Formularios de pedido/tarreo usan `form_token` guardado en `request.session` |
| Push | `pywebpush` 2.2.0 + VAPID. Envío **directo** dentro del request, envuelto en `try/except` (ver `mockups/views/pedidos.py` líneas ~135-150) |

### Entorno local
`gasmanager/settings.py` **exige** `CSRF_TRUSTED_ORIGINS` desde `.env` (lanza `ValueError` si falta). Crear `.env` con al menos:

```
DEBUG=True
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
ALLOWED_HOSTS=localhost,127.0.0.1
```

Sin `DB_ENGINE` se usa SQLite local. **No versionar el `.env`.**

### Roles del sistema
`telefonista`, `bodeguero`, `camionero`, `jefe`, `admin` (+ `is_superuser`).

---

## 3. Qué NO hacer (restricciones duras)

Cualquier desviación de esta sección debe consultarse antes con el dueño del proyecto.

### 3.1 Archivos que NO se tocan
- `mockups/views_monolith_backup.py` — respaldo histórico del monolito. **No está en uso** (las vistas activas están en `mockups/views/`). No editarlo, no "sincronizarlo", no borrarlo.
- `mockups/migrations/0001_*` … `0031_*` — migraciones ya aplicadas en producción.
- `backups/*.sql` y `logs/*` — datos y logs.
- `graphify-out/` y `mockups/graphify-out/` — artefactos generados por herramienta de análisis; se regeneran solos.
- `.env`, `render.yaml` (salvo que se pida explícitamente), `Procfile`.
- `mockups/tests/` — **no modificar los tests existentes**. Si se necesitan tests nuevos, crear el archivo en el subpaquete correspondiente (p. ej. `mockups/tests/auditoria/`).

### 3.2 Comportamiento que NO se cambia
- **No cambiar la semántica de la cancelación.** `camionero_cancelar_entrega` debe seguir dejando el pedido en `estado='cancelado'` y conservando `entregador` (hay una decisión de negocio validada: el pedido cancelado queda ligado al camionero para trazabilidad). El cambio a "devolver a pendiente" es **otro plan** y requiere decisión del dueño.
- **No agregar automatismos que cambien estados de pedidos.** Nada de cron, señales (`signals`) ni tareas que cancelen o modifiquen pedidos por tiempo.
- **No tocar** autenticación, 2FA, `AXES_*`, `SECRET_KEY`, `VAPID_*`, `CSRF_*`, `ALLOWED_HOSTS` ni las cabeceras de seguridad.
- **No debilitar el CSP.** `mockups/middleware.py` ya trae `connect-src 'self'` y `script-src 'self' 'unsafe-inline'`. La telemetría es **mismo origen**; no agregar dominios externos, ni `*`, ni quitar `frame-ancestors 'none'`.
- **No modificar `notificar_nuevo_pedido`**: debe seguir notificando **solo a camioneros** (`usuario__rol='camionero'`).
- **No cambiar el filtro de pedidos pendientes** de `camionero_entregas` / `camionero_entregas_api` (`estado='pendiente'`, `origen='telefono'`, `entregador__isnull=True`, fecha de hoy).
- **No romper el flujo POST → 302 → GET** de las acciones de entrega.

### 3.3 Datos que NO se registran (privacidad)
- **Prohibido** capturar: texto escrito por el usuario, teclas presionadas, valores de campos de formulario, contenido de inputs, ubicación GPS, cámara, micrófono, contactos, o cualquier dato personal más allá de lo operativo.
- Solo se registra: **tipo de evento**, **página**, **id de pedido**, **id lógico del botón** (`data-telemetria="cancelar"`), **estado del navegador** (visible/oculta, online/offline, permiso de notificaciones) y **tiempos**.
- El campo `datos` (JSON) no puede contener texto libre del usuario ni PII. Antes de cerrar cada tarea, revisar cada payload que se emita.
- Contexto legal (Chile, Ley 19.628 / 21.719): es una herramienta de trazabilidad laboral operativa. **Antes de activar en producción se debe avisar al personal** (ver §8, paso 6).

### 3.4 Dependencias y arquitectura
- **No instalar dependencias nuevas** (nada de Sentry, django-ratelimit, Celery, DRF, etc.). Todo debe resolverse con Django + `pywebpush`, que ya están.
- **No introducir hilos ni async** para el envío de notificaciones: seguir la convención de envío directo con `try/except` de `views/pedidos.py`. Un fallo de push **nunca** debe romper la respuesta al usuario.
- **No crear un segundo endpoint de telemetría** ni duplicar lógica de captura por pantalla: un solo JS (`client-telemetry.js`) y un solo endpoint.
- **No usar caché compartida para throttling** asumiendo que funciona entre workers: hoy no hay Redis/Memcached, cada worker de Gunicorn tiene su propio `LocMemCache`. El throttle de telemetría se hace por sesión (patrón ya usado con `2fa_intentos` en `mockups/views/auth.py`).

### 3.5 Git
- Crear rama nueva: `git checkout -b feature/auditoria-operativa` (partir de la rama de trabajo actual del repo; verificar con `git branch --show-current`).
- **No commitear directamente** a `main`, `develop-new`, `v2-main` ni a la rama de features actual.
- Commits pequeños por fase, mensajes en el estilo del repo (español, prefijo del área: p. ej. `auditoria: agrega modelo EventoCliente`).

---

## 4. Contrato de datos (implementar exactamente esto)

### 4.1 `EventoCliente` (nuevo, en `mockups/models.py`)

```python
class EventoCliente(models.Model):
    """
    Telemetría del lado del cliente (navegador / Service Worker).
    NO reemplaza a AuditoriaAccion: aquí van eventos técnicos de UI y de push,
    no acciones de negocio.
    """
    TIPOS = [
        ('SESSION_START',    'Sesión de app iniciada'),
        ('APP_VISIBLE',      'App pasó a primer plano'),
        ('APP_HIDDEN',       'App pasó a segundo plano'),
        ('APP_HEARTBEAT',    'Latido de app activa'),
        ('UI_TAP',           'Toque en botón accionable'),
        ('UI_MODAL_OPEN',    'Modal de confirmación abierto'),
        ('UI_MODAL_CANCEL',  'Modal de confirmación abortado'),
        ('UI_SUBMIT',        'Formulario enviado desde el cliente'),
        ('UI_BLOCKED',       'Acción bloqueada en el cliente'),
        ('UI_ERROR',         'Error de UI capturado'),
        ('JS_ERROR',         'Excepción JavaScript'),
        ('NET_ONLINE',       'Recuperó conexión'),
        ('NET_OFFLINE',      'Perdió conexión'),
        ('FORM_TOKEN_EXPIRED', 'Formulario rechazado por token vencido'),
        ('PUSH_PERMISSION',  'Estado del permiso de notificaciones'),
        ('PUSH_SUB_STATE',   'Estado de la suscripción push'),
        ('PUSH_RECEIVED',    'Push recibido por el dispositivo'),
        ('PUSH_SHOWN',       'Notificación mostrada al usuario'),
        ('PUSH_CLICK',       'Notificación clickeada'),
        ('PUSH_CLOSE',       'Notificación descartada sin abrir'),
        ('PUSH_SUBCHANGE',   'Suscripción push renovada por el navegador'),
    ]
    ORIGENES = [
        ('web', 'Navegador (pestaña o PWA)'),
        ('sw',  'Service Worker'),
    ]

    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True,
                                verbose_name="Usuario")
    username = models.CharField(max_length=150, blank=True, verbose_name="Username (snapshot)")
    tipo = models.CharField(max_length=32, choices=TIPOS, db_index=True, verbose_name="Tipo de evento")
    origen = models.CharField(max_length=8, choices=ORIGENES, default='web', verbose_name="Origen")
    sesion_id = models.CharField(max_length=64, blank=True, verbose_name="ID de sesión de cliente")
    pagina = models.CharField(max_length=120, blank=True, verbose_name="Ruta de la página")
    pedido_id = models.PositiveIntegerField(null=True, blank=True, db_index=True,
                                           verbose_name="ID de pedido relacionado")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Dirección IP")
    user_agent = models.TextField(blank=True, verbose_name="User Agent")
    datos = models.JSONField(null=True, blank=True, verbose_name="Datos adicionales (JSON)")
    fecha = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Fecha y hora")

    class Meta:
        verbose_name = "Evento de Cliente"
        verbose_name_plural = "Eventos de Cliente"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['usuario', 'fecha']),
            models.Index(fields=['tipo', 'fecha']),
            models.Index(fields=['pedido_id', 'fecha']),
            models.Index(fields=['sesion_id', 'tipo']),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.username or 'Anónimo'} - {self.fecha}"
```

**Nota sobre `pedido_id`:** es `PositiveIntegerField` y **no** `ForeignKey` a propósito. Si se borra un pedido, la evidencia debe sobrevivir.

### 4.2 `AlertaOperativa` (nuevo, en `mockups/models.py`)

```python
class AlertaOperativa(models.Model):
    """Alerta operativa en la campanita de la app (no es push ni correo)."""
    TIPOS = [
        ('CANCELACION_CAMIONERO', 'Entrega cancelada por camionero'),
        ('PATRON_CANCELACIONES',  'Patrón anómalo de cancelaciones'),
    ]

    destinatario = models.ForeignKey(Usuario, on_delete=models.CASCADE,
                                     related_name='alertas_operativas', verbose_name="Destinatario")
    tipo = models.CharField(max_length=32, choices=TIPOS, db_index=True, verbose_name="Tipo")
    pedido_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="ID de pedido")
    mensaje = models.CharField(max_length=255, verbose_name="Mensaje")
    url = models.CharField(max_length=200, blank=True, verbose_name="Enlace")
    leida_el = models.DateTimeField(null=True, blank=True, verbose_name="Leída el")
    fecha = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Fecha y hora")

    class Meta:
        verbose_name = "Alerta Operativa"
        verbose_name_plural = "Alertas Operativas"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['destinatario', 'leida_el', 'fecha']),
        ]
```

### 4.3 Cambios sin migración en `AuditoriaAccion.TIPOS`
Agregar dos entradas a la lista `TIPOS` de `AuditoriaAccion` (es solo `choices`, no requiere migración):
- `('PEDIDO_CANCEL', 'Pedido cancelado')` ← hoy se registra en BD pero sin etiqueta en el admin
- `('PUSH_ALERT', 'Alerta push enviada')`

### 4.4 Migraciones
Dos migraciones nuevas: `0032_eventocliente` y `0033_alertaoperativa` (el número exacto lo asigna `makemigrations`; la última existente es `0031`). Si se decide hacer una sola, está bien, pero **no mezclar con cambios de otras tablas**.

---

## 5. Fases de implementación

### Fase 0 — Base de datos y panel (sin riesgo para el usuario final)
- [ ] Agregar `EventoCliente` y `AlertaOperativa` a `mockups/models.py` (§4.1, §4.2).
- [ ] Agregar `PEDIDO_CANCEL` y `PUSH_ALERT` a `AuditoriaAccion.TIPOS`.
- [ ] `python manage.py makemigrations mockups` y revisar el archivo generado a mano.
- [ ] En `mockups/admin.py`: registrar `EventoClienteAdmin` **read-only**, copiando el patrón exacto de `AuditoriaAccionAdmin` (`has_add_permission`, `has_change_permission`, `has_delete_permission` → `False`; `list_display` con badge de color por tipo; `list_filter = ('tipo','fecha',('usuario', admin.RelatedOnlyFieldListFilter))`; `search_fields = ('username','pedido_id','sesion_id','ip_address')`).
- [ ] `python manage.py migrate` en local y verificar que la tabla se crea.

**Criterio de aceptación:** la migración corre en SQLite local sin errores y el admin muestra ambas tablas en modo lectura.

---

### Fase 1 — Captura de cliente (el corazón del plan)

#### 1.1 Endpoint de telemetría
- [ ] Nuevo archivo `mockups/views/telemetria.py`:

```python
"""Vistas HTTP — telemetría de cliente."""

@login_required
@require_POST
def telemetria_api(request):
    """
    Recibe un lote de eventos del cliente.
    Reglas: solo autenticados, solo POST, máximo 30 eventos por request,
    strings truncados a 2000 caracteres, tipos validados contra EventoCliente.TIPOS.
    La IP y el User-Agent SIEMPRE se toman del request, nunca del body.
    """
```

Reglas obligatorias de implementación:
- `require_roles_api` **no** aplica aquí: cualquier usuario autenticado puede enviar telemetría de su propia sesión.
- Ignorar silenciosamente eventos cuyo `tipo` no esté en `EventoCliente.TIPOS` (contarlos y reportarlos en la respuesta).
- Forzar `usuario=request.user`, `username=request.user.username`, `ip_address=get_client_ip(request)`, `user_agent=request.META.get('HTTP_USER_AGENT','')[:500]` desde el servidor.
- Throttle por sesión: máximo ~20 requests por minuto (contador en `request.session`, estilo `2fa_intentos`). Al exceder: responder `200 {'ok': True, 'descartado': True}` (nunca un error visible en la consola del usuario).
- Responder siempre `JsonResponse({'ok': True, 'guardados': N})`. Nunca lanzar 500 por telemetría: envolver el guardado en `try/except` y loguear con `security_logger.warning`.
- Usar `EventoCliente.objects.bulk_create(...)` para el lote (no un `save()` por evento).

- [ ] Registrar la ruta en `gasmanager/urls.py`, sección nueva:

```python
# ────────────────────────────────────────────────
# TELEMETRÍA DE CLIENTE (auditoría operativa)
# ────────────────────────────────────────────────
path('telemetria/api/', views.telemetria_api, name='telemetria_api'),
```

- [ ] Exportar la vista: `mockups/views/__init__.py` ya hace `from mockups.views.X import *`, así que **hay que agregar la línea** `from mockups.views.telemetria import *  # noqa: F403` en orden alfabético.

**Contrato del request:**
```json
{
  "sesion_id": "6f0c1e2a-...",
  "pagina": "/entregas/",
  "eventos": [
    {"tipo": "UI_TAP", "pedido_id": 4101, "boton": "cancelar", "ts": 1757970337000, "datos": {"ms_desde_carga": 184220}},
    {"tipo": "UI_MODAL_OPEN", "pedido_id": 4101, "boton": "cancelar"}
  ]
}
```

#### 1.2 Script de cliente
- [ ] Nuevo `mockups/static/js/client-telemetry.js` con API global `window.Telemetria`:

```js
window.Telemetria = {
  evento(tipo, datos),        // encola un evento (nunca bloquea, nunca lanza)
  flush(),                    // envía el lote pendiente
  setPedido(id)               // contexto de pedido activo (opcional)
};
```

Requisitos:
- Buffer en memoria; `flush()` automático cada 10 s o al llegar a 20 eventos.
- Envío con `fetch(url, {keepalive: true})` + header `X-CSRFToken` (leer cookie `csrftoken` con el `getCookie` que ya existe en `push-notifications.js`). **`navigator.sendBeacon` no permite headers**, así que solo como respaldo en `pagehide`, enviando el token dentro del body.
- Captura automática, sin que cada template tenga que pedirla:
  - `window.addEventListener('error')` y `'unhandledrejection'` → `JS_ERROR` con `{mensaje, archivo, linea}` (truncado).
  - `document.visibilitychange` → `APP_VISIBLE` / `APP_HIDDEN`.
  - `window.online` / `window.offline` → `NET_ONLINE` / `NET_OFFLINE`.
  - `Notification.permission` al iniciar → `PUSH_PERMISSION` con `{permiso, standalone, es_pwa}`.
  - `navigator.connection.effectiveType` dentro de cada `APP_HEARTBEAT` (si está disponible; si no, omitir).
  - Delegación de eventos en `document` para cualquier elemento con `data-telemetria="<accion>"` dentro de un `form[data-pedido]` → `UI_TAP` con `{boton, pedido_id}`.
  - **Heartbeat:** cada 3 minutos, si la pestaña está visible, se actualiza **la misma fila** `APP_HEARTBEAT` de esa `sesion_id` (upsert: buscar por `sesion_id`+`tipo` y actualizar `datos`/`fecha`), **no** se insertan filas nuevas. Objetivo: 1 fila por sesión que diga "la app estuvo abierta y visible hasta las HH:MM".
- El script **nunca** debe lanzar excepciones hacia afuera: todo envuelto en `try {} catch {}` silencioso.
- `sesion_id` en `sessionStorage` (se pierde al cerrar la pestaña, que es lo deseado). Generar con `crypto.randomUUID()`.

**Prohibido en este archivo:** leer valores de inputs, escuchar `keydown` para capturar teclas, enviar el DOM, o usar `console.log` con datos de usuario.

#### 1.3 Instrumentación de pantallas
- [ ] `mockups/templates/base.html`: incluir el script para usuarios autenticados (junto al bloque de push, antes de `{% block extra_js %}`), y agregar las variables de contexto que necesite (ruta actual, sesión).
- [ ] Marcar con `data-telemetria` los botones de las **4 pantallas acordadas** (no instrumentar otras vistas):

| Pantalla | Archivo | Qué marcar |
|---|---|---|
| Entregas camionero | `mockups/templates/entregas/camionero_entregas.html` | modal (abrir/confirmar/abortar), botón de notificaciones |
| Tarjetas de entrega | `mockups/templates/partials/_entregas_cards.html` | `tomar`, `entregado`, `cancelar` + `data-pedido="{{ pedido.id }}"` en cada `<form>` |
| Tarreo | `mockups/templates/entregas/tarreo.html` | `guardar` (incluye el caso "token vencido") |
| Pedidos telefonista | `mockups/templates/pedidos/transaccional_pedido.html` y `mockups/templates/pedidos/mis_pedidos_hoy.html` | `guardar`, `cancelar`, modal |
| Sobres | `mockups/templates/sobres/sobres.html` | `refrescar`, `cerrar_sobre`, `guardar` |

- [ ] Casos especiales a registrar explícitamente (son los reclamos más frecuentes):
  - `FORM_TOKEN_EXPIRED` cuando el servidor rechaza por token (el mensaje ya existe: *"Este pedido ya fue registrado o el formulario expiró"*). Se puede emitir desde el `messages` renderizado o desde el JS al detectar el redirect.
  - `UI_SUBMIT` con `datos.ms` = milisegundos entre el toque y el `submit` (permite probar "la app estaba lenta").
  - `UI_BLOCKED` si el botón está `disabled` cuando se toca.

#### 1.4 Ack de notificaciones push (la parte que mata el "no me llegan")
- [ ] Nuevo endpoint `POST /push/ack/` en `mockups/views/push.py`:

```json
{"tipo": "PUSH_SHOWN", "endpoint": "https://fcm.googleapis.com/...", "pedido_id": 4101, "ts": 1757970193000}
```
Escribe un `EventoCliente` con `origen='sw'`, `usuario=request.user`, y valida que el `endpoint` pertenezca a una `PushSubscription` del propio usuario (si no, se descarta: no se acepta telemetría de terceros).

- [ ] Ruta en `gasmanager/urls.py`: `path('push/ack/', views.push_ack, name='push_ack'),`
- [ ] En `mockups/static/sw.js`, emitir ack en 4 momentos:
  - entrada del handler `push` → `PUSH_RECEIVED`
  - después de `showNotification(...)` → `PUSH_SHOWN`
  - en `notificationclick` → `PUSH_CLICK` (o `PUSH_CLOSE` si `event.action === 'cerrar'`)
  - en `notificationclose` → `PUSH_CLOSE`
  - en `pushsubscriptionchange` → `PUSH_SUBCHANGE`
  - Reusar `cachedPushConfig.csrfToken`, que el SW **ya** guarda (ver `sendPushConfigToServiceWorker` en `push-notifications.js`). Si no hay token cacheado, pedirlo con `client.postMessage` o simplemente omitir el ack (nunca romper el push).

**Interpretación correcta (documentarla):** si el teléfono está apagado, sin datos o con la app cerrada por el sistema, **no habrá ack**. La ausencia de ack es evidencia legítima ("el dispositivo no reportó recepción"), no un fallo del servidor. El servidor siempre deja `PUSH_SENT` en `audit.log`.

#### 1.5 Quitar las excusas reales (obligatorio, es parte del objetivo)
Estos son defectos **existentes** que le dan razón al reclamo; se corrigen en esta misma fase:

- [ ] `mockups/templates/entregas/camionero_entregas.html`, handler de `#btnConfirmarAccionEntrega`:
  - hacer `bootstrap.Modal.getInstance(modal).hide()` **antes** de `submit()`;
  - `confirmBtn.disabled = true` inmediatamente;
  - `pendingFormId = null` después de enviar;
  - guardia: `const form = document.getElementById(pendingFormId); if (form) form.submit();` (hoy lanza `TypeError` silencioso si el pedido ya no está en el DOM);
  - emitir `UI_MODAL_OPEN` / `UI_MODAL_CANCEL` cuando corresponda.
- [ ] En `actualizarEntregas()` (mismo archivo) y en `mockups/templates/pedidos/mis_pedidos_hoy.html`: **no** reemplazar el contenedor si hay un modal abierto (`document.body.classList.contains('modal-open')`) ni si el contenedor tiene el foco. Así el contenido no se mueve bajo el dedo del usuario.
- [ ] Separar visualmente los botones **MARCAR ENTREGADO** (verde) y **CANCELAR ENTREGA** (rojo) en `_entregas_cards.html`: hoy son contiguos, ambos `btn-lg w-100 py-4`. Dejar al menos una separación clara y/o mover el rojo a una segunda fila con texto de advertencia.

**Criterio de aceptación Fase 1:** en Chrome/Android con DevTools, al abrir y abortar el modal de cancelar aparecen `UI_MODAL_OPEN` y `UI_MODAL_CANCEL` en la BD; al cortar la red y tocar un botón aparecen `UI_TAP` + `NET_OFFLINE`; al crear un pedido y verlo en el teléfono quedan `PUSH_RECEIVED`, `PUSH_SHOWN` y luego `PUSH_CLICK`/`PUSH_CLOSE`.

---

### Fase 2 — Vista forense por pedido

- [ ] Nuevo `mockups/views/auditoria.py` con `forense_pedido(request, pedido_id)`:
  - Permisos: `require_roles(request, ['jefe', 'admin'], 'index', ...)` (o superuser).
  - Une en una sola línea de tiempo cronológica:
    - `Pedido` + `DetallePedido` (cabecera y líneas, con precios snapshot)
    - `HistorialEstadoPedido` (cambios de estado con usuario y fecha)
    - `HistorialCambioPedido` (ediciones)
    - `AuditoriaAccion` filtrado por `objeto_tipo='Pedido'`, `objeto_id=pedido.id`
    - `EventoCliente` filtrado por `pedido_id=pedido.id`
    - `PushSubscription` del `entregador` (para saber si tenía notificaciones activas)
  - **Sin N+1:** usar `select_related`/`prefetch_related`; verificar el número de queries con un pedido de 5 líneas (debe ser constante, no creciente).
  - Ordenar todo por fecha ascendente y mostrar diferencias entre eventos consecutivos (`+00:10`) — es lo que hace evidente un barrido de 55 segundos.
  - Destacar visualmente en rojo: cancelaciones, `JS_ERROR`, `NET_OFFLINE`, `UI_BLOCKED`.
- [ ] Ruta: `path('auditoria/pedido/<int:pedido_id>/', views.forense_pedido, name='auditoria_pedido'),`
- [ ] Template nuevo `mockups/templates/auditoria/forense_pedido.html`, reusando el estilo de `auditoria/lista_auditoria.html`.
- [ ] Agregar el enlace "Ver línea de tiempo" en `mockups/templates/auditoria/lista_auditoria.html` (solo cuando `objeto_tipo == 'Pedido'`) y en el detalle del pedido para jefe/admin.

**Criterio de aceptación:** abriendo la línea de tiempo del pedido `#4101` se ve, en orden: creado por telefonista (19:03:12, IP 181.161.29.187) → push enviado → tomado por camionero (19:05:27, IP 186.41.157.35) → modal abierto → confirmado → cancelado (19:05:37).

---

### Fase 3 — Alertas de cancelación

- [ ] En `mockups/push_notifications.py`, nueva función `notificar_cancelacion_entrega(pedido, camionero)`:
  - Sigue **exactamente** el patrón de `notificar_nuevo_pedido`: obtiene `PushSubscription.objects.filter(usuario__rol__in=['telefonista','jefe','admin'], usuario__is_active=True, activa=True)`, itera, arma `subscription_info` con `sub.to_subscription_info()`, llama a `send_push_notification(...)`, y desactiva las expiradas (`error == 'subscription_expired'`).
  - Crea una `AlertaOperativa` por cada telefonista activo (`tipo='CANCELACION_CAMIONERO'`, `url='/entregas/'` o `/pedidos/mios/`), con mensaje del tipo: `"Pedido #4100 cancelado por camionero_r"`.
  - Devuelve la cantidad de pushes enviados, igual que `notificar_nuevo_pedido`.
  - **No** notificar a otros camioneros.
- [ ] En `mockups/views/entregas.py`, dentro de `camionero_cancelar_entrega` (y **sin alterar** la lógica de estado):
  - mejorar el log: `audit_logger.info(f"PEDIDO_CANCEL | #{pedido.id} | By camionero: {request.user.username} | IP: {get_client_ip(request)} | UA: {ua_resumen}")`;
  - llamar a la notificación envuelta en `try/except` (un fallo de push no debe romper la respuesta):

```python
try:
    from mockups.push_notifications import notificar_cancelacion_entrega
    notificar_cancelacion_entrega(pedido, request.user)
except Exception as e:
    audit_logger.warning(f"PUSH_ERROR | Cancelacion #{pedido.id} | Error: {str(e)}")
```

- [ ] Habilitar push a los nuevos roles:
  - `mockups/views/push.py` → `push_subscribe`: cambiar `require_roles_api(request, ['camionero'])` a `require_roles_api(request, ['camionero', 'telefonista', 'jefe', 'admin'])`.
  - `mockups/templates/base.html`: la condición `{% if user.is_authenticated and user.rol == "camionero" %}` debe incluir esos roles.
  - **Verificar** que `notificar_nuevo_pedido` siga filtrando solo camioneros.
- [ ] Campanita de alertas:
  - `GET /alertas/api/` → no leídas del usuario (últimas 20) + contador.
  - `POST /alertas/<int:alerta_id>/leida/` → marca `leida_el` (validar que la alerta sea del usuario que la pide).
  - Partial nuevo `mockups/templates/partials/_campanita_alertas.html` con badge, incluido en `base.html` para `telefonista`, `jefe` y `admin`.
  - JS de refresco cada 60 s + al volver a la pestaña (`visibilitychange`). **No** agregar otro polling a los ya existentes en las pantallas de camionero.
- [ ] Regla de patrón en `camionero_cancelar_entrega`: contar cancelaciones del mismo camionero en los últimos 10 minutos (`HistorialEstadoPedido` o `AuditoriaAccion`, filtrando por `cambiado_por`/`username` y `fecha`). Si `>= settings.ALERTA_CANCELACIONES_UMBRAL` (nuevo setting, default `2`), generar `AlertaOperativa` tipo `PATRON_CANCELACIONES`, push y `AuditoriaAccion` tipo `SUSPICIOUS`.

**Criterio de aceptación:** un camionero anula un pedido → el telefonista recibe push y ve la campanita con la alerta en <5 s; si anula dos en 10 min, aparece la alerta de patrón; el pedido sigue quedando en `estado='cancelado'` con su `entregador` (sin cambios de comportamiento).

---

### Fase 4 — Purga, tests y documentación

- [ ] Comando nuevo `mockups/management/commands/purgar_eventos_cliente.py` (seguir el estilo de `limpiar_sobres.py`):
  - `--antes-de YYYY-MM-DD` **obligatorio** (no hay purga por defecto ni borrado total).
  - Opcionales: `--tipo TIPO`, `--usuario username`, `--dry-run`.
  - Salida: cantidad de filas que se borrarían / se borraron.
  - Nunca borrar `AuditoriaAccion` ni `AlertaOperativa` desde este comando.
- [ ] Tests nuevos en `mockups/tests/auditoria/` (**no tocar** los tests existentes):
  - telemetría: requiere login (302/403 sin sesión), rechaza GET (405), ignora tipos inválidos, trunca strings largos, ignora la IP enviada por el cliente y usa la del request;
  - `forense_pedido`: 403/redirect para `camionero`, 200 para `jefe`/`admin`; número de queries constante;
  - cancelación: crea `AlertaOperativa` para telefonistas y **no falla** si el envío de push lanza excepción (mockear `send_push_notification`);
  - umbral de patrón: 2 cancelaciones en 10 min generan la alerta;
  - purga: borra solo lo anterior a la fecha y respeta `--dry-run`.
- [ ] Documentar en `docs/AUDITORIA_OPERATIVA.md` (nuevo): qué se registra, qué **no** se registra, cómo leer la línea de tiempo, cómo purgar, y la interpretación de la ausencia de ack. Agregar una referencia corta en `README.md` y en `docs/PUSH_NOTIFICATIONS.md`.

---

## 6. Definición de "terminado" (Definition of Done)

1. `python manage.py makemigrations --check --dry-run` no reporta cambios pendientes.
2. `python manage.py test mockups.tests.auditoria mockups.tests.pedidos.test_logica_negocio -v 2` pasa completo.
3. `python manage.py check --deploy` no agrega advertencias nuevas respecto de la rama base.
4. En un teléfono Android real: se registran `UI_TAP`, `UI_MODAL_*`, `PUSH_SHOWN` y `APP_HEARTBEAT`.
5. La línea de tiempo de un pedido cancelado muestra el orden completo de eventos con sus deltas.
6. El telefonista recibe push + campanita al cancelarse una entrega.
7. El comportamiento visible para el usuario **no cambió** salvo las mejoras de UI de §1.5 (el modal ahora se cierra y los botones están separados).
8. `docs/AUDITORIA_OPERATIVA.md` explica cómo purgar y qué no se guarda.
9. Ninguna dependencia nueva en `requirements*.txt`.

---

## 7. Riesgos y trampas conocidas

| Riesgo | Mitigación |
|---|---|
| `sendBeacon` no permite headers → falla el CSRF | Usar `fetch(..., {keepalive:true})` con `X-CSRFToken` como vía principal; `sendBeacon` solo con token en el body y `@csrf_exempt` **con validación manual del token + sesión**. No dejar el endpoint sin protección |
| El SW no tiene token CSRF al primer push | El SW ya recibe `PUSH_CONFIG` con `csrfToken`; si no lo tiene, omitir el ack (nunca romper el push) |
| Volumen de datos | 1 fila por sesión para el heartbeat + purga manual `--antes-de`. Estimar: ~40-80 eventos por turno de camionero |
| Logs de archivo en Render son efímeros | El disco se reinicia en cada deploy: `logs/*.log` se pierden. Por eso la evidencia durable va a la BD (`EventoCliente`). En Render solo se ve `stdout` |
| JSON en MySQL/PG | `JSONField` funciona en MySQL 5.7+/MariaDB 10.2+ y PostgreSQL. No usar operadores específicos de PostgreSQL en las consultas de la vista forense |
| `require_debug_false` en el logging | Los handlers de archivo se comportan distinto con `DEBUG=True`; probar también con `DEBUG=False` |
| Throttling con `LocMemCache` | No compartir contadores entre workers de Gunicorn. Usar contador por sesión, no por IP/caché global |
| iOS | Web Push en iPhone requiere iOS 16.4+ y la app **agregada a la pantalla de inicio**. Documentarlo: un reclamo "no me llegan" desde iPhone sin PWA instalada es una limitación del sistema, no un bug |
| Sesiones de 48 h | `SESSION_COOKIE_AGE = 172800`: una pestaña vieja puede sobrevivir 2 días. El polling y el heartbeat deben manejar bien el caso "sesión expirada" (recibir 403 y detenerse, no reintentar en loop) |
| Escritura excesiva en BD | Nunca escribir telemetría dentro del `poll` de 45 s; el polling no genera eventos (solo `UI_TAP`, visibilidad, red, heartbeat y errores) |
| La telemetría no debe degradar la app | Todo el JS en `try/catch`, envío asíncrono, y sin bloquear el `submit` del formulario. Si el endpoint de telemetría está caído, la app debe funcionar igual |

---

## 8. Despliegue

1. Merge/PR a la rama de despliegue revisado por el dueño.
2. Verificar que `build.sh` corre `migrate` (ya lo hace) y que las migraciones son reversibles.
3. Deploy a Render → confirmar en logs que las migraciones `0032`/`0033` se aplicaron.
4. Activar la telemetría de a poco: primero **solo** `mockups/templates/entregas/*` (camioneros), verificar volumen 24-48 h, y luego ampliar a telefonista/tarreo/sobres.
5. Probar el flujo real: crear un pedido, que un camionero lo tome, lo cancele, y confirmar push + campanita + línea de tiempo.
6. **Avisar al personal** antes de activar en producción: nota interna de una página explicando que se registran toques y estado de la app (no contenido) para trazabilidad operativa. Es requisito de la Ley 19.628/21.719 y además quita la excusa "nadie me avisó".
7. Rollback: desactivar la inclusión del script en `base.html` (deja de emitirse telemetría) sin necesidad de revertir migraciones.

---

## 9. Preguntas abiertas (decidir antes de la Fase 3)

1. **Campanita:** ¿las alertas caducan solas a las 24 h (recomendado, evita acumulación) o quedan hasta marcarlas como leídas?
2. **Umbral de patrón:** ¿`ALERTA_CANCELACIONES_UMBRAL = 2` en 10 minutos (recomendado, habría disparado 3 veces en el caso real) u otro valor?
3. **Latencia:** ¿se registra también el tiempo de respuesta del POST de cancelar (`datos={'ms': ...}`) para poder probar "la app estaba lenta"? (recomendado: sí, es gratis)
4. **Retención por defecto:** ¿la purga se ejecuta a mano o se agenda (cron de Render) con una ventana estándar (p. ej. 1 año)?
5. **Fuera de alcance, plan aparte:** agregar la acción "Devolver a pendiente" (liberar el pedido al pool) separada de "Cancelar". Hoy `camionero_cancelar_entrega` documenta *"devuelve pedido a 'pendiente'"* pero implementa `estado='cancelado'`, y **no existe** forma de devolver un pedido. Esto explica por qué un camionero puede creer que estaba "devolviendo" pedidos cuando en realidad los anulaba. Requiere validación del negocio antes de tocarlo.

---

## 10. Referencias rápidas del código actual

| Necesito… | Archivo / símbolo |
|---|---|
| Registrar auditoría de negocio | `mockups/models.py` → `AuditoriaAccion.registrar()` |
| Permisos por rol (HTML / JSON) | `mockups/utils/permisos.py` → `require_roles`, `require_roles_api` |
| IP real del cliente | `mockups/utils/permisos.py` → `get_client_ip(request)` |
| Patrón de envío de push | `mockups/push_notifications.py` → `notificar_nuevo_pedido()` |
| Llamada a push desde una vista | `mockups/views/pedidos.py` (~líneas 135-150) |
| Endpoints push existentes | `mockups/views/push.py`, rutas en `gasmanager/urls.py` |
| Service Worker y ack de push | `mockups/static/sw.js` (handlers `push`, `notificationclick`, `notificationclose`, `pushsubscriptionchange`) |
| Cliente push existente (getCookie, sync) | `mockups/static/js/push-notifications.js` |
| Throttle por sesión (patrón) | `mockups/views/auth.py` → contador `2fa_intentos` |
| Admin read-only de auditoría (patrón) | `mockups/admin.py` → `AuditoriaAccionAdmin` |
| Visor de auditoría actual | `mockups/views/catalogos.py` → `auditoria_lista`, template `auditoria/lista_auditoria.html` |
| Comando de limpieza (patrón) | `mockups/management/commands/limpiar_sobres.py` |
| Estilos de test | `mockups/tests/pedidos/test_logica_negocio.py` |
