"""
Pruebas de Seguridad - Gas Manager
Ejecutar: python manage.py test security_tests --keepdb

Para pruebas más completas instalar:
    pip install bandit safety django-security-check

Comandos adicionales:
    bandit -r mockups/ gasmanager/ -f txt       # Análisis estático de código
    safety check                                 # Vulnerabilidades en dependencias
    python manage.py check --deploy             # Checklist de seguridad Django
"""

import os
import sys
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gasmanager.settings')
django.setup()

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.conf import settings

User = get_user_model()


class SecurityConfigTests(TestCase):
    """Pruebas de configuración de seguridad"""
    
    def test_debug_is_false_in_production(self):
        """DEBUG debe estar en False en producción"""
        # En producción esto debe fallar si DEBUG=True
        if os.environ.get('ENVIRONMENT') == 'production':
            self.assertFalse(settings.DEBUG, "DEBUG debe ser False en producción")
    
    def test_secret_key_is_set(self):
        """SECRET_KEY debe estar configurada y no ser la default"""
        self.assertIsNotNone(settings.SECRET_KEY)
        self.assertNotIn('insecure', settings.SECRET_KEY.lower())
        self.assertGreater(len(settings.SECRET_KEY), 40, "SECRET_KEY muy corta")
    
    def test_allowed_hosts_configured(self):
        """ALLOWED_HOSTS debe tener valores"""
        self.assertTrue(len(settings.ALLOWED_HOSTS) > 0)
        self.assertNotIn('*', settings.ALLOWED_HOSTS, "Evitar * en ALLOWED_HOSTS")
    
    def test_csrf_middleware_enabled(self):
        """CSRF middleware debe estar activo"""
        self.assertIn(
            'django.middleware.csrf.CsrfViewMiddleware',
            settings.MIDDLEWARE
        )
    
    def test_security_middleware_enabled(self):
        """SecurityMiddleware debe estar activo"""
        self.assertIn(
            'django.middleware.security.SecurityMiddleware',
            settings.MIDDLEWARE
        )
    
    def test_clickjacking_protection(self):
        """Protección contra Clickjacking debe estar activa"""
        self.assertIn(
            'django.middleware.clickjacking.XFrameOptionsMiddleware',
            settings.MIDDLEWARE
        )
    
    def test_password_validators_configured(self):
        """Validadores de contraseña deben estar configurados"""
        validators = settings.AUTH_PASSWORD_VALIDATORS
        self.assertTrue(len(validators) >= 4)
    
    def test_session_cookie_secure(self):
        """Cookie de sesión debe usar Secure en HTTPS"""
        if not settings.DEBUG:
            secure = getattr(settings, 'SESSION_COOKIE_SECURE', False)
            self.assertTrue(secure, "SESSION_COOKIE_SECURE debe ser True")
    
    def test_csrf_cookie_secure(self):
        """Cookie CSRF debe usar Secure en HTTPS"""
        if not settings.DEBUG:
            secure = getattr(settings, 'CSRF_COOKIE_SECURE', False)
            self.assertTrue(secure, "CSRF_COOKIE_SECURE debe ser True")


class AuthenticationSecurityTests(TestCase):
    """Pruebas de seguridad de autenticación"""
    
    def setUp(self):
        self.client = Client()
        # Crear usuario de prueba
        self.test_user = User.objects.create_user(
            username='testuser',
            password='TestPass123!',
            rol='telefonista'
        )
    
    def test_login_with_wrong_password(self):
        """Login con contraseña incorrecta debe fallar"""
        response = self.client.post(reverse('auth_login'), {
            'username': 'testuser',
            'password': 'wrongpassword'
        })
        # No debe redirigir exitosamente
        self.assertNotEqual(response.status_code, 302)
        # O si redirige, debe volver al login
        if response.status_code == 302:
            self.assertIn('login', response.url)
    
    def test_login_with_nonexistent_user(self):
        """Login con usuario inexistente debe fallar"""
        response = self.client.post(reverse('auth_login'), {
            'username': 'noexiste',
            'password': 'anypassword'
        })
        self.assertEqual(response.status_code, 200)  # Muestra login de nuevo
    
    def test_logout_clears_session(self):
        """Logout debe eliminar la sesión"""
        self.client.login(username='testuser', password='TestPass123!')
        self.client.get(reverse('auth_logout'))
        # Verificar que no hay usuario autenticado
        response = self.client.get(reverse('pedidos_crear'))
        self.assertEqual(response.status_code, 302)  # Redirige a login
    
    def test_protected_views_require_login(self):
        """Vistas protegidas deben requerir autenticación"""
        protected_urls = [
            'pedidos_crear',
            'pedidos_consulta',
            'precios_lista',
            'sobres_lista',
            'reportes_ventas',
        ]
        for url_name in protected_urls:
            response = self.client.get(reverse(url_name))
            self.assertEqual(
                response.status_code, 302,
                f"Vista {url_name} debe requerir login"
            )


class CSRFProtectionTests(TestCase):
    """Pruebas de protección CSRF"""
    
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username='csrftest',
            password='TestPass123!',
            rol='admin'
        )
    
    def test_post_without_csrf_fails(self):
        """POST sin token CSRF debe fallar"""
        self.client.login(username='csrftest', password='TestPass123!')
        # Intentar POST sin token CSRF
        response = self.client.post(reverse('auth_crear_usuario'), {
            'username': 'newuser',
            'password1': 'testpass123',
            'password2': 'testpass123',
            'rol': 'telefonista'
        })
        self.assertEqual(response.status_code, 403)


class AuthorizationTests(TestCase):
    """Pruebas de autorización por roles"""
    
    def setUp(self):
        self.client = Client()
        # Crear usuarios con diferentes roles
        self.telefonista = User.objects.create_user(
            username='telefonista1',
            password='TestPass123!',
            rol='telefonista'
        )
        self.camionero = User.objects.create_user(
            username='camionero1',
            password='TestPass123!',
            rol='camionero'
        )
        self.admin = User.objects.create_user(
            username='admin1',
            password='TestPass123!',
            rol='admin'
        )
    
    def test_telefonista_cannot_create_users(self):
        """Telefonista no debe poder crear usuarios"""
        self.client.login(username='telefonista1', password='TestPass123!')
        response = self.client.get(reverse('auth_crear_usuario'))
        # Debe redirigir (sin permiso)
        self.assertEqual(response.status_code, 302)
    
    def test_admin_can_create_users(self):
        """Admin debe poder crear usuarios"""
        self.client.login(username='admin1', password='TestPass123!')
        response = self.client.get(reverse('auth_crear_usuario'))
        self.assertEqual(response.status_code, 200)

    def test_admin_must_confirm_own_password_to_create_user(self):
        """Crear usuario exige confirmar la contraseña actual del admin"""
        self.client.login(username='admin1', password='TestPass123!')
        response = self.client.post(reverse('auth_crear_usuario'), {
            'username': 'nuevo_usuario',
            'first_name': 'Nuevo',
            'last_name': 'Usuario',
            'telefono': '+56911111111',
            'rol': 'telefonista',
            'password1': 'ClaveSegura123!',
            'password2': 'ClaveSegura123!',
            'admin_password': 'incorrecta',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='nuevo_usuario').exists())
        self.assertContains(response, 'La contraseña actual no es correcta.')

    def test_admin_can_create_user_with_password_confirmation(self):
        """Admin puede crear usuario si confirma su contraseña actual"""
        self.client.login(username='admin1', password='TestPass123!')
        response = self.client.post(reverse('auth_crear_usuario'), {
            'username': 'nuevo_usuario_ok',
            'first_name': 'Nuevo',
            'last_name': 'Usuario',
            'telefono': '+56922222222',
            'rol': 'telefonista',
            'password1': 'ClaveSegura123!',
            'password2': 'ClaveSegura123!',
            'admin_password': 'TestPass123!',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='nuevo_usuario_ok', rol='telefonista').exists())
    
    def test_camionero_cannot_edit_prices(self):
        """Camionero no debe poder gestionar precios (solo jefe/admin/bodeguero)"""
        self.client.login(username='camionero1', password='TestPass123!')
        response = self.client.get(reverse('precios_lista'))
        # Debe redirigir por falta de permisos
        self.assertEqual(response.status_code, 302)


class SQLInjectionTests(TestCase):
    """Pruebas contra SQL Injection"""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='sqlitest',
            password='TestPass123!',
            rol='admin'
        )
    
    def test_login_sql_injection_username(self):
        """Intentar SQL injection en campo username"""
        payloads = [
            "' OR '1'='1",
            "admin'--",
            "' UNION SELECT * FROM usuarios--",
            "1; DROP TABLE usuarios;--",
        ]
        for payload in payloads:
            response = self.client.post(reverse('auth_login'), {
                'username': payload,
                'password': 'anypassword'
            })
            # No debe autenticar con payloads maliciosos
            self.assertNotIn('_auth_user_id', self.client.session)
    
    def test_search_sql_injection(self):
        """Intentar SQL injection en búsquedas"""
        self.client.login(username='sqlitest', password='TestPass123!')
        payloads = [
            "'; DROP TABLE pedido;--",
            "' UNION SELECT password FROM usuario--",
            "1 OR 1=1",
        ]
        for payload in payloads:
            # No debe causar error 500
            response = self.client.get(
                reverse('pedidos_consulta'),
                {'q': payload}
            )
            self.assertNotEqual(response.status_code, 500)


class XSSProtectionTests(TestCase):
    """Pruebas contra Cross-Site Scripting (XSS)"""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='xsstest',
            password='TestPass123!',
            rol='admin',
            first_name='<script>alert("xss")</script>'
        )
    
    def test_username_xss_escaped(self):
        """Datos de usuario deben estar escapados en output"""
        self.client.login(username='xsstest', password='TestPass123!')
        response = self.client.get(reverse('index'))
        content = response.content.decode()
        # El script no debe aparecer sin escapar
        self.assertNotIn('<script>alert("xss")</script>', content)


class BruteForceProtectionTests(TestCase):
    """Pruebas contra ataques de fuerza bruta"""
    
    def test_axes_is_enabled_for_login_protection(self):
        """La protección de fuerza bruta debe estar centralizada en Axes."""
        from django.conf import settings

        self.assertIn('axes', settings.INSTALLED_APPS)
        self.assertIn('axes.middleware.AxesMiddleware', settings.MIDDLEWARE)
        self.assertIn('axes.backends.AxesStandaloneBackend', settings.AUTHENTICATION_BACKENDS)
        self.assertGreaterEqual(getattr(settings, 'AXES_FAILURE_LIMIT', 0), 1)


class IDORTests(TestCase):
    """Pruebas de Insecure Direct Object Reference (IDOR)"""
    
    def setUp(self):
        from mockups.models import Pedido, TipoBalon
        
        self.client = Client()
        self.user1 = User.objects.create_user(
            username='user1', password='TestPass123!', rol='telefonista'
        )
        self.user2 = User.objects.create_user(
            username='user2', password='TestPass123!', rol='telefonista'
        )
    
    def test_cannot_access_other_user_data(self):
        """
        Usuario no debe acceder a recursos de otros usuarios
        sin autorización adecuada
        """
        # Este test debe adaptarse según tu lógica de negocio
        # Por ejemplo, verificar que un telefonista no puede ver
        # pedidos de otro telefonista (si aplica)
        pass


if __name__ == '__main__':
    # Ejecutar pruebas directamente
    import unittest
    unittest.main()
