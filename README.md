# GasFácil

Sistema web para la gestión eficiente de pedidos, entregas, precios y reportes de un distribuidor de **gas licuado (GLP)**.  
Reemplaza planillas de Excel y grupos de WhatsApp por una solución simple, segura y móvil-friendly. actualmente el nombre *GasFáci* es solamente un placeholder

## Estado actual (rama main v2)
- Autenticación con roles: Telefonista, Bodeguero, Camionero, Jefe y Administrador  
- Registro de pedidos a domicilio (telefonista) y ventas en local (bodeguero)  
- Cálculo automático de monto según tipo de balón y cantidad  
- Selección de sector y captura de dirección exacta (solo telefonistas)  
- Vista de precios y disponibilidad de balones con historial automático de cambios  
- Reporte de ventas con gráficos (pie chart de balones más vendidos) y tablas detalladas  
- Consultas avanzadas de pedidos con filtros y paginación  
- Interfaz responsiva con Bootstrap 5 (optimizada para móviles)  
- Configuración de entorno con `.env` y preparación para despliegue en **Render.com**

**Próximos pasos en testing**  
- Vista completa del camionero (lista de pedidos pendientes/en ruta, botones para tomar y marcar entregado, diseño tipo app móvil)  
- Seguimiento de metas mensuales en el reporte de ventas (progreso, alertas para no exceder)  
- Exportación de reportes a CSV/PDF  
- Gráficos interactivos adicionales (ventas diarias, por sector, por trabajador)

## Tecnologías utilizadas
- **Backend**: Django 5.2 (Python 3.12)  
- **Frontend**: Bootstrap 5 + Bootstrap Icons  
- **Base de datos**: MySQL (desarrollo) / PostgreSQL (producción)  
- **Gestión de entorno**: python-dotenv  
- **Despliegue**: Preparado para Render.com (Web Service + PostgreSQL)

## Requisitos
- Python 3.12+  
- MySQL (o PostgreSQL)  
- `pip install -r requirements.txt`
