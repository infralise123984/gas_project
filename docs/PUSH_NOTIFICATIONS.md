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
- `mockups/models.py` - Agregado modelo `PushSubscription`
- `mockups/views.py` - Vistas para subscribe/unsubscribe y envío en transaccional_pedido
- `mockups/admin.py` - Admin para gestionar suscripciones
- `gasmanager/urls.py` - URLs para push API
- `gasmanager/settings.py` - Configuración VAPID
- `mockups/templates/base.html` - Carga del JS y Service Worker
- `mockups/templates/camionero_entregas.html` - Botón para activar notificaciones
- `mockups/static/manifest.json` - Permisos de notificación
- `requirements.txt` - Agregado pywebpush

## Configuración

### Variables de entorno (.env)

Agregar estas variables a tu archivo `.env`:

```bash
# Claves VAPID para Web Push (generadas con scripts/generate_vapid.py)
VAPID_PUBLIC_KEY=BB0yr5YaM0f0zscZ96WzfrUzyRstuxb339lkwKMXFDSZiJSaMvKn_c53YJSUKF7DjLouGgpLxARF-gLky3zbFp8
VAPID_PRIVATE_KEY=yezbxI6LntId1PZcWqOXxLbM2sX039plDZQaUwyGM5M
VAPID_ADMIN_EMAIL=mailto:admin@kimgas.cl
```

**IMPORTANTE**: Para producción, genera nuevas claves VAPID con:
```bash
python scripts/generate_vapid.py
```

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
