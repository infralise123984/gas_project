"""Pruebas del flujo de verificación en dos pasos (TOTP).

Ejecutar:
    python manage.py test mockups.tests.auth.test_2fa -v 2
"""

import pyotp
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from mockups.tests.base import crear_usuario


class TwoFactorAuthFlowTests(TestCase):
    """Pruebas del flujo de verificación en dos pasos (TOTP)."""

    def setUp(self):
        self.client = Client()
        self.secret = pyotp.random_base32()
        self.user = crear_usuario('admin', 'testadmin2fa', password='ClaveSegura123!')
        self.user.totp_secret = self.secret
        self.user.totp_activo = True
        self.user.save(update_fields=['totp_secret', 'totp_activo'])

    def _hacer_login_factor1(self):
        """Completa el primer factor (usuario/contraseña) y retorna la respuesta."""
        return self.client.post(reverse('auth_login'), {
            'username': 'testadmin2fa',
            'password': 'ClaveSegura123!',
        })

    def test_factor1_redirige_a_verificacion_2fa(self):
        """Login con 2FA activo debe redirigir a la página de verificación."""
        response = self._hacer_login_factor1()
        self.assertRedirects(response, reverse('auth_verificar_2fa'), fetch_redirect_response=False)

    def test_session_guarda_pending_user_id(self):
        """El user_id debe quedar pendiente en sesión tras el primer factor."""
        self._hacer_login_factor1()
        self.assertIn('2fa_pending_user_id', self.client.session)
        self.assertEqual(self.client.session['2fa_pending_user_id'], self.user.pk)

    def test_session_guarda_timestamp_pendiente(self):
        """El timestamp 2fa_pending_at debe guardarse al iniciar el flujo 2FA."""
        self._hacer_login_factor1()
        self.assertIn('2fa_pending_at', self.client.session)
        ahora = timezone.now().timestamp()
        delta = abs(ahora - self.client.session['2fa_pending_at'])
        self.assertLess(delta, 5, "El timestamp debe ser reciente (< 5 segundos de diferencia)")

    def test_session_key_cambia_tras_factor1(self):
        """La session key debe rotarse tras el primer factor (mitiga session fixation)."""
        # Obtener la key de una sesión anónima previa
        self.client.get(reverse('auth_login'))
        key_anonima = self.client.session.session_key
        self._hacer_login_factor1()
        key_post_factor1 = self.client.session.session_key
        self.assertNotEqual(key_anonima, key_post_factor1, "La session key debe rotar tras el factor 1")

    def test_2fa_codigo_valido_completa_login(self):
        """Ingresar un código TOTP válido debe autenticar al usuario."""
        self._hacer_login_factor1()
        codigo = pyotp.TOTP(self.secret).now()
        response = self.client.post(reverse('auth_verificar_2fa'), {'codigo': codigo})
        # Debe redirigir al inicio (login completado)
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_2fa_codigo_invalido_no_autentica(self):
        """Un código incorrecto no debe autenticar al usuario."""
        self._hacer_login_factor1()
        response = self.client.post(reverse('auth_verificar_2fa'), {'codigo': '000000'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_2fa_estado_expirado_redirige_a_login(self):
        """Un estado 2FA con más de 10 minutos debe expirar y redirigir a login."""
        self._hacer_login_factor1()
        # Manipular el timestamp para simular expiración
        session = self.client.session
        session['2fa_pending_at'] = timezone.now().timestamp() - 700  # 11+ minutos atrás
        session.save()
        codigo = pyotp.TOTP(self.secret).now()
        response = self.client.post(reverse('auth_verificar_2fa'), {'codigo': codigo})
        self.assertRedirects(response, reverse('auth_login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertNotIn('2fa_pending_user_id', self.client.session)

    def test_2fa_bloqueo_tras_5_intentos_fallidos(self):
        """Tras 5 intentos fallidos la sesión debe invalidarse y redirigir a login."""
        self._hacer_login_factor1()
        for _ in range(5):
            self.client.post(reverse('auth_verificar_2fa'), {'codigo': '000000'})
        # El sexto intento debe estar bloqueado
        response = self.client.post(reverse('auth_verificar_2fa'), {'codigo': '000000'})
        self.assertRedirects(response, reverse('auth_login'), fetch_redirect_response=False)
        self.assertNotIn('2fa_pending_user_id', self.client.session)

    def test_verificar_2fa_sin_sesion_pendiente_redirige(self):
        """Acceder a /verificar-2fa/ sin sesión pendiente debe redirigir a login."""
        response = self.client.get(reverse('auth_verificar_2fa'))
        self.assertRedirects(response, reverse('auth_login'), fetch_redirect_response=False)

    def test_activar_2fa_no_regenera_qr_al_recargar(self):
        """Recargar la página de activación no debe cambiar el secreto temporal."""
        self.user.totp_activo = False
        self.user.totp_secret = None
        self.user.save(update_fields=['totp_activo', 'totp_secret'])
        self.client.force_login(self.user)

        self.client.get(reverse('auth_activar_2fa'))
        secreto_primera_carga = self.client.session.get('2fa_setup_secret')

        self.client.get(reverse('auth_activar_2fa'))
        secreto_segunda_carga = self.client.session.get('2fa_setup_secret')

        self.assertEqual(secreto_primera_carga, secreto_segunda_carga,
                         "El secreto no debe cambiar al recargar la página de activación")
