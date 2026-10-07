"""Tests del API móvil (F1).

Archivo aparte a propósito: no se toca ninguno de los tests existentes.
Ejecutar con:

    python manage.py test mockups.tests_api_movil -v 2

Cubre el mínimo del docs/API_MOVIL.md §8 y los casos que exige el spike de
sesión + CSRF + django-axes. Lo que NO se cubre queda listado al final del
archivo.
"""

import json
import time
from calendar import monthrange
from datetime import timedelta
from unittest.mock import patch

from axes.models import AccessAttempt
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from mockups.api.limites import consumir
from mockups.models import (
    AuditoriaAccion,
    DetallePedido,
    HistorialCambioPedido,
    HistorialEstadoPedido,
    Pedido,
    TipoBalon,
    Usuario,
)
from mockups.utils.fechas import navegacion_mes, rango_dia_chile, today_chile

RUTA_CSRF = '/api/v1/auth/csrf/'
RUTA_LOGIN = '/api/v1/auth/login/'
RUTA_LOGOUT = '/api/v1/auth/logout/'
RUTA_PERFIL = '/api/v1/auth/perfil/'
RUTA_ENTREGAS = '/api/v1/entregas/'
RUTA_TARREO = '/api/v1/tarreo/'
RUTA_BALONES = '/api/v1/balones/'
RUTA_RESUMEN_HOY = '/api/v1/resumen-hoy/'
RUTA_HISTORIAL = '/api/v1/historial/'
RUTA_VERSION = '/api/v1/version/'


def ruta_accion(accion, pedido_id):
    """Ruta de una de las cuatro acciones del §7.3."""
    return f'/api/v1/entregas/{pedido_id}/{accion}/'

ORIGEN_CONFIABLE = settings.CSRF_TRUSTED_ORIGINS[0]
ORIGEN_ARBITRARIO = 'https://atacante.example'


def creado_hoy(momento=10):
    """Un instante garantizado dentro del día actual de Chile (no del día UTC)."""
    inicio, _ = rango_dia_chile(today_chile())
    return inicio + timedelta(hours=momento)


class BaseApiTest(TestCase):
    """Datos comunes: dos camioneros, un telefonista y pedidos en los tres grupos."""

    def setUp(self):
        cache.clear()  # el limitador vive en la caché del proceso

        self.camionero_a = Usuario.objects.create_user(
            username='camionero_a', password='ClaveDePrueba123', rol='camionero'
        )
        self.camionero_b = Usuario.objects.create_user(
            username='camionero_b', password='ClaveDePrueba123', rol='camionero'
        )
        self.telefonista = Usuario.objects.create_user(
            username='telefonista_1', password='ClaveDePrueba123', rol='telefonista'
        )

        self.balon = TipoBalon.objects.create(
            nombre='Gas 11 kg', peso_neto_gas=11,
            precio_compra=15000, precio_local=20000, precio_domicilio=22900,
        )

        self.en_ruta_a = self._pedido(estado='en_ruta', entregador=self.camionero_a)
        self.en_ruta_b = self._pedido(estado='en_ruta', entregador=self.camionero_b)
        self.pendiente_hoy = self._pedido(estado='pendiente')
        self.pendiente_ayer = self._pedido(
            estado='pendiente', fecha=creado_hoy() - timedelta(days=1)
        )
        self.pendiente_local = self._pedido(estado='pendiente', origen='local')
        self.entregado_hoy_a = self._pedido(
            estado='entregado', entregador=self.camionero_a
        )
        self.entregado_hoy_b = self._pedido(
            estado='entregado', entregador=self.camionero_b
        )

    def _pedido(self, *, estado, entregador=None, origen='telefono', fecha=None):
        """Pedido con una línea de 2 balones. `monto_total` = 45.800 (int en pesos)."""
        pedido = Pedido.objects.create(
            sector='Población Recreo',
            direccion_entrega='Los Aromos 1234, casa esquina',
            registrador=self.telefonista,
            entregador=entregador,
            estado=estado,
            origen=origen,
            metodo_pago='efectivo',
            fecha=fecha or creado_hoy(),
            monto_total=45800,
        )
        DetallePedido.objects.create(
            pedido=pedido,
            balon=self.balon,
            cantidad=2,
            precio_venta_unitario=22900,
            precio_compra_unitario=15000,
        )
        return pedido

    def _token_csrf(self, cliente=None):
        """Token del doble envío. Requiere que /auth/csrf/ siembre la cookie."""
        cliente = cliente or self.client
        respuesta = cliente.get(RUTA_CSRF, secure=True)
        self.assertEqual(respuesta.status_code, 200)
        return cliente.cookies['csrftoken'].value

    def _post_api(self, ruta, payload, cliente=None, **extra):
        """POST JSON con el doble envío CSRF y un Origin confiable."""
        cliente = cliente or self.client
        token = self._token_csrf(cliente)
        cabeceras = {
            'HTTP_ORIGIN': ORIGEN_CONFIABLE,
            'HTTP_X_CSRFTOKEN': token,
            'content_type': 'application/json',
        }
        cabeceras.update(extra)
        return cliente.post(
            ruta, data=json.dumps(payload), secure=True, **cabeceras
        )

    def _login(self, usuario=None, clave='ClaveDePrueba123'):
        usuario = usuario or self.camionero_a
        return self._post_api(
            RUTA_LOGIN, {'username': usuario.username, 'password': clave}
        )

    def _post_form_login(self, clave):
        """Login con formulario, el otro Content-Type que acepta el contrato."""
        token = self._token_csrf()
        return self.client.post(
            RUTA_LOGIN,
            data={'username': self.camionero_a.username, 'password': clave},
            secure=True,
            HTTP_ORIGIN=ORIGEN_CONFIABLE,
            HTTP_X_CSRFTOKEN=token,
        )


class SobreYAccesoTest(BaseApiTest):
    """Forma del sobre y guardas de acceso (§5 y §8)."""

    def test_sin_sesion_devuelve_401_con_sobre(self):
        for ruta in (RUTA_ENTREGAS, RUTA_PERFIL, RUTA_VERSION):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(ruta)
                self.assertEqual(respuesta.status_code, 401)
                cuerpo = respuesta.json()
                self.assertEqual(cuerpo['error']['codigo'], 'no_autenticado')
                self.assertIn('servidor_ahora', cuerpo)

    def test_sin_sesion_en_logout_devuelve_401(self):
        respuesta = self.client.post(RUTA_LOGOUT, secure=True)
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')

    def test_rol_incorrecto_devuelve_403_y_audita_perm_denied(self):
        self.client.force_login(self.telefonista)
        respuesta = self.client.get(RUTA_ENTREGAS)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json()['error']['codigo'], 'sin_permiso')
        # El cuerpo NO es el del helper require_roles_api.
        self.assertNotIn('error', respuesta.json()['error'])
        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='PERM_DENIED').exists(),
            'require_roles_api debe seguir auditando el acceso denegado',
        )

    def test_metodo_no_permitido_con_sobre(self):
        respuesta = self.client.get(RUTA_LOGIN)
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta.json()['error']['codigo'], 'metodo_no_permitido')
        self.assertIn('servidor_ahora', respuesta.json())

    def test_servidor_ahora_en_toda_respuesta(self):
        self.client.force_login(self.camionero_a)
        respuesta = self.client.get(RUTA_ENTREGAS)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('servidor_ahora', respuesta.json())

    def test_no_se_cachea_ninguna_respuesta(self):
        self.client.force_login(self.camionero_a)
        respuesta = self.client.get(RUTA_ENTREGAS)
        self.assertIn('no-store', respuesta['Cache-Control'])


class ListadoEntregasTest(BaseApiTest):
    """Alcance de las tres consultas del panel (§7.1)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.camionero_a)

    def test_pendientes_solo_de_hoy_telefono_y_sin_asignar(self):
        respuesta = self.client.get(RUTA_ENTREGAS)
        ids = [p['id'] for p in respuesta.json()['data']['pendientes']]

        self.assertIn(self.pendiente_hoy.id, ids)
        self.assertNotIn(self.pendiente_ayer.id, ids, 'pedido de ayer no debe aparecer')
        self.assertNotIn(self.pendiente_local.id, ids, 'origen local no es del pool')
        self.assertNotIn(self.en_ruta_a.id, ids)

    def test_en_ruta_solo_del_camionero_autenticado(self):
        respuesta = self.client.get(RUTA_ENTREGAS)
        ids = [p['id'] for p in respuesta.json()['data']['en_ruta']]

        self.assertEqual(ids, [self.en_ruta_a.id])
        self.assertNotIn(
            self.en_ruta_b.id, ids,
            'un camionero no debe ver los pedidos en ruta de otro',
        )

    def test_entregados_hoy_solo_propios(self):
        respuesta = self.client.get(RUTA_ENTREGAS)
        ids = [p['id'] for p in respuesta.json()['data']['entregados_hoy']]

        self.assertEqual(ids, [self.entregado_hoy_a.id])
        self.assertNotIn(self.entregado_hoy_b.id, ids)

    def test_actividad_hoy_no_es_solo_lo_entregado(self):
        """El listado del día también trae en ruta y cancelados.

        Es lo que muestra `mis_entregas_camionero`: sin esto, la app perdería
        los cancelados (con su franja roja) y los que siguen en ruta.
        """
        cancelado = self._pedido(estado='cancelado', entregador=self.camionero_a)

        datos = self.client.get(RUTA_ENTREGAS).json()['data']
        ids = [p['id'] for p in datos['actividad_hoy']]

        self.assertIn(self.entregado_hoy_a.id, ids)
        self.assertIn(self.en_ruta_a.id, ids)
        self.assertIn(cancelado.id, ids)
        self.assertNotIn(
            self.pendiente_hoy.id, ids,
            'un pendiente sin asignar no es actividad del camionero',
        )
        self.assertNotIn(
            self.en_ruta_b.id, ids,
            'no debe ver la actividad de otro camionero',
        )

    def test_actividad_hoy_contiene_lo_entregado(self):
        datos = self.client.get(RUTA_ENTREGAS).json()['data']
        entregados = {p['id'] for p in datos['entregados_hoy']}
        actividad = {p['id'] for p in datos['actividad_hoy']}

        self.assertTrue(
            entregados.issubset(actividad),
            'entregados_hoy debe ser un subconjunto de actividad_hoy',
        )

    def test_hoy_es_la_fecha_de_chile(self):
        respuesta = self.client.get(RUTA_ENTREGAS)
        self.assertEqual(respuesta.json()['data']['hoy'], today_chile().isoformat())

    def test_objeto_pedido_cumple_el_contrato(self):
        respuesta = self.client.get(RUTA_ENTREGAS)
        pedido = respuesta.json()['data']['en_ruta'][0]

        self.assertEqual(
            set(pedido),
            {
                'id', 'estado', 'estado_etiqueta', 'origen', 'origen_etiqueta',
                'sector', 'direccion_entrega', 'metodo_pago', 'fecha',
                'monto_total', 'subtotal_bruto', 'descuento_total',
                'tiene_descuento', 'kilos', 'lineas',
            },
        )
        self.assertEqual(pedido['sector'], 'Población Recreo')  # valor crudo
        self.assertEqual(pedido['kilos'], 22)                   # int, no Decimal
        self.assertEqual(pedido['monto_total'], 45800)          # int en pesos
        self.assertEqual(pedido['descuento_total'], 0)
        self.assertFalse(pedido['tiene_descuento'])
        self.assertEqual(pedido['estado_etiqueta'], 'En ruta')
        self.assertTrue(pedido['fecha'].endswith(('-03:00', '-04:00')))

        linea = pedido['lineas'][0]
        self.assertEqual(linea['balon_nombre'], 'Gas 11 kg')
        self.assertEqual(linea['cantidad'], 2)
        self.assertEqual(linea['subtotal'], 45800)
        self.assertEqual(linea['subtotal_neto'], 45800)

    def test_sin_consultas_n_plus_uno_al_agregar_pedidos(self):
        with CaptureQueriesContext(connection) as primera:
            self.client.get(RUTA_ENTREGAS)

        for _ in range(4):
            self._pedido(estado='entregado', entregador=self.camionero_a)

        with CaptureQueriesContext(connection) as segunda:
            self.client.get(RUTA_ENTREGAS)

        self.assertEqual(
            len(primera), len(segunda),
            'el número de consultas no debe crecer con la cantidad de pedidos',
        )


class AccionesEntregasTest(BaseApiTest):
    """Las cuatro acciones del §7.3, con sus guardas."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.camionero_a)

    def _accion(self, accion, pedido):
        return self.client.post(ruta_accion(accion, pedido.id))

    # --- tomar ---------------------------------------------------------

    def test_tomar_asigna_el_pedido_y_deja_historial(self):
        respuesta = self._accion('tomar', self.pendiente_hoy)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['data']['pedido']['estado'], 'en_ruta')

        self.pendiente_hoy.refresh_from_db()
        self.assertEqual(self.pendiente_hoy.entregador, self.camionero_a)
        self.assertTrue(
            HistorialEstadoPedido.objects.filter(
                pedido=self.pendiente_hoy, estado_nuevo='en_ruta'
            ).exists()
        )
        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='PEDIDO_UPDATE').exists()
        )

    def test_tomar_dos_veces_devuelve_409(self):
        """La segunda toma es la carrera que el contrato resuelve con 409."""
        self.assertEqual(self._accion('tomar', self.pendiente_hoy).status_code, 200)

        segunda = self._accion('tomar', self.pendiente_hoy)

        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(segunda.json()['error']['codigo'], 'no_disponible')

    def test_un_pedido_de_local_no_se_puede_tomar(self):
        """El pool de pendientes es solo de origen telefónico."""
        self.assertEqual(self._accion('tomar', self.pendiente_local).status_code, 409)

    # --- entregar ------------------------------------------------------

    def test_entregar_marca_entregado(self):
        respuesta = self._accion('entregar', self.en_ruta_a)

        self.assertEqual(respuesta.status_code, 200)
        self.en_ruta_a.refresh_from_db()
        self.assertEqual(self.en_ruta_a.estado, 'entregado')

    def test_entregar_un_pedido_ajeno_devuelve_409(self):
        self.assertEqual(self._accion('entregar', self.en_ruta_b).status_code, 409)

    # --- cancelar ------------------------------------------------------

    def test_cancelar_conserva_el_entregador(self):
        """Regla de negocio: queda asignado para que siga en su historial."""
        respuesta = self._accion('cancelar', self.en_ruta_a)

        self.assertEqual(respuesta.status_code, 200)
        self.en_ruta_a.refresh_from_db()
        self.assertEqual(self.en_ruta_a.estado, 'cancelado')
        self.assertEqual(self.en_ruta_a.entregador, self.camionero_a)
        self.assertTrue(
            HistorialCambioPedido.objects.filter(pedido=self.en_ruta_a).exists()
        )

    # --- devolver ------------------------------------------------------

    def test_devolver_libera_el_pedido_al_pool(self):
        respuesta = self._accion('devolver', self.en_ruta_a)

        self.assertEqual(respuesta.status_code, 200)
        self.en_ruta_a.refresh_from_db()
        self.assertEqual(self.en_ruta_a.estado, 'pendiente')
        self.assertIsNone(self.en_ruta_a.entregador)
        self.assertIsNotNone(self.en_ruta_a.devuelto_el)
        self.assertEqual(self.en_ruta_a.devuelto_por, self.camionero_a)

    def test_devolver_un_pedido_ajeno_devuelve_409(self):
        self.assertEqual(self._accion('devolver', self.en_ruta_b).status_code, 409)

    # --- guardas comunes -----------------------------------------------

    def test_get_devuelve_405_con_sobre(self):
        respuesta = self.client.get(ruta_accion('tomar', self.pendiente_hoy.id))

        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta.json()['error']['codigo'], 'metodo_no_permitido')

    def test_sin_sesion_devuelve_401(self):
        self.client.logout()

        respuesta = self._accion('tomar', self.pendiente_hoy)

        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')

    def test_rol_incorrecto_devuelve_403(self):
        self.client.force_login(self.telefonista)

        respuesta = self._accion('tomar', self.pendiente_hoy)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json()['error']['codigo'], 'sin_permiso')


class CsrfSpikeTest(BaseApiTest):
    """Spike del §6.1: comportamiento REAL de Django 5.1.3 sobre HTTPS.

    Estos tests documentan la respuesta definitiva a la pregunta que bloqueaba
    F1: la app debe enviar `Origin` confiable + `X-CSRFToken`, o recibe 403.
    """

    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)

    def test_https_sin_origin_ni_referer_es_rechazado(self):
        """Sin Origin y sin Referer, Django 5.1.3 rechaza el POST seguro.

        `CsrfViewMiddleware.process_view` cae en `_check_referer()` y levanta
        REASON_NO_REFERER. Es el motivo por el que la app DEBE mandar Origin.
        """
        respuesta = self.client.post(
            RUTA_LOGIN,
            data=json.dumps({'username': 'x', 'password': 'y'}),
            content_type='application/json',
            secure=True,
        )
        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn('servidor_ahora', respuesta.content.decode('utf-8', 'replace'))

    def test_origin_confiable_y_token_permite_el_login(self):
        respuesta = self._login()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['data']['usuario']['rol'], 'camionero')

    def test_origin_arbitrario_es_rechazado(self):
        token = self._token_csrf()
        respuesta = self.client.post(
            RUTA_LOGIN,
            data=json.dumps({'username': 'camionero_a', 'password': 'ClaveDePrueba123'}),
            content_type='application/json',
            secure=True,
            HTTP_ORIGIN=ORIGEN_ARBITRARIO,
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_sin_token_csrf_es_rechazado(self):
        respuesta = self.client.post(
            RUTA_LOGIN,
            data=json.dumps({'username': 'camionero_a', 'password': 'ClaveDePrueba123'}),
            content_type='application/json',
            secure=True,
            HTTP_ORIGIN=ORIGEN_CONFIABLE,
        )
        self.assertEqual(respuesta.status_code, 403)

    def test_csrf_siembra_cookie_y_devuelve_un_token_usable(self):
        """La app obtiene aquí el valor para ``X-CSRFToken``.

        ``get_token()`` devuelve el token **enmascarado** y la cookie guarda el
        secreto: son cadenas distintas y Django acepta cualquiera de las dos en
        la cabecera. Por eso no se compara por igualdad, se comprueba que ambas
        sirven para completar el doble envío.
        """
        cliente = Client(enforce_csrf_checks=True)
        respuesta = cliente.get(RUTA_CSRF, secure=True)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('csrftoken', cliente.cookies)

        del_cuerpo = respuesta.json()['data']['csrf_token']
        de_la_cookie = cliente.cookies['csrftoken'].value

        for etiqueta, token in (('cuerpo', del_cuerpo), ('cookie', de_la_cookie)):
            with self.subTest(origen_del_token=etiqueta):
                otro = Client(enforce_csrf_checks=True)
                otro.cookies['csrftoken'] = de_la_cookie
                login = otro.post(
                    RUTA_LOGIN,
                    data=json.dumps(
                        {'username': 'camionero_a', 'password': 'ClaveDePrueba123'}
                    ),
                    content_type='application/json',
                    secure=True,
                    HTTP_ORIGIN=ORIGEN_CONFIABLE,
                    HTTP_X_CSRFTOKEN=token,
                )
                self.assertEqual(login.status_code, 200)


class LoginApiTest(BaseApiTest):
    """Login, logout, 2FA y bloqueo por fuerza bruta (§6.1)."""

    def setUp(self):
        super().setUp()
        # Sin enforce_csrf_checks el test client no ejercita CSRF; el spike está
        # en CsrfSpikeTest. Aquí se prueba la lógica del endpoint.
        self.client = Client()

    def test_credenciales_invalidas_devuelve_401_y_audita(self):
        respuesta = self._login(clave='incorrecta')

        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')
        self.assertTrue(
            AuditoriaAccion.objects.filter(tipo='LOGIN_FAIL').exists()
        )

    def test_login_ok_abre_sesion_y_audita(self):
        respuesta = self._login()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['data']['usuario']['username'], 'camionero_a')
        self.assertTrue(AuditoriaAccion.objects.filter(tipo='LOGIN_OK').exists())
        self.assertEqual(self.client.get(RUTA_PERFIL).status_code, 200)

    def test_perfil_no_expone_secretos(self):
        self.client.force_login(self.camionero_a)
        datos = self.client.get(RUTA_PERFIL).json()['data']

        self.assertEqual(datos['usuario']['rol'], 'camionero')
        for prohibido in ('password', 'totp_secret', 'last_login', 'is_superuser'):
            self.assertNotIn(prohibido, datos['usuario'])
        self.assertIn('csrf_token', datos)

    def test_usuario_con_2fa_no_abre_sesion(self):
        self.camionero_a.totp_activo = True
        self.camionero_a.save(update_fields=['totp_activo'])

        respuesta = self._login()

        self.assertEqual(respuesta.status_code, 202)
        self.assertTrue(respuesta.json()['data']['requiere_2fa'])
        # No debe quedar sesión iniciada.
        self.assertEqual(self.client.get(RUTA_PERFIL).status_code, 401)

    def test_campo_extra_es_rechazado(self):
        respuesta = self._post_api(
            RUTA_LOGIN,
            {'username': 'camionero_a', 'password': 'ClaveDePrueba123', 'rol': 'admin'},
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')

    def test_cuerpo_demasiado_grande_es_rechazado_antes_de_parsear(self):
        respuesta = self._post_api(
            RUTA_LOGIN, {'username': 'x' * 5000, 'password': 'y'}
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')

    def test_content_type_no_soportado(self):
        token = self._token_csrf()
        respuesta = self.client.post(
            RUTA_LOGIN,
            data='username=x&password=y',
            content_type='text/plain',
            secure=True,
            HTTP_ORIGIN=ORIGEN_CONFIABLE,
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_json_invalido(self):
        token = self._token_csrf()
        respuesta = self.client.post(
            RUTA_LOGIN,
            data='{esto no es json',
            content_type='application/json',
            secure=True,
            HTTP_ORIGIN=ORIGEN_CONFIABLE,
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_falta_password(self):
        respuesta = self._post_api(RUTA_LOGIN, {'username': 'camionero_a'})
        self.assertEqual(respuesta.status_code, 400)

    def test_bloqueo_de_axes_tras_cinco_fallos(self):
        """Tras AXES_FAILURE_LIMIT fallos, el API responde 429 con sobre.

        Sin la intervención del endpoint, django-axes reemplazaría la respuesta
        por su propio 429 en texto plano: el cliente recibiría algo que no es el
        contrato. El bloqueo es por IP (AXES_LOCKOUT_PARAMETERS = ['ip_address']).
        """
        limite = settings.AXES_FAILURE_LIMIT

        for intento in range(1, limite):
            respuesta = self._login(clave='incorrecta')
            self.assertEqual(
                respuesta.status_code, 401,
                f'el intento {intento} todavía no debe bloquear',
            )

        # El intento que alcanza el límite ya queda bloqueado.
        quinto = self._login(clave='incorrecta')
        self.assertEqual(quinto.status_code, 429)
        self.assertEqual(quinto.json()['error']['codigo'], 'bloqueado_login')

        # Y el siguiente ni siquiera autentica: se corta antes.
        sexto = self._login()
        self.assertEqual(sexto.status_code, 429)
        self.assertEqual(sexto.json()['error']['codigo'], 'bloqueado_login')
        self.assertIn('Retry-After', sexto)
        self.assertGreater(int(sexto['Retry-After']), 0)
        self.assertIn('servidor_ahora', sexto.json())

    def test_bloqueo_no_afecta_credenciales_validas_antes_del_limite(self):
        self._login(clave='incorrecta')
        self.assertEqual(self._login().status_code, 200)

    def test_el_intento_fallido_no_persiste_la_password(self):
        """django-axes guarda el cuerpo de los intentos fallidos.

        Con JSON el cuerpo no entra en ``request.POST``, así que no se almacena
        nada; con formulario, axes enmascara ``password``. En ninguno de los dos
        casos la contraseña puede quedar en la base.
        """
        secreto = 'ClaveSecretaQueNoDebePersistir'

        self._login(clave=secreto)
        self._post_form_login(secreto)

        intentos = list(AccessAttempt.objects.all())
        self.assertTrue(intentos, 'el fallo debió registrarse en axes')
        for intento in intentos:
            with self.subTest(intento=intento.pk):
                self.assertNotIn(secreto, intento.post_data or '')
                self.assertNotIn(secreto, intento.get_data or '')

    def test_los_logs_de_intento_fallido_no_incluyen_la_password(self):
        secreto = 'ClaveSecretaQueNoDebeLoguearse'

        with self.assertLogs('security', level='WARNING') as registro:
            self._login(clave=secreto)

        self.assertNotIn(secreto, '\n'.join(registro.output))

    def test_logout_cierra_sesion_y_audita(self):
        self._login()
        respuesta = self._post_api(RUTA_LOGOUT, {})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(AuditoriaAccion.objects.filter(tipo='LOGOUT').exists())
        self.assertEqual(self.client.get(RUTA_PERFIL).status_code, 401)


class LimitePeticionesTest(BaseApiTest):
    """Control de abuso (no es protección DDoS: ver docstring de limites.py)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.camionero_a)

    def test_version_se_limita_a_diez_por_minuto(self):
        for numero in range(10):
            self.assertEqual(
                self.client.get(RUTA_VERSION).status_code, 200,
                f'la petición {numero + 1} debe pasar',
            )

        respuesta = self.client.get(RUTA_VERSION)
        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.json()['error']['codigo'], 'demasiadas_peticiones')

        espera = int(respuesta['Retry-After'])
        self.assertTrue(1 <= espera <= 60, f'Retry-After fuera de rango: {espera}')

    def test_el_limite_es_por_usuario(self):
        for _ in range(10):
            self.client.get(RUTA_VERSION)
        self.assertEqual(self.client.get(RUTA_VERSION).status_code, 429)

        self.client.force_login(self.camionero_b)
        self.assertEqual(self.client.get(RUTA_VERSION).status_code, 200)

    def test_si_la_cache_falla_se_falla_abierto(self):
        with patch('mockups.api.limites.consumir', side_effect=RuntimeError('store caído')):
            respuesta = self.client.get(RUTA_VERSION)
        self.assertEqual(respuesta.status_code, 200)


class ConsumidorTest(TestCase):
    """Ventana fija del limitador, sin pasar por HTTP."""

    def setUp(self):
        cache.clear()

    def test_permite_hasta_el_limite(self):
        for _ in range(3):
            permitida, espera = consumir('prueba', 'usuario-1', 3, 60)
            self.assertTrue(permitida)
            self.assertEqual(espera, 0)

        permitida, espera = consumir('prueba', 'usuario-1', 3, 60)
        self.assertFalse(permitida)
        self.assertTrue(1 <= espera <= 60)

    def test_claves_distintas_no_se_mezclan(self):
        for _ in range(3):
            consumir('prueba', 'usuario-1', 3, 60)
        permitida, _ = consumir('prueba', 'usuario-2', 3, 60)
        self.assertTrue(permitida)

    def test_la_ventana_expira(self):
        self.assertTrue(consumir('expira', 'usuario-1', 1, 1)[0])
        self.assertFalse(consumir('expira', 'usuario-1', 1, 1)[0])

        time.sleep(1.1)

        self.assertTrue(
            consumir('expira', 'usuario-1', 1, 1)[0],
            'pasada la ventana la clave debe haber expirado sola',
        )


class VersionTest(BaseApiTest):
    def test_version_minima_y_apk_sin_hosting(self):
        self.client.force_login(self.camionero_a)
        datos = self.client.get(RUTA_VERSION).json()['data']

        self.assertEqual(datos['version_minima'], '0.1.0')
        self.assertIsNone(
            datos['url_apk'],
            'mientras no exista hosting del APK debe ser null, no una URL rota',
        )


class BalonesApiTest(BaseApiTest):
    """Catálogo de balones del §7.5."""

    def setUp(self):
        super().setUp()
        # El balón del `setUp` base hace de "normal": se le fija el tipo para
        # que el orden esperado no dependa del valor por defecto del modelo.
        # Y no se crea otro con el mismo nombre: `TipoBalon.nombre` es único.
        self.balon.tipo_gas = 'normal'
        self.balon.save(update_fields=['tipo_gas'])
        self.normal = self.balon
        self.catalitico = TipoBalon.objects.create(
            nombre='Gas 15 kg catalítico', peso_neto_gas=15, tipo_gas='catalitico',
            precio_compra=21000, precio_local=27000, precio_domicilio=29900,
        )
        self.retirado = TipoBalon.objects.create(
            nombre='Gas 5 kg retirado', peso_neto_gas=5, tipo_gas='normal',
            activo=False, precio_compra=8000, precio_local=11000,
            precio_domicilio=12900,
        )

    def test_lista_los_activos_en_el_orden_de_los_sobres(self):
        self.client.force_login(self.camionero_a)
        respuesta = self.client.get(RUTA_BALONES)

        self.assertEqual(respuesta.status_code, 200)
        ids = [balon['id'] for balon in respuesta.json()['data']]

        self.assertNotIn(
            self.retirado.id, ids, 'un balón retirado no se puede vender'
        )
        # El orden sale de `services.catalogos`: normal antes que catalítico.
        self.assertLess(ids.index(self.normal.id), ids.index(self.catalitico.id))

    def test_no_expone_el_precio_de_compra(self):
        """El costo no viaja al teléfono: con él se reconstruye el margen."""
        self.client.force_login(self.camionero_a)
        datos = self.client.get(RUTA_BALONES).json()['data']

        self.assertEqual(
            set(datos[0]),
            {'id', 'nombre', 'peso_neto_gas', 'precio_domicilio', 'tipo_gas'},
        )

    def test_sin_sesion_devuelve_401(self):
        respuesta = self.client.get(RUTA_BALONES)

        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')

    def test_rol_incorrecto_devuelve_403(self):
        self.client.force_login(self.telefonista)
        respuesta = self.client.get(RUTA_BALONES)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json()['error']['codigo'], 'sin_permiso')


class TarreoApiTest(BaseApiTest):
    """Venta en la calle del §7.4."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.camionero_a)
        self.retirado = TipoBalon.objects.create(
            nombre='Gas 5 kg', peso_neto_gas=5, tipo_gas='normal', activo=False,
            precio_compra=8000, precio_local=11000, precio_domicilio=12900,
        )

    def _venta(self, payload):
        return self._post_api(RUTA_TARREO, payload)

    def _payload(self, **extra):
        payload = {
            'metodo_pago': 'efectivo',
            'lineas': [{'balon_id': self.balon.id, 'cantidad': 2}],
        }
        payload.update(extra)
        return payload

    def _ventas(self):
        return Pedido.objects.filter(origen='tarreo')

    def test_registra_la_venta_ya_entregada_y_calcula_los_totales(self):
        respuesta = self._venta(self._payload(direccion_entrega='Frente a la plaza'))

        self.assertEqual(respuesta.status_code, 200)
        pedido_json = respuesta.json()['data']['pedido']
        self.assertEqual(pedido_json['origen'], 'tarreo')
        self.assertEqual(pedido_json['estado'], 'entregado')
        self.assertEqual(pedido_json['direccion_entrega'], 'Frente a la plaza')
        self.assertEqual(pedido_json['monto_total'], 45800)

        pedido = self._ventas().get()
        self.assertEqual(pedido.registrador, self.camionero_a)
        self.assertEqual(pedido.entregador, self.camionero_a)
        # `calcular_totales()`: la web deja estos dos campos en cero (§7.4).
        self.assertEqual(int(pedido.ganancia_total), 15800)
        self.assertEqual(int(pedido.descuento_total), 0)
        self.assertTrue(
            HistorialEstadoPedido.objects.filter(
                pedido=pedido, estado_nuevo='entregado'
            ).exists()
        )

    def test_ignora_los_precios_y_descuentos_que_mande_el_cliente(self):
        """El camionero no está en `ROLES_CON_DESCUENTO` y el precio lo fija el
        catálogo: si el payload pudiera imponerlos, vendería a cualquier precio."""
        respuesta = self._venta(
            {
                'metodo_pago': 'efectivo',
                'lineas': [
                    {
                        'balon_id': self.balon.id,
                        'cantidad': 2,
                        'precio_venta_unitario': 1,
                        'precio_compra_unitario': 1,
                        'descuento_unitario': 20000,
                    }
                ],
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        pedido_json = respuesta.json()['data']['pedido']
        self.assertEqual(pedido_json['lineas'][0]['precio_venta_unitario'], 22900)
        self.assertEqual(pedido_json['lineas'][0]['descuento_unitario'], 0)
        self.assertEqual(pedido_json['monto_total'], 45800)
        self.assertEqual(int(self._ventas().get().descuento_total), 0)

    def test_direccion_vacia_usa_la_por_defecto(self):
        for direccion in (None, '', '   '):
            with self.subTest(direccion=direccion):
                payload = self._payload()
                if direccion is not None:
                    payload['direccion_entrega'] = direccion

                respuesta = self._venta(payload)

                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(
                    respuesta.json()['data']['pedido']['direccion_entrega'],
                    'Tarreo / venta directa en camión',
                )

    def test_suma_las_lineas_repetidas_del_mismo_balon(self):
        """La web tiene un campo por balón; dos líneas del mismo balón solo pueden
        venir de la app, y sumarlas es lo que el usuario quiso decir."""
        respuesta = self._venta(
            {
                'metodo_pago': 'efectivo',
                'lineas': [
                    {'balon_id': self.balon.id, 'cantidad': 1},
                    {'balon_id': self.balon.id, 'cantidad': 2},
                ],
            }
        )

        self.assertEqual(respuesta.status_code, 200)
        pedido_json = respuesta.json()['data']['pedido']
        self.assertEqual(len(pedido_json['lineas']), 1)
        self.assertEqual(pedido_json['lineas'][0]['cantidad'], 3)
        self.assertEqual(pedido_json['monto_total'], 68700)

    def test_sin_metodo_pago_no_crea_nada(self):
        respuesta = self._venta({'lineas': [{'balon_id': self.balon.id, 'cantidad': 2}]})

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
        self.assertEqual(self._ventas().count(), 0)

    def test_metodo_pago_desconocido_devuelve_400(self):
        respuesta = self._venta(self._payload(metodo_pago='trueque'))

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
        self.assertEqual(self._ventas().count(), 0)

    def test_sin_lineas_utiles_no_crea_nada(self):
        casos = (
            self._payload(lineas=[]),
            self._payload(lineas=[{'balon_id': self.balon.id, 'cantidad': 0}]),
            self._payload(lineas='dos balones'),
        )
        for payload in casos:
            with self.subTest(payload=payload):
                respuesta = self._venta(payload)

                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
                self.assertEqual(self._ventas().count(), 0)

    def test_balon_retirado_o_inexistente_devuelve_400(self):
        for balon_id in (self.retirado.id, 999999):
            with self.subTest(balon_id=balon_id):
                respuesta = self._venta(
                    {
                        'metodo_pago': 'efectivo',
                        'lineas': [{'balon_id': balon_id, 'cantidad': 1}],
                    }
                )

                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
                self.assertEqual(self._ventas().count(), 0)

    def test_campo_no_permitido_en_el_payload_devuelve_400(self):
        respuesta = self._venta(self._payload(total=1))

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
        self.assertEqual(self._ventas().count(), 0)

    def test_get_devuelve_405_con_sobre(self):
        respuesta = self.client.get(RUTA_TARREO)

        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta.json()['error']['codigo'], 'metodo_no_permitido')

    def test_sin_sesion_devuelve_401(self):
        self.client.logout()

        respuesta = self._venta(self._payload())

        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')
        self.assertEqual(self._ventas().count(), 0)

    def test_rol_incorrecto_devuelve_403(self):
        self.client.force_login(self.telefonista)

        respuesta = self._venta(self._payload())

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json()['error']['codigo'], 'sin_permiso')
        self.assertEqual(self._ventas().count(), 0)


class ResumenHistorialApiTest(BaseApiTest):
    """Resúmenes del camionero: día, mes y detalle de un día (§7.5)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.camionero_a)
        # Cancelado del día: aparece en la lista pero no suma plata ni kilos.
        self.cancelado_a = self._pedido(
            estado='cancelado', entregador=self.camionero_a
        )

    def _historial_dia(self, fecha):
        return self.client.get(f'{RUTA_HISTORIAL}{fecha}/')

    # --- resumen-hoy ---------------------------------------------------

    def test_resumen_hoy_suma_solo_los_entregados(self):
        """Total = entregados; lista = toda la actividad (cancelados incluidos)."""
        datos = self.client.get(RUTA_RESUMEN_HOY).json()['data']

        self.assertEqual(datos['hoy'], today_chile().isoformat())
        self.assertEqual(datos['entregas'], 1)
        self.assertEqual(datos['monto'], 45800)
        self.assertEqual(datos['kilos'], 22)

        pedidos = datos['pedidos']
        self.assertEqual(len(pedidos), 3)  # en ruta, entregado y cancelado
        self.assertIn('cancelado', [pedido['estado'] for pedido in pedidos])

    def test_resumen_hoy_no_mezcla_al_otro_camionero(self):
        datos = self.client.get(RUTA_RESUMEN_HOY).json()['data']

        ids = [pedido['id'] for pedido in datos['pedidos']]
        self.assertNotIn(self.entregado_hoy_b.id, ids)
        self.assertNotIn(self.en_ruta_b.id, ids)
        # El pendiente sin asignar no es actividad de nadie todavía.
        self.assertNotIn(self.pendiente_hoy.id, ids)
        # El de ayer no entra en el día de hoy.
        self.assertNotIn(self.pendiente_ayer.id, ids)

    # --- historial del mes ---------------------------------------------

    def test_historial_sin_mes_usa_el_actual(self):
        hoy = today_chile()
        ultimo = monthrange(hoy.year, hoy.month)[1]
        datos = self.client.get(RUTA_HISTORIAL).json()['data']

        self.assertEqual(datos['mes'], f'{hoy.year:04d}-{hoy.month:02d}')
        self.assertEqual(len(datos['dias']), ultimo)
        # Orden descendente: el último día del mes va primero (como la web).
        self.assertEqual(
            datos['dias'][0]['fecha'],
            f'{hoy.year:04d}-{hoy.month:02d}-{ultimo:02d}',
        )

        self.assertEqual(datos['totales']['entregas'], 1)
        self.assertEqual(datos['totales']['monto'], 45800)
        self.assertEqual(datos['totales']['kilos'], 22)
        self.assertEqual(datos['dias_con_venta'], 1)

        self.assertFalse(
            datos['mes_siguiente_habilitado'],
            'no se navega al futuro: no hay nada que mostrar',
        )

    def test_historial_marca_hoy_y_los_dias_sin_venta(self):
        hoy = today_chile()
        datos = self.client.get(RUTA_HISTORIAL).json()['data']
        fila_hoy = next(f for f in datos['dias'] if f['es_hoy'])

        self.assertEqual(fila_hoy['fecha'], hoy.isoformat())
        self.assertTrue(fila_hoy['tiene_venta'])
        self.assertEqual(
            set(fila_hoy),
            {
                'fecha', 'es_hoy', 'tiene_venta', 'entregas', 'monto',
                'kilos', 'kilos_domicilio', 'kilos_tarreo',
            },
        )
        vacias = [f for f in datos['dias'] if not f['tiene_venta']]
        self.assertTrue(vacias and all(f['monto'] == 0 for f in vacias))

    def test_historial_de_un_mes_anterior_no_tiene_ventas(self):
        anterior = navegacion_mes(today_chile().year, today_chile().month)[0]
        datos = self.client.get(
            RUTA_HISTORIAL, {'mes': f'{anterior[0]:04d}-{anterior[1]:02d}'}
        ).json()['data']

        self.assertEqual(datos['totales']['entregas'], 0)
        self.assertEqual(datos['dias_con_venta'], 0)
        self.assertTrue(datos['mes_siguiente_habilitado'])

    def test_historial_con_mes_invalido_devuelve_400(self):
        """La web cae al mes actual en silencio; el API lo rechaza: mostrarle al
        camionero un mes que no pidió es peor que un error."""
        for mes in ('2026-13', '2026-00', 'octubre', '2026', '', '2026-1x'):
            with self.subTest(mes=mes):
                respuesta = self.client.get(RUTA_HISTORIAL, {'mes': mes})

                if mes == '':
                    # Vacío es "sin parámetro": mes actual, como la web.
                    self.assertEqual(respuesta.status_code, 200)
                    continue
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')

    # --- detalle de un día ---------------------------------------------

    def test_historial_dia_devuelve_solo_los_entregados(self):
        hoy = today_chile()
        datos = self._historial_dia(hoy.isoformat()).json()['data']

        self.assertEqual(datos['fecha'], hoy.isoformat())
        self.assertTrue(datos['es_hoy'])
        self.assertEqual(datos['entregas'], 1)
        self.assertEqual(datos['monto'], 45800)
        self.assertEqual(datos['kilos'], 22)
        self.assertEqual(datos['kilos_domicilio'], 22)
        self.assertEqual(datos['kilos_tarreo'], 0)

        # El día se lista como ventas: el cancelado del día no es una venta.
        ids = [pedido['id'] for pedido in datos['pedidos']]
        self.assertEqual(ids, [self.entregado_hoy_a.id])
        self.assertEqual(datos['pedidos'][0]['kilos'], 22)

    def test_historial_dia_sin_ventas(self):
        ayer = (today_chile() - timedelta(days=1)).isoformat()
        datos = self._historial_dia(ayer).json()['data']

        self.assertFalse(datos['es_hoy'])
        self.assertEqual(datos['entregas'], 0)
        self.assertEqual(datos['monto'], 0)
        self.assertEqual(datos['kilos'], 0)
        self.assertEqual(datos['pedidos'], [])

    def test_historial_dia_con_fecha_invalida_devuelve_400(self):
        for fecha in ('2026-02-30', 'ayer', '2026-13-01', '07-10-2026'):
            with self.subTest(fecha=fecha):
                respuesta = self._historial_dia(fecha)

                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')

    # --- guardas comunes -----------------------------------------------

    def test_sin_sesion_devuelve_401(self):
        self.client.logout()

        for ruta in (RUTA_RESUMEN_HOY, RUTA_HISTORIAL, f'{RUTA_HISTORIAL}2026-10-07/'):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(ruta)

                self.assertEqual(respuesta.status_code, 401)
                self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')

    def test_rol_incorrecto_devuelve_403(self):
        self.client.force_login(self.telefonista)

        for ruta in (RUTA_RESUMEN_HOY, RUTA_HISTORIAL, f'{RUTA_HISTORIAL}2026-10-07/'):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(ruta)

                self.assertEqual(respuesta.status_code, 403)
                self.assertEqual(respuesta.json()['error']['codigo'], 'sin_permiso')


# Pendiente de cubrir:
#   * idempotencia con `Idempotency-Key`: ningún mutador la exige todavía
#     (necesita migración, docs/API_MOVIL.md §6.2);
#   * concurrencia real (`select_for_update` con dos procesos) y del limitador
#     (hilos): se probó la carrera secuencial y el fallo abierto;
#   * que el mes con muchas ventas no degrade: 31 días de consultas es el costo
#     aceptado de replicar `camionero_historial` tal cual.
