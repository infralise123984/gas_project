"""Tests del canal de notificaciones de la app nativa (docs/API_MOVIL.md §7.7).

Cubre las dos mitades: el endpoint que registra el teléfono y el envío por FCM.
El transporte HTTP va simulado: la suite **no sale a internet** y no necesita
credenciales de Firebase para pasar.

Ejecutar con:

    python manage.py test mockups.tests.api.test_dispositivos -v 2
"""

import base64
import json
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.test import TestCase

from mockups.api.limites import cupo_de
from mockups.models import DispositivoPush
from mockups.notificaciones_pedidos import notificar_pedido_disponible
from mockups.push_notifications_movil import (
    ALCANCE_OAUTH,
    URL_OAUTH,
    _codigo_de_error,
    _jwt_firmado,
    enviar_fcm,
    notificar_nuevo_pedido_movil,
)
from mockups.tests.base import crear_balon_11kg, crear_pedido, crear_usuario

RUTA_REGISTRO = '/api/v1/dispositivos/'
RUTA_BAJA = '/api/v1/dispositivos/baja/'

TOKEN_A = 'fcm-token-de-prueba-A'
TOKEN_B = 'fcm-token-de-prueba-B'


class RespuestaFalsa:
    """Respuesta mínima con la forma que mira ``_codigo_de_error``."""

    def __init__(self, status_code=400, cuerpo=None, texto=''):
        self.status_code = status_code
        self._cuerpo = cuerpo
        self.text = texto or (json.dumps(cuerpo) if cuerpo else '')

    def json(self):
        if self._cuerpo is None:
            raise ValueError('sin JSON')
        return self._cuerpo


class BaseDispositivosTest(TestCase):
    """Un camionero con teléfono, otro camionero y un telefonista."""

    def setUp(self):
        cache.clear()  # el limitador vive en la caché del proceso

        self.camionero = crear_usuario('camionero', 'camionero_disp')
        self.otro_camionero = crear_usuario('camionero', 'camionero_disp_2')
        self.telefonista = crear_usuario('telefonista', 'telefonista_disp')
        self.balon = crear_balon_11kg()

    def _registrar(self, token=TOKEN_A, usuario=None, **extra):
        self.client.force_login(usuario or self.camionero)
        cuerpo = {'token': token, 'plataforma': 'android', 'app_version': '0.1.0'}
        cuerpo.update(extra)
        return self.client.post(
            RUTA_REGISTRO, data=json.dumps(cuerpo), content_type='application/json'
        )

    def _baja(self, token, usuario=None):
        self.client.force_login(usuario or self.camionero)
        return self.client.post(
            RUTA_BAJA, data=json.dumps({'token': token}), content_type='application/json'
        )

    def _pedido(self, **extra):
        return crear_pedido(
            registrador=self.telefonista, lineas=[(self.balon, 2)], **extra
        )


class RegistroDispositivoTest(BaseDispositivosTest):
    def test_alta_registra_el_token_del_camionero(self):
        respuesta = self._registrar()

        self.assertEqual(respuesta.status_code, 200)
        sobre = respuesta.json()
        self.assertIn('servidor_ahora', sobre)
        self.assertTrue(sobre['data']['registrado'])
        self.assertTrue(sobre['data']['alta'])

        dispositivo = DispositivoPush.objects.get()
        self.assertEqual(dispositivo.usuario, self.camionero)
        self.assertEqual(dispositivo.token, TOKEN_A)
        self.assertTrue(dispositivo.activa)

    def test_la_respuesta_no_expone_el_token(self):
        respuesta = self._registrar()
        self.assertNotIn(TOKEN_A, respuesta.content.decode())

    def test_reintento_es_idempotente(self):
        self._registrar()
        respuesta = self._registrar()

        self.assertEqual(DispositivoPush.objects.count(), 1)
        self.assertFalse(respuesta.json()['data']['alta'])

    def test_el_mismo_telefono_se_reasigna_al_usuario_nuevo(self):
        self._registrar(usuario=self.camionero)
        self._registrar(usuario=self.otro_camionero)

        self.assertEqual(DispositivoPush.objects.count(), 1)
        self.assertEqual(DispositivoPush.objects.get().usuario, self.otro_camionero)

    def test_token_invalido_se_rechaza(self):
        for token in ('', '   ', 'x' * 256, 12345, None):
            with self.subTest(token=token):
                cache.clear()
                respuesta = self._registrar(token=token)
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')

        self.assertFalse(DispositivoPush.objects.exists())

    def test_plataforma_invalida_se_rechaza(self):
        respuesta = self._registrar(plataforma='windows')
        self.assertEqual(respuesta.status_code, 400)

    def test_campo_desconocido_se_rechaza(self):
        respuesta = self._registrar(rol='camionero')
        self.assertEqual(respuesta.status_code, 400)

    def test_sin_sesion_responde_401_con_sobre(self):
        respuesta = self.client.post(
            RUTA_REGISTRO,
            data=json.dumps({'token': TOKEN_A}),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')

    def test_otro_rol_no_puede_registrar(self):
        respuesta = self._registrar(usuario=self.telefonista)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json()['error']['codigo'], 'sin_permiso')

    def test_solo_post(self):
        self.client.force_login(self.camionero)
        respuesta = self.client.get(RUTA_REGISTRO)

        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(respuesta.json()['error']['codigo'], 'metodo_no_permitido')

    def test_respeta_el_cupo_publicado(self):
        cupo = cupo_de('dispositivos')
        for _ in range(cupo.peticiones):
            self.assertEqual(self._registrar().status_code, 200)

        respuesta = self._registrar()

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta.json()['error']['codigo'], 'demasiadas_peticiones')
        self.assertIn('Retry-After', respuesta)

    def test_baja_solo_borra_el_dispositivo_propio(self):
        self._registrar(token=TOKEN_A, usuario=self.camionero)
        self._registrar(token=TOKEN_B, usuario=self.otro_camionero)

        # El token ajeno no se borra, y la respuesta no confirma si existe.
        ajena = self._baja(TOKEN_B, usuario=self.camionero)
        self.assertEqual(ajena.status_code, 200)
        self.assertEqual(DispositivoPush.objects.count(), 2)

        respuesta = self._baja(TOKEN_A)
        self.assertFalse(respuesta.json()['data']['registrado'])
        self.assertEqual([d.token for d in DispositivoPush.objects.all()], [TOKEN_B])


class EnvioFcmTest(BaseDispositivosTest):
    def setUp(self):
        super().setUp()
        self.pedido = self._pedido()

    def test_sin_credenciales_no_envia_y_no_falla(self):
        with self.settings(FCM_CREDENCIALES=None):
            ok, error = enviar_fcm(TOKEN_A, 'Titulo', 'Cuerpo')

        self.assertFalse(ok)
        self.assertEqual(error, 'FCM no configurado')

        self._registrar()
        with self.settings(FCM_CREDENCIALES=None):
            self.assertEqual(notificar_nuevo_pedido_movil(self.pedido), 0)

    def test_avisa_a_todos_los_dispositivos_activos(self):
        self._registrar(token=TOKEN_A)
        self._registrar(token=TOKEN_B, usuario=self.otro_camionero)

        with patch(
            'mockups.push_notifications_movil.enviar_fcm', return_value=(True, None)
        ) as envio:
            enviados = notificar_nuevo_pedido_movil(self.pedido)

        self.assertEqual(enviados, 2)
        self.assertEqual(
            {llamada.args[0] for llamada in envio.call_args_list}, {TOKEN_A, TOKEN_B}
        )

    def test_no_avisa_al_camionero_que_devuelve(self):
        self._registrar(token=TOKEN_A)
        self._registrar(token=TOKEN_B, usuario=self.otro_camionero)

        with patch(
            'mockups.push_notifications_movil.enviar_fcm', return_value=(True, None)
        ) as envio:
            enviados = notificar_nuevo_pedido_movil(
                self.pedido, excluir_usuario_id=self.camionero.id
            )

        self.assertEqual(enviados, 1)
        self.assertEqual(envio.call_args.args[0], TOKEN_B)

    def test_un_dispositivo_inactivo_no_recibe(self):
        self._registrar()
        DispositivoPush.objects.update(activa=False)

        with patch(
            'mockups.push_notifications_movil.enviar_fcm', return_value=(True, None)
        ) as envio:
            self.assertEqual(notificar_nuevo_pedido_movil(self.pedido), 0)

        envio.assert_not_called()

    def test_token_muerto_desactiva_el_dispositivo(self):
        self._registrar()

        with patch(
            'mockups.push_notifications_movil.enviar_fcm',
            return_value=(False, 'dispositivo_invalido'),
        ):
            self.assertEqual(notificar_nuevo_pedido_movil(self.pedido), 0)

        self.assertFalse(DispositivoPush.objects.get().activa)

    def test_un_fallo_transitorio_no_desactiva_el_dispositivo(self):
        self._registrar()

        with patch(
            'mockups.push_notifications_movil.enviar_fcm',
            return_value=(False, 'sin_conexion_fcm (ConnectionError)'),
        ):
            self.assertEqual(notificar_nuevo_pedido_movil(self.pedido), 0)

        self.assertTrue(DispositivoPush.objects.get().activa)

    def test_solo_avisa_pedidos_disponibles_de_domicilio(self):
        self._registrar()
        en_ruta = self._pedido(estado='en_ruta', entregador=self.camionero)
        del_local = self._pedido(origen='local')

        with patch(
            'mockups.push_notifications_movil.enviar_fcm', return_value=(True, None)
        ) as envio:
            self.assertEqual(notificar_nuevo_pedido_movil(en_ruta), 0)
            self.assertEqual(notificar_nuevo_pedido_movil(del_local), 0)

        envio.assert_not_called()

    def test_solo_avisa_a_camioneros(self):
        DispositivoPush.objects.create(usuario=self.telefonista, token=TOKEN_A)
        DispositivoPush.objects.create(usuario=self.camionero, token=TOKEN_B)

        with patch(
            'mockups.push_notifications_movil.enviar_fcm', return_value=(True, None)
        ) as envio:
            self.assertEqual(notificar_nuevo_pedido_movil(self.pedido), 1)

        self.assertEqual(envio.call_args.args[0], TOKEN_B)

    def test_despachador_aisla_el_fallo_de_web_push(self):
        self._registrar()

        with patch(
            'mockups.push_notifications.notificar_nuevo_pedido',
            side_effect=RuntimeError('web push caído'),
        ), patch(
            'mockups.push_notifications_movil.enviar_fcm', return_value=(True, None)
        ):
            resultado = notificar_pedido_disponible(self.pedido)

        self.assertEqual(resultado, {'web': 0, 'movil': 1})

    def test_despachador_no_propaga_el_fallo_del_canal_movil(self):
        with patch(
            'mockups.push_notifications.notificar_nuevo_pedido', return_value=0
        ), patch(
            'mockups.push_notifications_movil.notificar_nuevo_pedido_movil',
            side_effect=RuntimeError('fcm caído'),
        ):
            resultado = notificar_pedido_disponible(self.pedido)

        self.assertEqual(resultado, {'web': 0, 'movil': 0})


class MensajeFcmTest(TestCase):
    """El mensaje que viaja a FCM: es lo que define la eficiencia del diseño."""

    CREDENCIALES = {
        'project_id': 'kim-gas',
        'client_email': 'cuenta@kim-gas.iam.gserviceaccount.com',
        'private_key': 'PEM-simulado',
    }

    def _enviar(self, **extra):
        with self.settings(FCM_CREDENCIALES=self.CREDENCIALES), patch(
            'mockups.push_notifications_movil.token_de_acceso',
            return_value=('acceso-de-prueba', None),
        ), patch('mockups.push_notifications_movil.requests.post') as post:
            post.return_value = RespuestaFalsa(status_code=200, cuerpo={})
            resultado = enviar_fcm(TOKEN_A, 'Titulo', 'Cuerpo', **extra)
        return resultado, post

    def test_lleva_notification_y_data_en_el_mismo_mensaje(self):
        (ok, error), post = self._enviar(datos={'pedido_id': 12}, etiqueta='pedido-12')

        self.assertTrue(ok, error)
        mensaje = post.call_args.kwargs['json']['message']
        self.assertEqual(mensaje['token'], TOKEN_A)
        self.assertEqual(mensaje['notification'], {'title': 'Titulo', 'body': 'Cuerpo'})
        # Todo valor de `data` tiene que ser texto: FCM rechaza los números.
        self.assertEqual(mensaje['data'], {'pedido_id': '12'})

    def test_pide_prioridad_alta_y_reemplaza_por_etiqueta(self):
        (ok, error), post = self._enviar(etiqueta='pedido-12')

        self.assertTrue(ok, error)
        android = post.call_args.kwargs['json']['message']['android']
        self.assertEqual(android['priority'], 'high')
        self.assertEqual(android['notification']['tag'], 'pedido-12')

    def test_usa_la_url_v1_del_proyecto_con_el_token_oauth(self):
        (ok, error), post = self._enviar()

        self.assertTrue(ok, error)
        url = post.call_args.args[0]
        self.assertIn('/v1/projects/kim-gas/messages:send', url)
        self.assertNotIn('fcm/send', url)  # API legacy: no se usa
        self.assertEqual(
            post.call_args.kwargs['headers']['Authorization'], 'Bearer acceso-de-prueba'
        )

    def test_un_error_de_fcm_no_filtra_el_token(self):
        with self.settings(FCM_CREDENCIALES=self.CREDENCIALES), patch(
            'mockups.push_notifications_movil.token_de_acceso',
            return_value=('acceso-de-prueba', None),
        ), patch('mockups.push_notifications_movil.requests.post') as post:
            post.return_value = RespuestaFalsa(
                status_code=503,
                cuerpo={'error': {'status': 'UNAVAILABLE', 'message': 'try again'}},
            )
            ok, error = enviar_fcm(TOKEN_A, 'Titulo', 'Cuerpo')

        self.assertFalse(ok)
        self.assertNotIn(TOKEN_A, str(error))


class CodigoDeErrorFcmTest(TestCase):
    def test_lee_el_error_code_de_details(self):
        respuesta = RespuestaFalsa(
            status_code=404,
            cuerpo={
                'error': {
                    'status': 'NOT_FOUND',
                    'message': 'Requested entity was not found.',
                    'details': [{'errorCode': 'UNREGISTERED'}],
                }
            },
        )

        self.assertEqual(_codigo_de_error(respuesta)[0], 'UNREGISTERED')

    def test_cuerpo_sin_json_usa_el_texto(self):
        respuesta = RespuestaFalsa(cuerpo=None, texto='algo UNREGISTERED aqui')

        self.assertEqual(_codigo_de_error(respuesta)[0], 'UNREGISTERED')

    def test_status_como_respaldo(self):
        respuesta = RespuestaFalsa(
            status_code=403,
            cuerpo={'error': {'status': 'PERMISSION_DENIED', 'message': 'x'}},
        )

        self.assertEqual(_codigo_de_error(respuesta)[0], 'PERMISSION_DENIED')

    def test_sin_codigo_conocido(self):
        respuesta = RespuestaFalsa(status_code=500, cuerpo={'error': {'message': 'raro'}})

        codigo, _ = _codigo_de_error(respuesta)

        self.assertIsNone(codigo)


class JwtFirmadoTest(TestCase):
    """El JWT RS256 es la única pieza criptográfica del canal: se prueba entera."""

    def test_estructura_y_firma_verificable(self):
        llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = llave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        credenciales = {
            'client_email': 'cuenta@kim-gas.iam.gserviceaccount.com',
            'private_key': pem,
        }

        jwt = _jwt_firmado(credenciales, 1_700_000_000)
        cabecera_b64, reclamos_b64, firma_b64 = jwt.split('.')

        def decodificar(segmento):
            relleno = '=' * (-len(segmento) % 4)
            return json.loads(base64.urlsafe_b64decode(segmento + relleno))

        self.assertEqual(decodificar(cabecera_b64), {'alg': 'RS256', 'typ': 'JWT'})
        reclamos = decodificar(reclamos_b64)
        self.assertEqual(reclamos['iss'], credenciales['client_email'])
        self.assertEqual(reclamos['scope'], ALCANCE_OAUTH)
        self.assertEqual(reclamos['aud'], URL_OAUTH)
        self.assertEqual(reclamos['iat'], 1_700_000_000)
        self.assertEqual(reclamos['exp'] - reclamos['iat'], 3600)

        # La firma tiene que verificar con la clave pública: si no, Google
        # respondería 401 y ningún aviso llegaría.
        firma = base64.urlsafe_b64decode(firma_b64 + '=' * (-len(firma_b64) % 4))
        llave.public_key().verify(
            firma,
            f'{cabecera_b64}.{reclamos_b64}'.encode('ascii'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
