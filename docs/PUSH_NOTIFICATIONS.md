# Push Notifications - GasFácil

## Descripción

Sistema de notificaciones push para alertar a los camioneros sobre:
- **Nuevos pedidos** - Cuando un telefonista crea un pedido de domicilio
- **Recordatorios** - Pedidos pendientes de más de 30 minutos (vía comando programado)

Las notificaciones funcionan incluso con la app cerrada gracias a Web Push API y Service Workers.

## Archivos Creados/Modificados

### Nuevos archivos:
- `mockups/static/sw.js` - Service Worker para recibir push en segundo plano
- `mockups/static/js/push-notifications.js` - Módulo JS para suscripciones push
- `mockups/push_notifications.py` - Lógica de envío de notificaciones
- `mockups/context_processors.py` - Inyecta VAPID_PUBLIC_KEY a templates
- `mockups/management/commands/enviar_recordatorios.py` - Comando para recordatorios

### Archivos modificados:
- `mockups/models.py` - Modelo `PushSubscription`
- `mockups/views/push.py` - Vistas `push_subscribe`, `push_unsubscribe`, `push_status` y `push_test`
- `mockups/views/pedidos.py` - Dispara `notificar_nuevo_pedido()` al registrar un pedido de teléfono
- `mockups/admin.py` - Admin para gestionar suscripciones
- `gasmanager/urls.py` - URLs de la API push
- `gasmanager/settings.py` - Lectura de las claves VAPID desde variables de entorno
- `mockups/templates/base.html` - Carga del JS y registro del Service Worker
- `mockups/templates/entregas/camionero_entregas.html` - Botón para activar notificaciones
- `mockups/static/manifest.json` - Permisos de notificación
- `requirements.txt` (y `requirements-local.txt` / `requirements-render.txt`) - `pywebpush`

## Configuración

### Variables de entorno (.env)

Web Push necesita un par de claves **VAPID** (una pública y una privada), que se
generan una vez por instalación. Cada entorno (local y producción) debe tener su
**propio** par, y las claves no se comparten ni se versionan:

```env
# Ver "Cómo generar las claves VAPID" más abajo
VAPID_PUBLIC_KEY=<clave pública en base64url>
VAPID_PRIVATE_KEY=<clave privada en base64url>
# Solo un contacto informativo que exige el protocolo VAPID: no necesita ser un buzón real.
VAPID_ADMIN_EMAIL=mailto:admin@tudominio.cl
```

> Las claves se leen de variables de entorno. Si `VAPID_PUBLIC_KEY` o
> `VAPID_PRIVATE_KEY` están vacías, la aplicación funciona con normalidad pero
> **no se envían notificaciones**: `send_push_notification()` responde
> `'Claves VAPID no configuradas'`.
>
> ⚠️ **Deuda de seguridad conocida (2026-09-16).** `gasmanager/settings.py` define
> además un **par por defecto hardcodeado** para `VAPID_PUBLIC_KEY` y
> `VAPID_PRIVATE_KEY`, y ese par quedó registrado en el historial de git. Hasta que
> se elimine ese valor por defecto **y** se rote el par, hay que asumir que la clave
> privada es pública: no usarla en entornos nuevos y tratar cualquier notificación
> inesperada en los teléfonos de los camioneros como un posible envío suplantado.

### Cómo generar las claves VAPID

```bash
# Desde la raíz del proyecto, con el entorno virtual activado
python scripts/generate_vapid.py
```

El script crea un par nuevo, lo imprime en formato base64url listo para pegar en
el `.env` y elimina los archivos `.pem` temporales. Salida esperada (los datos de
abajo son un **ejemplo de formato**, no claves utilizables):

```
============================================================
CLAVES VAPID GENERADAS - AGREGAR A .env
============================================================

VAPID_PUBLIC_KEY=<cadena base64url de 87 caracteres>
VAPID_PRIVATE_KEY=<cadena base64url de 43 caracteres>
VAPID_ADMIN_EMAIL=mailto:admin@kimgas.cl
============================================================
```

Pegar el par completo en el `.env` local y en las variables de entorno de Render
(producción). `py-vapid` ya viene instalado como dependencia de `pywebpush`: no
hay que instalar nada extra.

**Reglas de manejo:**

| Regla | Motivo |
|---|---|
| Nunca escribirlas en documentación, issues, capturas, chats ni commits | Quien tenga el par puede enviar notificaciones en nombre de la aplicación |
| Un par distinto por entorno | Si se filtra el de desarrollo, producción no queda comprometida |
| La clave pública se expone al navegador; la privada **jamás** | El frontend necesita la pública para poder suscribirse |
| Al rotarlas, **todas las suscripciones existentes quedan inválidas** | Cada camionero debe volver a activar las notificaciones desde la app |

### Migración de base de datos

Después de deployar, ejecutar:
```bash
python manage.py migrate
```

## Uso

### Para el camionero:

1. Abrir la página de "Entregas Pendientes"
2. Hacer click en "Activar Notificaciones"
3. Aceptar el permiso del navegador
4. ¡Listo! Recibirá notificaciones de nuevos pedidos

### Recordatorios automáticos

Para enviar recordatorios de pedidos pendientes, configurar un cron job:

```bash
# Cada 30 minutos
*/30 * * * * cd /ruta/a/gas_project && python manage.py enviar_recordatorios
```

En **Render**, puedes usar un Cron Job o un servicio de Background Workers.

## API Endpoints

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/push/subscribe/` | POST | Suscribirse a notificaciones |
| `/push/unsubscribe/` | POST | Cancelar suscripción |
| `/push/test/` | POST | Enviar notificación de prueba |
| `/push/status/` | GET | Verificar estado de suscripciones |

## Requisitos del navegador

Las notificaciones push funcionan en:
- ✅ Chrome (Android y Desktop)
- ✅ Firefox (Android y Desktop)
- ✅ Edge (Desktop)
- ✅ Safari 16+ (macOS Ventura+, iOS 16.4+)
- ⚠️ iOS Safari requiere que la PWA esté instalada en la pantalla de inicio

## Troubleshooting

### Las notificaciones no llegan

1. Verificar que el navegador soporta Web Push
2. Verificar que los permisos de notificación están concedidos
3. Revisar que hay suscripciones activas en `/admin/mockups/pushsubscription/`
4. Revisar logs de auditoría para errores de envío

### Error "subscription_expired"

Las suscripciones push pueden expirar. El sistema las desactiva automáticamente.
El usuario debe volver a activar las notificaciones.

### Notificaciones en iOS

Para iOS, el usuario debe:
1. Instalar la PWA (agregar a pantalla de inicio)
2. Activar notificaciones desde la app instalada
3. Requiere iOS 16.4 o superior

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                         SERVIDOR (Django)                        │
├─────────────────────────────────────────────────────────────────┤
│  views.py                    push_notifications.py               │
│  ├─ transaccional_pedido()   ├─ notificar_nuevo_pedido()        │
│  │   └─ Al crear pedido      │   └─ Envía a todos los camioneros│
│  │       llama a →           │                                   │
│  │                           ├─ notificar_recordatorio_pendientes│
│  │                           │   └─ Via comando de Django        │
│  └─ push_subscribe()         │                                   │
│      push_unsubscribe()      └─ send_push_notification()        │
│      push_status()               └─ Usa pywebpush + VAPID       │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                        Push Service (FCM/APNS)
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CLIENTE (Navegador)                         │
├─────────────────────────────────────────────────────────────────┤
│  sw.js (Service Worker)       push-notifications.js              │
│  ├─ Recibe push event         ├─ subscribeToPush()               │
│  ├─ Muestra notificación      ├─ unsubscribeFromPush()           │
│  └─ Maneja clicks             └─ Actualiza UI del botón          │
│                                                                   │
│  (Funciona con app cerrada)                                       │
└─────────────────────────────────────────────────────────────────┘
```

## Seguridad

- Solo los camioneros pueden suscribirse (validado en backend)
- Las claves VAPID deben mantenerse privadas
- Las suscripciones expiradas se desactivan automáticamente
- Todas las acciones se registran en auditoría
- ⚠️ **Pendiente:** el par VAPID por defecto hardcodeado en `gasmanager/settings.py` está expuesto en el historial de git (ver la nota en "Variables de entorno"). Requiere eliminar el default y rotar el par; ambas acciones quedaron **decididas como no urgentes** el 2026-09-16.
