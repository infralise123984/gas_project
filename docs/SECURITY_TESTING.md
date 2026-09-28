# 🔒 Guía de Pruebas de Seguridad - Gas Manager

## 📋 Checklist de Seguridad Rápido

Ejecutar estos comandos para verificar la seguridad:

```powershell
# 1. Checklist de seguridad de Django (producción)
python manage.py check --deploy

# 2. Ejecutar script de seguridad custom (standalone, NO es módulo de tests de Django)
python scripts/security_tests.py

# 3. Análisis estático de código (instalar primero)
pip install bandit
bandit -r mockups/ gasmanager/ -f txt -o reporte_seguridad.txt

# 4. Verificar dependencias vulnerables
pip install safety
safety check

# 5. Análisis de configuración Django
pip install django-security
python manage.py checksecurity
```

---

## 🛡️ Vulnerabilidades a Revisar

### 1. Configuración HTTPS (Alta Prioridad)
Agregar en `settings.py` para producción:

```python
# Solo cuando DEBUG=False y con HTTPS configurado
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000  # 1 año
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```

### 2. Rate Limiting en Login (Media Prioridad)
Instalar django-ratelimit:

```powershell
pip install django-ratelimit
```

Modificar `views.py`:
```python
from django_ratelimit.decorators import ratelimit

@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def login_view(request):
    ...
```

### 3. Logging de Intentos de Login Fallidos
Agregar en `login_view`:
```python
import logging
logger = logging.getLogger('security')

def login_view(request):
    if request.method == "POST":
        username = request.POST["username"]
        user = authenticate(...)
        if user is None:
            logger.warning(f"Login fallido para usuario: {username} desde IP: {request.META.get('REMOTE_ADDR')}")
```

### 4. Headers de Seguridad Adicionales
Agregar middleware o configurar en nginx/render:
```python
# En settings.py
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True  # Deprecado pero útil para navegadores viejos
X_FRAME_OPTIONS = 'DENY'
```

---

## 🧪 Pruebas Manuales de Seguridad

### A. Pruebas de Autenticación
1. ✅ Intentar acceder a `/pedidos/crear/` sin login → debe redirigir
2. ✅ Intentar login con contraseña incorrecta múltiples veces
3. ✅ Verificar que la sesión expira correctamente
4. ✅ Verificar logout efectivo (no poder navegar atrás)

### B. Pruebas de Autorización (IDOR)
1. ✅ Como telefonista, intentar acceder a `/auth/crear-usuario/`
2. ✅ Como camionero, intentar cambiar ID en URLs como `/pedidos/{id}/editar/`
3. ✅ Verificar que cada rol solo ve sus opciones de menú

### C. Pruebas de Inyección
1. ✅ En campos de búsqueda, probar: `' OR '1'='1`
2. ✅ En campos de texto, probar: `<script>alert(1)</script>`
3. ✅ En campos numéricos, probar: `../../etc/passwd`

### D. Pruebas CSRF
1. ✅ Inspeccionar que todos los forms tienen `{% csrf_token %}`
2. ✅ Intentar POST desde otro dominio (debe fallar)
3. ✅ Verificar que no hay vistas con `@csrf_exempt`

---

## 🔧 Herramientas de Pentesting

### OWASP ZAP (Gratuito)
```powershell
# Descargar de: https://www.zaproxy.org/
# Ejecutar escaneo pasivo contra tu aplicación local
```

### Burp Suite (Community Edition - Gratuito)
- Interceptar y modificar requests
- Probar manipulación de parámetros
- Repetir requests con payloads diferentes

### SQLMap (Para SQL Injection)
```powershell
# Solo usar en ambientes de prueba propios
sqlmap -u "http://localhost:8000/pedidos/consulta/?q=test" --batch --dbs
```

---

## 📊 Ejecutar Suite Completa

```powershell
# Script completo de seguridad
Write-Host "=== Verificando Seguridad de Gas Manager ===" -ForegroundColor Green

Write-Host "`n[1/5] Django Security Check..." -ForegroundColor Yellow
python manage.py check --deploy 2>&1 | Tee-Object -Variable djangoCheck

Write-Host "`n[2/5] Running Security Tests..." -ForegroundColor Yellow
python scripts/security_tests.py

Write-Host "`n[3/5] Bandit Static Analysis..." -ForegroundColor Yellow
if (Get-Command bandit -ErrorAction SilentlyContinue) {
    bandit -r mockups/ gasmanager/ -f txt -ll
} else {
    Write-Host "Instalar bandit: pip install bandit" -ForegroundColor Red
}

Write-Host "`n[4/5] Checking Dependencies..." -ForegroundColor Yellow
if (Get-Command safety -ErrorAction SilentlyContinue) {
    safety check
} else {
    Write-Host "Instalar safety: pip install safety" -ForegroundColor Red
}

Write-Host "`n[5/5] Complete!" -ForegroundColor Green
```

---

## 🚨 Vulnerabilidades Específicas Encontradas

| # | Vulnerabilidad | Archivo | Línea | Recomendación |
|---|---------------|---------|-------|---------------|
| 1 | `\|safe` en templates | reporte_sobres.html | 497+ | Usar json_script filter |
| 2 | Sin HTTPS headers | settings.py | - | Agregar configuración HTTPS |
| 3 | Sin rate limiting | views.py:159 | login_view | Usar django-ratelimit |
| 4 | Vista index pública | views.py:151 | - | Evaluar si necesita auth |

---

## ✅ Correcciones Recomendadas

### Fix para `|safe` en templates (XSS Prevention)
Cambiar de:
```django
labels: {{ chart_dias_labels|safe }},
```
A:
```django
{{ chart_dias_labels|json_script:"chart-labels" }}
<script>
    const labels = JSON.parse(document.getElementById('chart-labels').textContent);
</script>
```

### Fix para settings de producción
Ver sección "Configuración HTTPS" arriba.

---

## 📚 Referencias
- [Django Security Docs](https://docs.djangoproject.com/en/5.2/topics/security/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Django Deployment Checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
