# Scripts y utilidades

Herramientas puntuales que **no** son parte de la aplicación: no las importa ninguna vista ni template. Se sacaron de la raíz para que el árbol del proyecto se lea de un vistazo.

## Regla general

> Si un script se usa **más de una vez**, debe convertirse en un **management command** en `mockups/management/commands/`. Así se ejecuta con `python manage.py <nombre>`, tiene `--help`, argumentos validados y mensajes consistentes.

Los comandos ya existentes con esa forma están en `mockups/management/commands/`:
`cerrar_sobres_automatico`, `limpiar_sobres`, `debug_pedidos`, `enviar_recordatorios`, `crear_usuarios`, `create_superuser_env`, `create_test_balones`, `generar_datos_prueba`, `importar_sectores_actuales`.

---

## Inventario

| Script | Qué hace | Cómo ejecutarlo | Estado |
|---|---|---|---|
| `generate_vapid.py` | Genera un par de claves VAPID para Web Push y las imprime en base64url listas para pegar en `.env` | `python scripts/generate_vapid.py` | **Activo**. Requiere `py-vapid` y `cryptography` |
| `security_tests.py` | Pruebas de seguridad contra la app (permisos, aislamiento entre usuarios) | `python scripts/security_tests.py` | **Incompleto**: la prueba de IDOR está como placeholder (`pass`), ver pendientes en `docs/SECURITY_TESTING.md` |
| `debug_pedidos.py` | Imprime un diagnóstico de por qué un pedido no aparece en el sobre de bodega (conteos, fechas, estados) | `python manage.py shell < scripts/debug_pedidos.py` | **Duplicado** de `python manage.py debug_pedidos` → candidato a eliminar |
| `test_crear_sobre.py` | Crea un sobre de bodega de prueba para la fecha de hoy | `python manage.py shell < scripts/test_crear_sobre.py` | **Duplicado** parcial de `python manage.py crear_datos_prueba` → candidato a eliminar |
| `limpiar_sobres.py` | ⚠️ **DESTRUCTIVO**: borra **todos** los sobres, líneas, pagos y gastos de la base de datos | `python scripts/limpiar_sobres.py` | **Peligroso**. `python manage.py limpiar_sobres` hace lo mismo pero acotado a una fecha y tipo → candidato a eliminar |

---

## Notas de ejecución

- Scripts que llaman `django.setup()` por su cuenta (`generate_vapid.py`, `security_tests.py`, `limpiar_sobres.py`) se ejecutan directo con `python`.
- Scripts que asumen el contexto de Django ya cargado (`debug_pedidos.py`, `test_crear_sobre.py`) se ejecutan con `python manage.py shell < archivo.py`. Si los ejecutas con `python archivo.py` va a fallar por `settings` no configurados.
- `generate_vapid.py` escribe `private_key.pem` y `public_key.pem` en el **directorio actual**, no en `scripts/`. Ejecútalo desde la raíz (así el `.gitignore` los cubre con `*.pem`) y bórralos después: las claves que importan son las que se pegan en `.env` y en las variables de entorno de Render.
- Ejecutar `limpiar_sobres.py` sin querer puede dejar la contabilidad del día en cero. Si se decide conservarlo, la recomendación es dejarlo con un `--confirmo` explícito.
