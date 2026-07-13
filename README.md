# GasFácil - Sistema de Gestión para Distribuidores de Gas

Sistema web progresivo (PWA) para la gestión integral de pedidos, entregas, inventario y reportes de un distribuidor de **gas licuado (GLP)**.

Reemplaza planillas de Excel y grupos de WhatsApp por una solución moderna, segura y optimizada para uso móvil. El nombre *GasFácil* es un placeholder - actualmente implementado para **KIM GAS Rancagua**.

---


## Funcionalidades Implementadas

### Gestión de Usuarios y Roles
- **5 roles diferenciados**: Telefonista, Bodeguero, Camionero, Jefe y Administrador
- Cada rol accede solo a las vistas autorizadas mediante `require_roles()` (HTML) y `require_roles_api()` (JSON)
- Auditoría automática de accesos denegados (`AuditoriaAccion`)
- Creación de usuarios desde panel administrativo
- Autenticación con 2FA (TOTP) opcional

### Pedidos y Ventas
- **Telefonista**: Registro de pedidos a domicilio con dirección, sector y múltiples balones
- **Bodeguero**: Ventas en local (mostrador)
- **Camionero**: Ventas en ruta (tarreo) con interfaz táctil optimizada
- Cálculo automático de montos según tipo de balón y cantidad
- Historial completo de cambios de estado

### Vista Camionero (PWA)
- Interfaz tipo app móvil, instalable en Android
- Lista de entregas pendientes y en ruta
- Botones grandes para tomar pedido, marcar en ruta y entregado
- **Venta Tarreo**: Registro rápido de ventas en la calle con UI accesible para personas mayores
- Notificaciones push cuando hay nuevos pedidos

### Sistema de Sobres Diarios
- Control de caja diario por trabajador
- Registro de líneas de venta, gastos y pagos
- Cierre de sobre con validación de cuadre
- Historial y reportes de sobres

### Notificaciones Push
- Notificaciones en tiempo real a camioneros
- Funciona incluso con la app cerrada (Service Worker)
- Envío asíncrono para no bloquear la interfaz del telefonista

### Reportes y Consultas
- Reporte de ventas con gráficos (pie chart de balones más vendidos)
- Consultas avanzadas de pedidos con filtros y paginación
- Historial de precios de balones
- Exportación a Excel (XLSX)

### Gestión de Balones y Precios
- Catálogo de tipos de balón (5kg, 11kg, 15kg, 45kg, catalítico, aluminio)
- Precios diferenciados: local vs domicilio
- Historial automático de cambios de precio

### Auditoría y Seguridad
- Validación unificada de roles: `require_roles()` (vistas HTML) y `require_roles_api()` (endpoints JSON/AJAX)
- Registro automático de accesos denegados (`PERM_DENIED`) en `AuditoriaAccion` y `security_logger`
- Log de todas las acciones críticas (crear, editar, eliminar, login, logout)
- Protección anti-forgery CSRF, rotación de sesión en 2FA
- Rate-limiting de intentos de login (django-axes)

---

## Tecnologías

| Componente | Tecnología |
|------------|------------|
| Backend | Django 5.1 (Python 3.13) |
| Frontend | Bootstrap 5 + Bootstrap Icons |
| Base de datos | MySQL (dev) / PostgreSQL (prod) |
| PWA | Service Worker + Web Push API |
| Notificaciones | pywebpush (VAPID) |
| Servidor prod | Gunicorn + WhiteNoise |
| Hosting | Render.com |

---

## Instalación Local

```bash
# Clonar repositorio
git clone https://github.com/infralise123984/gas_project.git
cd gas_project

# Crear entorno virtual
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# Migraciones
python manage.py migrate

# Crear superusuario
python manage.py createsuperuser

# Ejecutar servidor
python manage.py runserver
```

---

## Variables de Entorno (.env)

```env
SECRET_KEY=tu_clave_secreta
DEBUG=True
DATABASE_URL=mysql://user:pass@localhost:3306/gasfacil

# Push Notifications (generar con: python scripts/generate_vapid.py)
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_ADMIN_EMAIL=mailto:admin@tudominio.com
```

---

## Arquitectura Modular

El proyecto fue refactorizado de un `views.py` monolítico (4400+ líneas) a una arquitectura modular para facilitar el mantenimiento:

```
gas_project/
├── gasmanager/              # Configuración Django
│   ├── settings.py
│   ├── urls.py              # Rutas (usa from mockups import views)
│   └── wsgi.py
├── mockups/                 # App principal
│   ├── models.py            # Usuario, Pedido, TipoBalon, SobreDiario, etc.
│   ├── forms.py             # Formularios
│   ├── push_notifications.py
│   │
│   ├── views/               # Vistas HTTP (modular)
│   │   ├── __init__.py      # Re-exporta todo para retrocompatibilidad
│   │   ├── auth.py          # Login, logout, perfil, 2FA, crear usuario
│   │   ├── pedidos.py       # Crear, editar, cancelar, consultar pedidos
│   │   ├── entregas.py      # Vistas del camionero (ruta, entregas, tarreo)
│   │   ├── sobres.py        # Sobres diarios, cierre de caja
│   │   ├── catalogos.py     # Balones, sectores, precios, auditoría
│   │   ├── reportes.py      # Reportes de ventas y sobres
│   │   └── push.py          # Suscripciones web push + service worker
│   │
│   ├── services/            # Lógica de negocio reutilizable
│   │   ├── camionero.py     # Querysets, stats y kilos del camionero
│   │   ├── sobres.py        # Sincronización de sobres desde pedidos
│   │   ├── catalogos.py     # Consultas de balones activos ordenados
│   │   └── exports.py       # Exportación a Excel (.xlsx)
│   │
│   ├── utils/               # Utilidades compartidas
│   │   ├── fechas.py        # Zona horaria Chile, rangos UTC, parseo de meses
│   │   └── permisos.py      # require_roles(), require_roles_api(), IP, display
│   │
│   ├── templates/           # HTML (Bootstrap 5)
│   ├── static/              # CSS, JS, manifest.json, sw.js
│   ├── management/          # Comandos personalizados
│   ├── migrations/          # Migraciones de base de datos
│   │
│   ├── tests.py                      # Tests de flujo 2FA
│   ├── tests_logica_negocio.py       # Tests de lógica y matemática
│   └── views_monolith_backup.py      # Backup del monolito original (referencia)
│
├── requirements.txt
├── requirements-render.txt  # Dependencias producción (Render)
├── render.yaml              # Configuración Render.com
├── build.sh                 # Script de build
├── Procfile                 # Comando de inicio
└── manage.py
```

---

## Despliegue en Render.com

El proyecto está configurado para Render con:
- `render.yaml` - Configuración de servicios
- `build.sh` - Script de construcción
- `Procfile` - Comando de inicio
- `requirements-render.txt` - Dependencias de producción

---

## Tests

```bash
# Todos los tests
python manage.py test mockups -v 2

# Solo lógica de negocio
python manage.py test mockups.tests_logica_negocio -v 2

# Solo flujo 2FA
python manage.py test mockups.tests -v 2
```

Los tests cubren:
- Totales de pedidos (monto, ganancia, kilos)
- Sincronización de sobres (cuadre de cantidades y montos)
- Flujo completo de autenticación con 2FA (TOTP)
- Bloqueo tras 5 intentos fallidos
- Coherencia de fechas Chile/UTC

---

## Licencia

Copyright © 2026 GasFácil. Todos los derechos reservados.

Este software es propietario y confidencial. Ver archivo [LICENSE](LICENSE) para más detalles.
