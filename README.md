# GasFácil - Sistema de Gestión para Distribuidores de Gas

Sistema web progresivo (PWA) para la gestión integral de pedidos, entregas, inventario y reportes de un distribuidor de **gas licuado (GLP)**.

Reemplaza planillas de Excel y grupos de WhatsApp por una solución moderna, segura y optimizada para uso móvil. El nombre *GasFácil* es un placeholder - actualmente implementado para **KIM GAS Rancagua**.

---


## Funcionalidades Implementadas

### Gestión de Usuarios y Roles
- **5 roles diferenciados**: Telefonista, Bodeguero, Camionero, Jefe y Administrador
- Cada rol tiene acceso solo a las vistas que necesita
- Creación de usuarios desde panel administrativo

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
- Log de todas las acciones críticas (crear, editar, eliminar)
- Registro de quién hizo qué y cuándo
- Tests de seguridad incluidos

---

## Tecnologías

| Componente | Tecnología |
|------------|------------|
| Backend | Django 5.1 (Python 3.12) |
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

# Push Notifications (generar con: python generate_vapid.py)
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_ADMIN_EMAIL=mailto:admin@tudominio.com
```

---

## Estructura del Proyecto

```
gas_project/
├── gasmanager/          # Configuración Django (settings, urls)
├── mockups/             # App principal
│   ├── models.py        # Usuario, Pedido, TipoBalon, SobreDiario, etc.
│   ├── views.py         # Vistas por rol
│   ├── forms.py         # Formularios
│   ├── push_notifications.py  # Sistema de notificaciones
│   ├── templates/       # HTML (Bootstrap 5)
│   ├── static/          # CSS, JS, manifest.json, sw.js
│   └── management/      # Comandos personalizados
├── requirements.txt
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

## Licencia

Copyright © 2026 GasFácil. Todos los derechos reservados.

Este software es propietario y confidencial. Ver archivo [LICENSE](LICENSE) para más detalles.
