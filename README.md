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
- Envío directo al registrar el pedido (evita perder notificaciones si Gunicorn recicla el worker)
- Limpieza automática de suscripciones expiradas (HTTP 404/410)

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
| Backend | Django 5.1.3 sobre Python 3.13 |
| Frontend | Bootstrap 5 + Bootstrap Icons (templates server-side) |
| Base de datos | MySQL 8 en local · PostgreSQL en Render · SQLite soportado para desarrollo |
| PWA | Service Worker + Web Push API |
| Notificaciones | pywebpush 2.2.0 (VAPID) |
| Autenticación | Sesiones Django + 2FA TOTP opcional (pyotp) + django-axes |
| Servidor prod | Gunicorn + WhiteNoise |
| Hosting | Render.com (build vía `build.sh`) |

---

## Instalación Local

### 1. Requisitos previos

| Requisito | Detalle |
|---|---|
| **Python 3.13** | Versión con la que se desarrolla el proyecto. Verificar con `python --version` |
| **Git** | Para clonar el repositorio |
| **MySQL 8** *(opcional)* | Solo si quieres la misma base de datos que en producción. Sin MySQL, usar la opción SQLite del paso 4 |
| **Túnel HTTPS** *(opcional)* | Solo para probar notificaciones push en un teléfono: el navegador exige HTTPS (o `localhost`) para registrar el Service Worker |

### 2. Clonar e instalar dependencias

```bash
# Clonar repositorio
git clone https://github.com/infralise123984/gas_project.git
cd gas_project

# Crear entorno virtual
python -m venv venv

# Activarlo
venv\Scripts\activate          # Windows (PowerShell / CMD)
# source venv/bin/activate     # Linux / macOS

# Instalar dependencias de desarrollo
pip install -r requirements-local.txt
```

> **Windows:** `requirements-local.txt` incluye `mysqlclient`, que necesita compilador C. Si falla, usa SQLite (paso 4) y comenta esa línea del archivo, o instala el binario ya compilado: `pip install mysqlclient --only-binary :all:`.

### 3. Crear el archivo `.env`

`gasmanager/settings.py` **no arranca** sin `SECRET_KEY`, `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS`: lanza `ValueError` a propósito, para evitar partir con configuración insegura.

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # Linux / macOS
```

Valores mínimos para levantar en local (el resto están comentados dentro de la plantilla):

```env
SECRET_KEY=<clave del paso siguiente>
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
```

Generar la `SECRET_KEY`:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 4. Configurar la base de datos

**Opción A — MySQL** (recomendada si ya tienes MySQL instalado):

```sql
CREATE DATABASE kimgas CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```env
DB_ENGINE=django.db.backends.mysql
DB_NAME=kimgas
DB_USER=root
DB_PASSWORD=tu_password
DB_HOST=127.0.0.1
DB_PORT=3306
```

**Opción B — SQLite** (la más rápida: no requiere instalar ni configurar ningún servidor):

```env
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3
# DB_USER, DB_PASSWORD, DB_HOST y DB_PORT se pueden dejar vacíos
```

> Si defines `DATABASE_URL`, **tiene prioridad** sobre todas las variables `DB_*` (así funciona Render, que la inyecta sola).

### 5. Migrar y cargar los datos mínimos

```bash
python manage.py migrate

# Usuarios de prueba para cada rol (telefonista_test, bodeguero_test, camionero_test, …)
# Contraseña: Contraseña@123 — solo funciona con DEBUG=True
python manage.py crear_usuarios

# Catálogo de balones con precios realistas
python manage.py create_test_balones

# Catálogo de sectores (a partir de la lista histórica de Pedido.SECTORES)
python manage.py importar_sectores_actuales

# Superusuario propio (alternativa interactiva a las variables SUPERUSER_*)
python manage.py createsuperuser
```

### 6. Levantar el servidor

```bash
python manage.py runserver
```

- Aplicación: <http://127.0.0.1:8000/>
- Panel de administración: <http://127.0.0.1:8000/admin/>

### 7. Problemas comunes

| Síntoma | Causa y solución |
|---|---|
| `ValueError: ¡SECRET_KEY no está definida!` (o `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`) | Falta la variable en `.env`. El archivo debe estar en la **raíz** del proyecto: `python-dotenv` lo carga automáticamente |
| `settings.DATABASES is improperly configured` | No definiste ni `DATABASE_URL` ni `DB_ENGINE`. Ver paso 4 |
| Falla `pip install` en Windows por `mysqlclient` | Usa SQLite (paso 4, opción B) o instala Visual C++ Build Tools. `mysqlclient` solo se necesita para MySQL |
| `ERROR 1045 Access denied` / `Unknown database` | Credenciales o nombre de base incorrectos en `.env` |
| La página carga sin estilos | Falta `python manage.py collectstatic` (solo aplica con `DEBUG=False`) |
| El login queda bloqueado y no deja entrar | `django-axes` bloquea tras 5 intentos fallidos (`AXES_FAILURE_LIMIT`). Esperar 1 hora (`AXES_COOLOFF_TIME`) o limpiar el bloqueo con `python manage.py axes_reset` |
| Las notificaciones push no se activan en el teléfono | El navegador exige HTTPS o `localhost` para el Service Worker. En iPhone se requiere iOS 16.4+ y la app **agregada a la pantalla de inicio** |

### 8. Comandos personalizados disponibles

| Comando | Para qué sirve |
|---|---|
| `python manage.py crear_usuarios` | Crea un usuario de prueba por rol (`*_test`). Solo con `DEBUG=True` |
| `python manage.py create_test_balones` | Carga/actualiza el catálogo de balones con precios realistas |
| `python manage.py importar_sectores_actuales` | Puebla el catálogo `Sector` desde la lista histórica (`--dry-run` para simular) |
| `python manage.py generar_datos_prueba` | Genera pedidos y sobres de prueba para reportes (`--dias 30`, `--limpiar`) |
| `python manage.py cerrar_sobres_automatico` | Cierra los sobres abiertos de días anteriores (tarea nocturna) |
| `python manage.py enviar_recordatorios` | Envía push recordando pedidos pendientes (pensado para cron) |
| `python manage.py limpiar_sobres` | Borra sobres de una fecha y tipo específicos (desarrollo) |
| `python manage.py create_superuser_env` | Crea el superusuario desde `SUPERUSER_*` (lo usa `build.sh` en Render) |

---

## Variables de entorno (`.env`)

La plantilla completa y comentada está en [`.env.example`](.env.example). Resumen:

| Variable | ¿Obligatoria? | Descripción |
|---|---|---|
| `SECRET_KEY` | **Sí** | Clave secreta de Django. Nunca versionarla ni compartirla |
| `DEBUG` | Recomendada | `True` en local. Si se omite, el default del código es `False` |
| `ALLOWED_HOSTS` | **Sí** | Separadas por coma y **sin** esquema: `localhost,127.0.0.1` |
| `CSRF_TRUSTED_ORIGINS` | **Sí** | Separadas por coma y **con** esquema: `http://localhost:8000` |
| `DATABASE_URL` | No | Si existe, manda sobre las `DB_*` (Render la inyecta sola) |
| `DB_ENGINE` | Sí, si no hay `DATABASE_URL` | `django.db.backends.mysql` o `django.db.backends.sqlite3` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Sí, si no hay `DATABASE_URL` | Datos de conexión. Con SQLite basta `DB_NAME=db.sqlite3` |
| `DB_HOST` / `DB_PORT` | No | Por defecto `127.0.0.1` y `3306` |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | No (solo push) | Claves de Web Push. Generar con `python scripts/generate_vapid.py`. Sin ellas la app funciona, pero no se envían notificaciones |
| `VAPID_ADMIN_EMAIL` | No | Contacto VAPID: `mailto:admin@kimgas.cl` |
| `SUPERUSER_USERNAME` / `SUPERUSER_EMAIL` / `SUPERUSER_PASSWORD` | No | Solo despliegue: si están definidas, `build.sh` crea el superusuario |
| `PYTHONUNBUFFERED` | No | `1` para ver los logs de Gunicorn sin buffer en Render |

> **Seguridad:** el `.env` está en `.gitignore`. No subir credenciales reales al repositorio; en Render se configuran en *Environment*.

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
├── docs/                    # Documentación técnica (ver docs/README.md)
│   ├── PUSH_NOTIFICATIONS.md
│   ├── SECURITY_TESTING.md
│   ├── REPORTE_RENDIMIENTO.md
│   ├── CIERRE_AUTOMATICO_SOBRES.md
│   └── PLAN_AUDITORIA_OPERATIVA.md
│
├── scripts/                 # Utilidades y herramientas puntuales (ver scripts/README.md)
│   ├── generate_vapid.py
│   ├── security_tests.py
│   ├── debug_pedidos.py
│   ├── limpiar_sobres.py
│   └── test_crear_sobre.py
│
├── backups/                 # Respaldos SQL (no versionado)
├── logs/                    # Logs rotativos de la app (no versionado)
├── to_do_list.txt           # Lista de pendientes activa
│
├── manage.py
├── build.sh                 # Script de build (invocado por render.yaml)
├── Procfile                 # Comando de inicio
├── render.yaml              # Configuración Render.com (debe permanecer en la raíz)
├── requirements.txt
├── requirements-render.txt  # Dependencias producción (Render)
├── requirements-local.txt   # Dependencias desarrollo local
├── README.md
├── LICENSE
└── .gitignore
```

---

## Despliegue en Render.com

| Archivo | Rol |
|---|---|
| `render.yaml` | Define el servicio web y la base de datos. **Debe permanecer en la raíz del repositorio** (Render lo busca ahí) |
| `build.sh` | Instala `requirements-render.txt`, ejecuta `collectstatic`, aplica `migrate` y crea el superusuario si existen las variables `SUPERUSER_*` |
| `Procfile` | Comando de inicio: `gunicorn gasmanager.wsgi --log-file -` |
| `requirements-render.txt` | Dependencias de producción |

Variables a configurar en el dashboard de Render (*Environment*) — no en el repositorio:

```env
SECRET_KEY=<clave real>
DEBUG=False
ALLOWED_HOSTS=midominio.onrender.com,midominio.cl
CSRF_TRUSTED_ORIGINS=https://midominio.onrender.com
DATABASE_URL=<la inyecta Render al crear la base de datos>
VAPID_PUBLIC_KEY=<clave de producción>
VAPID_PRIVATE_KEY=<clave de producción>
VAPID_ADMIN_EMAIL=mailto:admin@kimgas.cl
SUPERUSER_USERNAME=<opcional>
SUPERUSER_EMAIL=<opcional>
SUPERUSER_PASSWORD=<opcional>
PYTHONUNBUFFERED=1
```

Notas:

- Las migraciones se aplican solas en cada deploy, porque `build.sh` ejecuta `manage.py migrate`.
- `render.yaml` apunta a la rama `develop`: si se despliega desde otra rama, actualizarla ahí.
- **Nunca** subir el `.env` ni el `.env.render` con credenciales reales al repositorio.

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

Script de seguridad complementario (standalone, **no** es un módulo de tests de Django): `python scripts/security_tests.py`. Ver [`docs/SECURITY_TESTING.md`](docs/SECURITY_TESTING.md).

---

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/README.md`](docs/README.md) | Índice de toda la documentación técnica |
| [`docs/PUSH_NOTIFICATIONS.md`](docs/PUSH_NOTIFICATIONS.md) | Web Push: suscripciones, service worker, diagnóstico |
| [`docs/SECURITY_TESTING.md`](docs/SECURITY_TESTING.md) | Checklist de seguridad y comandos de verificación |
| [`docs/REPORTE_RENDIMIENTO.md`](docs/REPORTE_RENDIMIENTO.md) | Hallazgos de rendimiento priorizados |
| [`docs/CIERRE_AUTOMATICO_SOBRES.md`](docs/CIERRE_AUTOMATICO_SOBRES.md) | Reglas del cierre automático nocturno de sobres |
| [`docs/PLAN_AUDITORIA_OPERATIVA.md`](docs/PLAN_AUDITORIA_OPERATIVA.md) | Plan pendiente de auditoría operativa y alertas de cancelación |
| [`scripts/README.md`](scripts/README.md) | Inventario de scripts y utilidades |

---

## Licencia

Copyright © 2026 GasFácil. Todos los derechos reservados.

Este software es propietario y confidencial. Ver archivo [LICENSE](LICENSE) para más detalles.
