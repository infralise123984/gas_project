# API para la app móvil del camionero

> **Documento de traspaso.** Define el contrato entre `gas_project` (Django) y `gas_facil_movil` (Flutter).
> Última actualización: 2026-10-06.
> Contraparte en el repo móvil: `gas_facil_movil/docs/PLAN_APP_CAMIONERO.md` (§4 y §14). **Si algo cambia, cambia en los dos.**
>
> Revisado contra el código de `gas_project` el 2026-10-06: helpers, vistas, filtros y *properties* citados
> verificados uno por uno. Las desviaciones encontradas van marcadas como **nota de revisión** en la sección
> correspondiente y resumidas en §10.

---

## 1. Contexto

Existe una app Flutter (`gas_facil_movil`) para el **camionero**. No es un sistema aparte: es una
extensión de la web actual. Replica la interfaz y el flujo de las páginas que el camionero ya usa
(`entregas/camionero_entregas.html`, `entregas/tarreo.html`, `entregas/mis_entregas_camionero.html`).

Entre ambos sistemas hace falta una **API JSON**, porque hoy los endpoints que existen
(`entregas/api/`, `entregas/mias/api/`) devuelven **HTML renderizado**, no datos.

**Objetivo de la primera entrega (F1):** que el camionero pueda iniciar sesión y **ver** sus pedidos
(pendientes, en ruta y entregados hoy) desde el teléfono. Las acciones (tomar, entregar, cancelar,
devolver) son la F2.

---

## 2. Reglas del repo que NO se tocan

Tomadas de `docs/PLAN_AUDITORIA_OPERATIVA.md` §3. Aplican también a este trabajo:

- **No** editar migraciones ya aplicadas (`0001_*` … `0034_*`). La cadena va en `0032_nuevos_metodos_pago_sobres`,
  `0033_pedido_descuento_linea_y_devolucion` y `0034_conteo_diario_balones`; la migración de idempotencia
  (§6.2) se agrega **como nueva**, al final.
- **No** editar `mockups/views_monolith_backup.py`, `backups/*.sql`, `logs/*`, `graphify-out/`.
- **No** modificar los tests existentes (`mockups/tests.py`, `mockups/tests_logica_negocio.py`, y los
  demás `tests_*.py` del app). Si hacen falta tests nuevos, van en archivo aparte.
- **No** tocar autenticación, 2FA, `AXES_*`, `SECRET_KEY`, `VAPID_*`, `CSRF_*`, `ALLOWED_HOSTS` ni las
  cabeceras de seguridad (`mockups/middleware.py`).
- **No** debilitar el CSP.
- **No** modificar `notificar_nuevo_pedido`: debe seguir notificando **solo a camioneros**.
- **No** cambiar la semántica de la cancelación (el pedido queda `cancelado` y **conserva** el
  `entregador`).
- **No** agregar automatismos que cambien estados de pedidos (nada de cron, señales ni tareas).
- **No** cambiar el filtro de pendientes de `camionero_entregas`: `estado='pendiente'`,
  `origen='telefono'`, `entregador__isnull=True`, fecha de hoy.

**Este trabajo es aditivo.** Nada de lo existente debe cambiar de comportamiento.

---

## 3. Dónde va el código

```
mockups/
└── api/                      ← paquete NUEVO
    ├── __init__.py
    ├── urls.py               ← se incluye desde gasmanager/urls.py
    ├── respuestas.py         ← sobre de respuesta y errores (§5)              [F1 ✔]
    ├── acceso.py             ← sesión, rol y validación de entrada (413/400)   [F1 ✔]
    ├── limites.py            ← control de abuso, ventana fija (§6.4)           [F1 ✔]
    ├── serializadores.py     ← modelos → JSON del §7.2                         [F1 ✔]
    ├── auth.py               ← csrf / login / logout / perfil                 [F1 ✔]
    ├── entregas.py           ← listado [F1 ✔] y las 4 acciones [F2]
    ├── idempotencia.py       ← decorador + modelo de la §6.2                 [pendiente]
    ├── tarreo.py             ← venta tarreo                                  [F4 ✔]
    └── catalogos.py          ← versión [F1 ✔] y balones [F4 ✔], resumen, historial
```

Se registra en `gasmanager/urls.py` con **una sola línea**, al final:

```python
path('api/v1/', include('mockups.api.urls')),
```

**Estilo obligatorio: Function-Based Views** con `@never_cache`. **No se usa DRF.**

**Nota de revisión — `@login_required` NO se usa.** Redirige 302 a `/auth/login/`
(`settings.LOGIN_URL`), y el contrato exige `401 no_autenticado` con sobre. Su lugar lo ocupa
`@acceso_api(roles)` (`api/acceso.py`), que comprueba la sesión **antes** del rol (porque
`require_roles_api` lee `request.user.rol`, inexistente en `AnonymousUser`) y convierte el 403 del helper
al sobre del §5.2. Del mismo modo, `@require_POST` se reemplaza por `@solo_post`: su 405 no trae sobre.

### 3.1 Regla de oro: la API no tiene lógica de negocio

El endpoint es **transporte + validación de entrada**. El cambio de estado lo ejecuta la misma
secuencia que usa la web. Si aparece lógica nueva en `api/`, está mal.

| Necesidad | Se reutiliza |
|---|---|
| Listado y estadísticas del camionero | `services.camionero` → `queryset_actividad_camionero_dia`, `stats_ventas_camionero`, `stats_dia_camionero`, `kilos_de_pedido` |
| Catálogo y precios vigentes | `services.catalogos.get_balones_activos_ordenados()` |
| Anulación | **Ningún endpoint de §4 la usa hoy.** Solo aplicaría a un futuro `/anular/`, que en la web es de `telefonista`/`admin`: queda **fuera** del alcance "solo camioneros". Si se agrega: `services.pedidos.anular_pedido` + `AnulacionNoPermitida` |
| Sobres afectados | **Ningún endpoint de §4 la usa hoy.** Las acciones del camionero no tocan sobres (el cierre de caja es del bodeguero/jefe). Si se agregara `/anular/`: `services.sobres` → `hay_sobre_cerrado_para_pedido`, `resincronizar_sobres_afectados_por_pedido` |
| Permisos y auditoría | `utils.permisos` → `require_roles_api`, `get_client_ip`, `puede_otorgar_descuento` |
| Fechas | `utils.fechas` → `today_chile`, `now_chile`, `rango_dia_chile`, `get_rango_utc_para_fecha` |
| Recálculo de montos | `Pedido.calcular_totales()` |
| Historial y auditoría | `HistorialEstadoPedido`, `HistorialCambioPedido`, `AuditoriaAccion.registrar` |

### 3.2 Permisos: obligatorio usar los helpers

- Usar **`require_roles_api(request, ['camionero'])`** en todos los endpoints. **Nunca** `if user.rol == ...`.
- Ese helper ya audita `PERM_DENIED` en `AuditoriaAccion` y en el logger `security`.
- **Choque de sobres — resuelto.** `require_roles_api` (`mockups/utils/permisos.py:51`) devuelve
  `JsonResponse({'error': 'No autorizado'}, status=403)`: **no** trae `servidor_ahora` ni `error.codigo`, así que
  viola el §5.2. Se conserva su uso —porque audita `PERM_DENIED`— pero su salida **se descarta**:
  `@acceso_api` (`mockups/api/acceso.py`) responde `respuestas.error('sin_permiso')`. Hay un test que fija
  las dos cosas a la vez: sobre correcto **y** `AuditoriaAccion(tipo='PERM_DENIED')` presente.
- Además del rol, **cada endpoint de acción debe validar la propiedad del objeto**:
  `select_for_update().get(id=..., entregador=request.user, ...)`. Un camionero no puede tocar un
  pedido ajeno. Los filtros exactos están en la §7.

---

## 4. Rutas

```
GET    /api/v1/auth/csrf/          ← F1 · SIN sesión: siembra el token CSRF (§6.1)
POST   /api/v1/auth/login/         ← F1
POST   /api/v1/auth/logout/        ← F1
GET    /api/v1/auth/perfil/        ← F1

GET    /api/v1/entregas/           ← F1
POST   /api/v1/entregas/<id>/tomar/      ← F2
POST   /api/v1/entregas/<id>/entregar/   ← F2
POST   /api/v1/entregas/<id>/cancelar/   ← F2
POST   /api/v1/entregas/<id>/devolver/   ← F2

GET    /api/v1/resumen-hoy/                             ← F6
POST   /api/v1/tarreo/                                  ← F4
GET    /api/v1/balones/                                 ← F4
GET    /api/v1/historial/?mes=YYYY-MM                   ← F6
GET    /api/v1/historial/<YYYY-MM-DD>/                  ← F6
GET    /api/v1/version/                                 ← F1
```

Fases: **F1** = `auth/*`, `entregas/` (GET) y `version/`. **F2** = las cuatro acciones.
**F4** = `tarreo/`, `balones/`. **F6** = `resumen-hoy/`, `historial/*`.

**Estado: F1, F2 y F4 implementados y probados** (`mockups/api/`, suite en `mockups/tests_api_movil.py`).
Lo que sigue es F6 (`resumen-hoy/`, `historial/`) y la idempotencia con `Idempotency-Key` (§8), que es
aditiva pero necesita una migración.

---

## 5. Formato de las respuestas

### 5.1 Sobre de éxito

Todos los `200` llevan el mismo sobre:

```json
{
  "servidor_ahora": "2026-10-06T10:22:31-03:00",
  "data": { }
}
```

`servidor_ahora` **es obligatorio en toda respuesta**, incluidos los errores. La app nunca calcula
"hoy" con el reloj del teléfono: lo toma de acá, y lo usa además para ordenar la cola offline.
Se genera con `now_chile().isoformat()`.

### 5.2 Sobre de error

```json
{
  "servidor_ahora": "2026-10-06T10:22:31-03:00",
  "error": {
    "codigo": "no_disponible",
    "mensaje": "El pedido ya no está disponible o ya fue tomado."
  }
}
```

El `codigo` es **estable y en minúsculas**: la app decide con él, nunca leyendo el `mensaje`.
El `mensaje` es para mostrar al usuario, en español, corto y **sin detalles internos** (nada de
stack traces, nombres de tablas ni SQL — ver `docs/SECURITY_TESTING.md`).

| HTTP | `codigo` | Cuándo |
|---|---|---|
| 400 | `validacion` | Falta un campo o tiene formato inválido |
| 401 | `no_autenticado` | Sin sesión válida |
| 403 | `sin_permiso` | Rol incorrecto o el pedido no le pertenece |
| 405 | `metodo_no_permitido` | Se llamó con el verbo equivocado (p. ej. `GET` a un endpoint de acción) |
| 404 | `no_encontrado` | El recurso no existe |
| 409 | `no_disponible` | El pedido ya fue tomado por otro camionero |
| 409 | `conflicto_estado` | El pedido cambió de estado y la acción ya no aplica |
| 429 | `bloqueado_login` | `django-axes` bloqueó los intentos (`AXES_FAILURE_LIMIT = 5`) |
| 429 | `demasiadas_peticiones` | Se superó el límite por usuario de ese endpoint (§6.4). Trae `Retry-After` |
| 500 | `error_interno` | Fallo inesperado (el detalle va al log, no al cliente) |

**`requiere_2fa` no es un error:** ver §6.1.

**El sobre se garantiza en la capa API, no en el helper de roles.** `require_roles_api` emite su propio JSON 403
(`{"error": "No autorizado"}`) sin sobre: hay que convertirlo antes de responder. Ver §3.2.

---

## 6. Reglas transversales

### 6.1 Autenticación

Se **reutiliza la sesión de Django**. No hay tokens ni JWT. **No se toca `mockups/views/auth.py`** (el de la web,
que incluye 2FA, `login_view`, `logout_view` y `AXES_*`); el archivo nuevo del paquete es `mockups/api/auth.py`.

- `POST /api/v1/auth/login/` recibe `{"username": "...", "password": "..."}` (JSON o form).
- Llama a `django.contrib.auth.authenticate()` y luego `login()`, igual que `login_view`.
- Registra `LOGIN_OK` / `LOGIN_FAIL` en `AuditoriaAccion` y los loggers `audit` / `security`.
- Si el usuario tiene `totp_activo = True`, responde **`202` con
  `{"data": {"requiere_2fa": true}}`**. La app muestra un aviso de que todavía no soporta 2FA y no
  intenta autenticarlo. Los camioneros no lo usan hoy.
- Si `axes` bloqueó: `429` con `bloqueado_login` y la hora de desbloqueo si se puede calcular.
  **La app no debe reintentar** ante un `429`: cada intento extiende el bloqueo.

**Resultado del *spike* (§10 #1) — verificado leyendo el código de Django 5.1.3 instalado, no supuesto.**
`CsrfViewMiddleware.process_view` exige en todo método no seguro:

1. Un `X-CSRFToken` que coincida con la cookie `csrftoken` (doble envío). Sin cookie → 403.
2. Si el header `Origin` **viaja** y no está en `CSRF_TRUSTED_ORIGINS` → 403.
3. Si el header `Origin` **no viaja** y la conexión es HTTPS, exige un `Referer` https que coincida
   (`_check_referer`). **Sin `Origin` ni `Referer` la petición se rechaza con 403** (`REASON_NO_REFERER`).

Consecuencia para la app: en **todo** POST debe mandar `Origin` confiable **y** `X-CSRFToken`. Y hay un
problema de huevo y gallina: como el resto del API exige sesión, el cliente no tendría de dónde sacar el
token antes de su primer login. Por eso F1 agrega **`GET /api/v1/auth/csrf/`** (§4), sin sesión y con
`@ensure_csrf_cookie`, que devuelve el token y siembra la cookie. Después, con sesión, `GET /auth/perfil/`
también lo entrega. Ojo: `get_token()` devuelve el token **enmascarado** y la cookie guarda el secreto;
son valores distintos y Django acepta **cualquiera de los dos** en la cabecera.

**No se usa `@csrf_exempt`**: sigue prohibido por el §2. Los cuatro casos del *spike* están fijados como
tests en `CsrfSpikeTest` (sin Origin ni Referer → 403; Origin no confiable → 403; sin token → 403; con
ambos → 200).

**Credenciales inválidas → `401 no_autenticado`** con mensaje "Usuario o contraseña incorrectos.". No se
creó un código nuevo para no romper el contrato: la app decide con `codigo`.

**Interacción con `django-axes` 7.0.1** (verificado en el código de la librería): el bloqueo es **por IP**
(`AXES_LOCKOUT_PARAMETERS = ['ip_address']`, porque el proyecto no configura los flags legacy) y su
middleware **reemplaza** la respuesta del view por un 429 en texto plano cuando el intento que falla
alcanza el límite. El endpoint entonces: (a) consulta `AxesProxyHandler.is_allowed()` **antes** de
autenticar —es una lectura pura, no registra intentos ni alarga la ventana— y (b) si axes marcó la
petición (`request.axes_locked_out`), responde el sobre y limpia la marca. Resultado: `429 bloqueado_login`
**con** `Retry-After` calculado desde el intento más antiguo de esa IP, no la ventana completa.

### 6.2 Idempotencia

Todo endpoint que **muta** estado exige el header:

```
Idempotency-Key: <uuid v4 generado por la app>
```

Sin el header → `400` con `codigo: "validacion"`.

- La app reutiliza **la misma clave** al reintentar una acción que no pudo confirmar (por ejemplo
  tras quedarse sin señal). Esa es la razón de existir.
- El decorador `@idempotente` va **antes** de ejecutar la vista:

```python
class OperacionIdempotente(models.Model):
    clave         = models.CharField(max_length=64, unique=True)
    usuario       = models.ForeignKey('Usuario', on_delete=models.CASCADE, null=True)
    endpoint      = models.CharField(max_length=100)
    respuesta_json = models.TextField()
    status_http   = models.PositiveSmallIntegerField()
    creada_el     = models.DateTimeField(auto_now_add=True)
```

- Si la clave ya existe **para ese usuario**: devolver la respuesta guardada con su mismo status,
  **sin volver a ejecutar la vista**.
- Si la clave existe pero es de otro usuario: `400 validacion` (no filtrar la respuesta ajena).
- Se purgan las de más de 7 días con un comando de gestión (`purgar_idempotencia`), **no** con cron.

> Es **infraestructura técnica**, no una feature de negocio. La tabla no aparece en ningún flujo del
> negocio ni en la web.

### 6.3 Fechas, montos y formato

- Todas las fechas del servidor se calculan con `utils.fechas` (`today_chile`, `rango_dia_chile`).
  **Nunca** `datetime.now()`.
- Salida en ISO 8601 con zona (`-03:00`).
- Los montos son **enteros** en pesos, sin formato. El separador de miles lo pone la app.
- La app no recalcula nada de dinero: si muestra un total, es el que mandó el servidor.

### 6.4 Control de abuso

`mockups/api/limites.py`: ventana fija sobre la caché de Django, **por usuario autenticado**.

- La clave sale del usuario, **nunca** de `X-Forwarded-For`: esa cabecera la controla el cliente, y usarla
  como clave permite evadir el límite rotándola.
- `cache.add` fija el TTL en la misma operación que crea la clave: no existe el hueco del patrón
  "`INCR` y después `EXPIRE`", que deja la clave viva para siempre si el proceso muere en medio.
- `Retry-After` es el tiempo que **realmente** queda de la ventana, no la ventana completa.
- Si la caché falla, se **falla abierto** con aviso en el logger `security`: primero que el camionero vea
  sus pedidos, después el límite. Es una decisión explícita.
- Límites: `entregas` 60/min, `perfil` 30/min, `version` 10/min, y las cuatro acciones 30/min
  (clave `acciones_entregas`, F2: mueven estado y dinero, así que van más estrechas que el listado).
  En F4 se agregan `balones` 60/min (lectura, igual que el listado) y `tarreo` 30/min (crea dinero, igual
  que las acciones).
  El login **no** lleva límite propio: ya lo cubre `django-axes`.

**Alcance honesto:** esto frena bucles de cliente y martilleo de un usuario. **No** es protección DDoS —
la saturación de red se resuelve en infraestructura. Y como `settings.CACHES` no está definido (se usa
LocMemCache), el contador es **por proceso**: con más de una instancia el límite se multiplica (§10).

---

## 7. Contrato de cada endpoint

### 7.1 `GET /api/v1/entregas/` — panel del camionero

Réplica exacta de las tres consultas de `camionero_entregas`:

| Grupo | Filtro |
|---|---|
| `en_ruta` | `estado='en_ruta'`, `entregador=request.user` — **sin límite de fecha** |
| `pendientes` | `estado='pendiente'`, `origen='telefono'`, `entregador__isnull=True`, fecha de hoy |
| `entregados_hoy` | `queryset_actividad_camionero_dia(user, hoy).filter(estado='entregado')` |

Usar `prefetch_related('detalles__balon')` para no generar N+1.

**Nota de revisión — `select_related('registrador')` se omite a propósito.** El objeto Pedido del §7.2 no
expone `registrador`, así que ese JOIN no ahorraría ninguna consulta (la web sí lo usa porque su template
muestra quién registró). El `prefetch` de detalles sí es obligatorio: lo necesitan `kilos_de_pedido()` y
las líneas. Hay un test que compara el número de consultas antes y después de agregar pedidos: si aparece
un N+1, falla.

```json
{
  "servidor_ahora": "2026-10-06T10:22:31-03:00",
  "data": {
    "hoy": "2026-10-06",
    "pendientes": [Pedido],
    "en_ruta": [Pedido],
    "entregados_hoy": [Pedido],
    "actividad_hoy": [Pedido]
  }
}
```

`actividad_hoy` es la actividad completa del día del camionero (`queryset_actividad_camionero_dia`), sin los
pendientes sin asignar: es lo que lista `mis_entregas_camionero`, que además de las entregas muestra los
**cancelados** (con su franja roja) y los que siguen **en ruta**. `entregados_hoy` es su subconjunto
`entregado` y se mantiene por compatibilidad.

### 7.2 Objeto `Pedido`

```json
{
  "id": 4102,
  "estado": "en_ruta",
  "estado_etiqueta": "En ruta",
  "origen": "telefono",
  "origen_etiqueta": "Teléfono / Domicilio",
  "sector": "Población Recreo",
  "direccion_entrega": "Los Aromos 1234, casa esquina",
  "metodo_pago": "efectivo",
  "fecha": "2026-10-06T09:15:00-03:00",
  "monto_total": 45800,
  "subtotal_bruto": 45800,
  "descuento_total": 0,
  "tiene_descuento": false,
  "kilos": 22,
  "lineas": [
    {
      "balon_id": 2,
      "balon_nombre": "Gas 11 kg",
      "peso_neto_gas": 11,
      "cantidad": 2,
      "precio_venta_unitario": 22900,
      "descuento_unitario": 0,
      "subtotal": 45800,
      "subtotal_neto": 45800
    }
  ]
}
```

**Nota de revisión — precisión sobre las *properties*** (verificado en `mockups/models.py`):

- `Pedido.subtotal_bruto` (l.341) y `Pedido.tiene_descuento` (l.336) **sí** son *properties*.
- `Pedido.descuento_total` es un **campo** `DecimalField`, **no** una property: solo refleja la realidad si antes
  se llamó `calcular_totales()` (l.322).
- `Pedido.monto_total` **ya es el neto** (bruto − descuentos). A nivel de `Pedido` **no existe** `subtotal_neto`:
  esa property es de `DetallePedido` y va **solo** dentro de cada elemento de `lineas`.
- `kilos` por pedido sale de `services.camionero.kilos_de_pedido()`, que devuelve **`int`**: `cantidad` y
  `peso_neto_gas` son ambos `PositiveIntegerField`, así que la multiplicación es entera. El camino donde
  aparece `Decimal` es el **agregado** de `stats_ventas_camionero`
  (`Sum(F('cantidad') * F('balon__peso_neto_gas'))`), y ese ya viene envuelto en `int(...)` dentro de
  `stats_dia_camionero`. Serializar siempre con `int(...)`, por consistencia entre ambos caminos.
- `estado_etiqueta` / `origen_etiqueta` son `get_estado_display()` / `get_origen_display()`.
- `sector` es un `CharField` **sin `choices`** (`models.py:283`): **no existe** `get_sector_display()`.
  `Pedido.SECTORES` es una lista de clase que solo usa el formulario como respaldo, no el campo.
  Va el **valor crudo** (`"Población Recreo"`), no la etiqueta con
  prefijo `[SUR]` que se ve en la web. Al ser texto libre heredado puede traer valores viejos sin validar:
  el endpoint debe devolverlo tal cual y **no** intentar mapearlo.

### 7.3 Las cuatro acciones

**Estado: implementado** (`mockups/api/entregas.py`, probado en `AccionesEntregasTest`).

Todas: `POST`, con `Idempotency-Key`, y devuelven `{"data": {"pedido": Pedido}}` con el pedido ya
actualizado. Todas replican la vista web correspondiente, incluidos `HistorialEstadoPedido`,
`HistorialCambioPedido` y `AuditoriaAccion`, **salvo las desviaciones marcadas como nota de revisión**
(hoy, solo `/cancelar/`).

| Endpoint | Vista web de referencia | Filtro obligatorio | Efecto |
|---|---|---|---|
| `/tomar/` | `camionero_tomar_pedido` | `estado='pendiente'`, `entregador__isnull=True`, `origen='telefono'` | → `en_ruta`, `entregador=user` |
| `/entregar/` | `camionero_marcar_entregado` | `estado='en_ruta'`, `entregador=user`, `origen='telefono'` | → `entregado` |
| `/cancelar/` | `camionero_cancelar_entrega` | `estado='en_ruta'`, `entregador=user` | → `cancelado`, **conserva** `entregador` |
| `/devolver/` | `camionero_devolver_pedido` | `estado='en_ruta'`, `entregador=user`, `origen='telefono'` | → `pendiente`, limpia `entregador`, setea `devuelto_el` y `devuelto_por` |

Detalles que **no** se pueden omitir:

1. **`select_for_update()` dentro de `transaction.atomic()`** en las cuatro. Es lo que evita que dos
   camioneros tomen el mismo pedido.
   **Nota de revisión — la web no lo cumple en `/cancelar/`.** `camionero_tomar_pedido`,
   `camionero_marcar_entregado` y `camionero_devolver_pedido` sí usan `transaction.atomic()` +
   `select_for_update()`. En cambio `camionero_cancelar_entrega` (`mockups/views/entregas.py:442`) usa
   `Pedido.objects.get(...)` **sin** transacción ni bloqueo. Para `/cancelar/`, "replicar exactamente la vista
   web" y "`select_for_update` en las cuatro" son incompatibles. La API lo implementa igual (es aditivo y no
   cambia la web), pero queda **pendiente decidir** si se endurece también la vista web — §10.
2. `Pedido.DoesNotExist` → `409 no_disponible` y log de seguridad, **no** un 500.
3. `/devolver/` además **re-notifica** a los demás camioneros:
   ```python
   from mockups.push_notifications import notificar_nuevo_pedido
   notificar_nuevo_pedido(pedido, excluir_usuario_id=request.user.id, title='🚚 ¡Pedido disponible!')
   ```
   Va **fuera** de la transacción, envuelto en `try/except`, para no alargar el bloqueo de la fila.
4. `/cancelar/` **no** limpia `entregador` (decisión de negocio validada).

### 7.4 `POST /api/v1/tarreo/` — venta en la calle

**Estado: implementado** (`mockups/api/tarreo.py`, probado en `TarreoApiTest`).

Referencia: `tarreo_pedido`.

```json
{
  "lineas": [
    {"balon_id": 2, "cantidad": 2},
    {"balon_id": 1, "cantidad": 1}
  ],
  "metodo_pago": "efectivo",
  "direccion_entrega": "Tarreo / venta directa en camión"
}
```

Reglas, iguales que en la web:

- `metodo_pago` es **obligatorio** (`efectivo` | `tarjeta` | `transferencia`).
- `direccion_entrega` vacío → por defecto `"Tarreo / venta directa en camión"`.
- Al menos una línea con `cantidad > 0`, si no → `400 validacion` (y **no** crear el pedido).
- Precio: **`balon.precio_domicilio`**; `precio_compra_unitario = balon.precio_compra`.
- `origen='tarreo'`, `registrador = entregador = request.user`, `estado='entregado'` inmediato.
- Al final `pedido.calcular_totales()`. **El total lo calcula el servidor, nunca el cliente.**
  **Nota de revisión — divergencia intencional.** La web **no** llama `calcular_totales()`: asigna
  `pedido.monto_total = total_monto` y guarda (`mockups/views/entregas.py:658`), dejando `ganancia_total` y
  `descuento_total` en 0. La API usará `calcular_totales()`, que además llena esos dos campos. Es una mejora y
  solo afecta al endpoint nuevo; conviene reflejarla también en la web en una tarea aparte.
- **Sin descuentos.** El camionero no está en `ROLES_CON_DESCUENTO`: si llega `descuento_unitario`
  en el payload, se **ignora** (misma regla que `DetallePedidoForm._configurar_campo_descuento()`).
- El `Idempotency-Key` reemplaza al `form_token` de sesión que usa la web.

Detalles que la web no necesita y la API sí fija:

- **La respuesta es `200` con `{"data": {"pedido": Pedido}}`**, igual que las cuatro acciones del §7.3: el
  contrato no define `201`, y el cliente ya sabe leer ese sobre.
- **Solo balones activos.** La web itera el catálogo vigente, así que un `balon_id` retirado o inexistente
  → `400 validacion` y **no** se crea nada.
- **Cantidades repetidas del mismo balón se suman.** La web tiene un campo por balón; dos líneas del mismo
  balón solo pueden venir de la app, y sumarlas es lo que el usuario quiso decir.
- **`metodo_pago` se valida contra el modelo** (`Pedido._meta.get_field('metodo_pago').choices`), no contra
  una lista copiada a mano: si se agrega una forma de pago, la validación la acepta sola.
- **Todo se crea en una transacción**, y la validación termina antes de abrirla: un payload a medias no deja
  media venta.
- **Los precios y descuentos que mande el cliente se ignoran, no se rechazan.** De cada línea se leen solo
  `balon_id` y `cantidad`; el resto del diccionario no se mira. Hay un test que lo fija
  (`precio_venta_unitario: 1` → sigue valiendo el del catálogo).
- **No se registra `AuditoriaAccion`** porque la vista web tampoco lo hace; queda la línea `PEDIDO_TARREO_API`
  en el logger `audit`. Si se decide auditarlo en base, va en los dos lados a la vez.

### 7.5 Consultas

| Endpoint | Referencia | Devuelve |
|---|---|---|
| `GET /resumen-hoy/` | `mis_entregas_camionero` | `{"entregas": 2, "monto": 86500, "kilos": 43, "pedidos": [Pedido]}` |
| `GET /balones/` | `get_balones_activos_ordenados()` | `[{"id", "nombre", "peso_neto_gas", "precio_domicilio"}]`. **Implementado (F4)**. Solo activos, y **sin** `precio_compra`: con el costo se reconstruye el margen |
| `GET /historial/?mes=YYYY-MM` | `camionero_historial` | totales del mes + filas por día (kilos, monto, entregas) |
| `GET /historial/<fecha>/` | `camionero_historial_dia` | detalle de un día |
| `GET /auth/csrf/` | — | `{"csrf_token": "..."}` (además siembra la cookie `csrftoken`). **Sin sesión** |
| `GET /auth/perfil/` | `perfil_view` | `{"usuario": Usuario, "csrf_token": "..."}` |
| `GET /version/` | — | `{"version_minima": "0.1.0", "url_apk": null}` |

Objeto `Usuario` (identidad, sin secretos):
`{"id", "username", "nombre", "rol", "rol_etiqueta", "totp_activo"}`. Nunca incluye `password`,
`totp_secret` ni permisos internos; hay un test que lo fija.

`/auth/perfil/` **no** filtra por rol (es el arranque de identidad, no un dato de negocio); los endpoints
de negocio sí exigen `['camionero']`. `/version/` sí exige sesión: sin ella cualquiera podría sondear la
ruta de distribución del APK.

**Nota de revisión — el APK no tiene hosting.** No existe la carpeta `apk/` ni una ruta que la sirva en
`gasmanager/urls.py`. Además Render usa *filesystem* efímero: un archivo subido al servicio se pierde en cada
*deploy*. Por eso hoy el endpoint devuelve **`url_apk: null`** en vez de una URL que daría 404: la app debe
tolerar `null` hasta que se defina el hosting (release de GitHub, S3 u otro) — §10.

---

## 8. Testing

- **Archivo nuevo:** `mockups/tests_api_movil.py`. **No** tocar los tests existentes.
- `django.test.TestCase` + `self.client` (ver `mockups/tests_logica_negocio.py` como referencia).
- **`Client(enforce_csrf_checks=True)`** es obligatorio para probar CSRF: el cliente de tests lo omite por
  defecto. Y `secure=True` para ejercitar la rama HTTPS de Django.
- Estado: **67 tests en `mockups/tests_api_movil.py`, todos en verde** (F1 + F2 + F4).
- Mínimo exigido antes de dar un endpoint por terminado:

| Caso | Se espera |
|---|---|
| Sin sesión | `401 no_autenticado` |
| Sesión de `telefonista` o `bodeguero` | `403 sin_permiso` (y `PERM_DENIED` auditado) |
| Cuerpo de ese `403` | Sobre del §5.2 (`servidor_ahora` + `error.codigo`), **no** el `{"error": "No autorizado"}` del helper |
| Camionero A intenta operar un pedido del camionero B | `403 sin_permiso` |
| `tomar` un pedido ya tomado | `409 no_disponible` |
| `entregar` un pedido `pendiente` | `409 conflicto_estado` |
| Mismo `Idempotency-Key` dos veces | Una sola escritura; la segunda devuelve la respuesta guardada |
| Sin `Idempotency-Key` en un mutador | `400 validacion` |
| `tarreo` con `descuento_unitario` en el payload | El descuento se ignora (`descuento_total = 0`) |
| `tarreo` sin `metodo_pago` | `400 validacion` |
| `tarreo` con un `balon_id` retirado o inexistente | `400 validacion` y **no** se crea el pedido |
| `balones` | Solo activos, y el cuerpo **no** trae `precio_compra` |
| Cada acción | Deja `HistorialEstadoPedido` y `AuditoriaAccion` |
| Petición insegura **sin** `Origin` ni `Referer` sobre HTTPS | `403` de Django, ajeno al sobre: la app **debe** mandar `Origin` |
| `Origin` fuera de `CSRF_TRUSTED_ORIGINS` | `403` |
| `GET /auth/csrf/` | `200` + cookie `csrftoken`; tanto el token del cuerpo como el de la cookie sirven en `X-CSRFToken` |
| Superar el límite por usuario de un endpoint | `429 demasiadas_peticiones` con `Retry-After`; otro usuario no se ve afectado |
| Caché caída en un endpoint limitado | `200` (falla abierto) y aviso `API_LIMITE_SIN_CACHE` en el logger `security` |
| Intento de login fallido | Ni la base (`AccessAttempt`) ni los logs guardan la contraseña |
| Listado con más pedidos | El número de consultas **no** crece (detector de N+1) |

Comando: `python manage.py test mockups.tests_api_movil -v 2`

---

## 9. Flujo de trabajo entre los dos repos

| Repo | Qué cambia | Cuándo |
|---|---|---|
| `gas_project` | El paquete `mockups/api/`, la línea en `gasmanager/urls.py`, la tabla de idempotencia (migración nueva) y `mockups/tests_api_movil.py` | F1, F2, F4 |
| `gas_facil_movil` | La capa `data/` de Flutter: reemplazar `DatosMock` por un repositorio que consuma `/api/v1/`. Las pantallas no cambian | F1 |

Orden recomendado: **primero la API, después el cliente.** Cada endpoint se prueba con
`python manage.py test` antes de que la app lo consuma.

**Deploy:** Render ejecuta `bash build.sh`, que ya corre `python manage.py migrate`. La API viaja en
el mismo servicio que la web, sin infraestructura nueva.

**Compatibilidad:** mientras la PWA siga en uso (decisión del dueño: conviven), `/api/v1/` **no se
rompe**. Si hiciera falta un cambio incompatible, se agrega `/api/v2/` y la app migra después.

---

## 10. Pendientes y decisiones abiertas

| # | Pendiente | Estado |
|---|---|---|
| 1 | **Spike de CSRF/`Origin`** (§6.1) | **RESUELTO** (2026-10-06). Django 5.1.3 exige `X-CSRFToken` **y** `Origin` (o `Referer`) en HTTPS. Se agregó `GET /auth/csrf/`. Fijado en `CsrfSpikeTest` |
| 2 | ¿Hay entorno de pruebas, o se valida en local con SQLite y se despliega directo a Render? | Abierto (Dueño). F1 se validó con SQLite en memoria |
| 3 | `minSdk`/versión de Android de los teléfonos: la app usa `minSdk 26` | Resuelto |
| 4 | La app **no** permite "tomar pedido" sin conexión. El resto de las acciones sí esperan en cola offline | Decidido |
| 5 | ¿Se agrega a la vista web el `select_for_update` que le falta a `/cancelar/`? (§7.3) | Abierto (Dueño). La API sí lo hará |
| 6 | Hosting del APK para `GET /version/` (§7.5) | Abierto. Mientras tanto el endpoint devuelve `url_apk: null` |
| 7 | Cómo se envuelve `require_roles_api` para emitir el sobre del §5.2 | **RESUELTO**: `@acceso_api` descarta su salida y conserva la auditoría |
| 8 | **Store compartido para el limitador.** `settings.CACHES` no está definido → LocMemCache por proceso: con más de una instancia de Render el límite se multiplica por instancia | Abierto (Dueño). No bloquea F1 (una sola instancia) |
| 9 | **`get_client_ip()` confía en `X-Forwarded-For`** (`utils/permisos.py:25`, y `AuditoriaAccion.registrar` hace lo mismo). Si el proxy no sobreescribe esa cabecera, se puede **envenenar la IP de auditoría**. Es un riesgo preexistente del proyecto, no del API; el limitador **no** la usa como clave | Abierto (Dueño). Verificar cómo la fija Render |
| 10 | **Oráculo de 2FA:** `202 requiere_2fa` solo se responde cuando la contraseña era correcta, así que confirma que la clave es válida. Decisión consciente (contrato del §6.1); si molesta, se debe responder siempre `202` para usuarios con 2FA activo | Decidido |
| 11 | **Llevar `calcular_totales()` a `tarreo_pedido` de la web** (§7.4): hoy la venta en la calle se guarda con `ganancia_total` y `descuento_total` en 0, así que el margen del tarreo no cuadra en los reportes | Abierto (Dueño). El API ya lo calcula; la web no cambia por ahora |
