"""Pruebas de seguridad del API v1: auditoría del borde de request.

Suite de seguridad del borde de request (docs/API_MOVIL.md §2), complementaria a
`mockups/tests/api/test_movil.py` (la suite funcional). Acá solo se ataca el borde:

1. ¿el request exige una cuenta **válida y activa**?          -> `SesionTest`
2. ¿se exigen los **permisos del rol** en toda ruta?          -> `PermisosRutasTest`
3. ¿los **límites de abuso** se aplican de verdad?            -> `LimiteAbusoTest`
4. ¿la **entrada no confiable** se valida antes de tocar la BD? -> `EntradaNoConfiableTest`
5. ¿los errores **filtran internos**?                         -> `FugaDeInformacionTest`

Los casos que documentan un defecto conocido llevan `documenta_` en el nombre y
una aserción sobre el comportamiento actual: cuando se corrija el defecto, el
test falla y obliga a actualizarlo. No se usa `expectedFailure` para que el
hueco quede visible en el código y no escondido en un decorador.

Ejecutar con:

    python manage.py test mockups.tests.api.test_seguridad -v 2
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase

from mockups.api import entregas, historial
from mockups.api.errores import handler500
from mockups.models import Pedido, TipoBalon, Usuario
from mockups.tests.base import crear_pedido, crear_usuario
from mockups.utils.fechas import rango_dia_chile, today_chile

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

# Solo estas dos se pueden llamar sin sesión (arranque de identidad).
RUTAS_PUBLICAS = (
    ('GET', RUTA_CSRF),
    ('POST', RUTA_LOGIN),
)

# (metodo, ruta, exige rol camionero). El id 1 se usa para las acciones: en la
# matriz de permisos el rechazo ocurre ANTES de buscar el pedido, así que no
# importa si existe.
RUTAS_PRIVADAS = (
    ('GET', RUTA_PERFIL, False),
    ('POST', RUTA_LOGOUT, False),
    ('GET', RUTA_ENTREGAS, True),
    ('POST', '/api/v1/entregas/1/tomar/', True),
    ('POST', '/api/v1/entregas/1/entregar/', True),
    ('POST', '/api/v1/entregas/1/cancelar/', True),
    ('POST', '/api/v1/entregas/1/devolver/', True),
    ('POST', RUTA_TARREO, True),
    ('GET', RUTA_BALONES, True),
    ('GET', RUTA_RESUMEN_HOY, True),
    ('GET', RUTA_HISTORIAL, True),
    ('GET', '/api/v1/historial/2026-10-07/', True),
    ('GET', RUTA_VERSION, False),
)

CLAVE_VALIDA = 'ClaveDePrueba123'


class BaseSeguridadTest(TestCase):
    """Datos mínimos. Deliberadamente chico y explícito: cada test pide lo suyo."""

    def setUp(self):
        cache.clear()  # el limitador vive en la caché del proceso

        self.camionero = crear_usuario('camionero', 'camionero_seg', password=CLAVE_VALIDA)
        self.telefonista = crear_usuario('telefonista', 'telefonista_seg', password=CLAVE_VALIDA)
        self.balon = TipoBalon.objects.create(
            nombre='Gas 11 kg', peso_neto_gas=11,
            precio_compra=15000, precio_local=20000, precio_domicilio=22900,
        )

    def _pedido(self, *, estado='pendiente', entregador=None, origen='telefono'):
        inicio, _ = rango_dia_chile(today_chile())
        return crear_pedido(
            registrador=self.telefonista,
            entregador=entregador,
            estado=estado,
            origen=origen,
            fecha=inicio + timedelta(hours=10),
            sector='Población Recreo',
            direccion_entrega='Los Anegados 1',
            monto_total=45800,
            lineas=[(self.balon, 2)],
            calcular_totales=False,
        )

    def _payload_tarreo(self, **extra):
        payload = {
            'lineas': [{'balon_id': self.balon.pk, 'cantidad': 1}],
            'metodo_pago': 'efectivo',
        }
        payload.update(extra)
        return payload


class SesionTest(BaseSeguridadTest):
    """Pregunta 1: ¿el request exige una cuenta válida y activa?"""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_sin_sesion_ninguna_ruta_privada_pasa(self):
        for metodo, ruta, _ in RUTAS_PRIVADAS:
            with self.subTest(ruta=ruta):
                respuesta = self._llamar(metodo, ruta)
                self.assertEqual(respuesta.status_code, 401)
                self.assertEqual(
                    respuesta.json()['error']['codigo'], 'no_autenticado'
                )
                self.assertIn('servidor_ahora', respuesta.json())

    def test_cuenta_desactivada_no_puede_loguear(self):
        self.camionero.is_active = False
        self.camionero.save(update_fields=['is_active'])

        respuesta = self._login()

        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')

    def test_cuenta_desactivada_en_medio_de_la_sesion_da_401(self):
        """Desactivar a alguien debe cortarle el acceso YA, no en el próximo login.

        `ModelBackend.get_user` valida `is_active` al reconstruir el usuario
        desde la sesión, así que la cookie viva deja de servir.
        """
        self.client.force_login(self.camionero)
        self.assertEqual(self.client.get(RUTA_ENTREGAS).status_code, 200)

        self.camionero.is_active = False
        self.camionero.save(update_fields=['is_active'])

        self.assertEqual(self.client.get(RUTA_ENTREGAS).status_code, 401)

    def test_sesion_de_usuario_borrado_da_401(self):
        self.client.force_login(self.camionero)
        self.camionero.delete()
        self.assertEqual(self.client.get(RUTA_ENTREGAS).status_code, 401)

    def test_logout_invalida_la_sesion_del_lado_del_servidor(self):
        """No basta con borrar la cookie en el teléfono: el servidor la descarta."""
        self.client.force_login(self.camionero)
        cookie = self.client.cookies['sessionid'].value

        self.assertEqual(self.client.post(RUTA_LOGOUT).status_code, 200)

        # Reintento con la cookie que quedó en el teléfono (sesión robada).
        intruso = Client()
        intruso.cookies['sessionid'] = cookie
        self.assertEqual(intruso.get(RUTA_ENTREGAS).status_code, 401)

    def test_login_renueva_el_identificador_de_sesion(self):
        """Anti-fixation: la sesión anónima previa no sobrevive al login."""
        sesion = self.client.session
        sesion['marcador'] = 'anonimo'
        sesion.save()
        anonima = self.client.cookies['sessionid'].value

        self._login()

        self.assertNotEqual(self.client.cookies['sessionid'].value, anonima)

    def test_cookie_de_sesion_no_es_legible_por_scripts(self):
        respuesta = self._login()
        valor = respuesta.cookies['sessionid'].output(header='')
        self.assertIn('HttpOnly', valor)
        self.assertIn('SameSite', valor)

    def test_la_cookie_de_sesion_no_viaja_en_la_respuesta_del_cuerpo(self):
        """El identificador de sesión no debe aparecer en el JSON."""
        respuesta = self._login()
        self.assertNotIn(
            self.client.cookies['sessionid'].value, respuesta.content.decode()
        )

    def _login(self):
        return self.client.post(
            RUTA_LOGIN,
            data=json.dumps(
                {'username': self.camionero.username, 'password': CLAVE_VALIDA}
            ),
            content_type='application/json',
            secure=True,
        )

    def _llamar(self, metodo, ruta):
        if metodo == 'GET':
            return self.client.get(ruta)
        return self.client.post(
            ruta, data=json.dumps({}), content_type='application/json', secure=True
        )


class PermisosRutasTest(BaseSeguridadTest):
    """Pregunta 2: ¿se exigen los permisos del rol en TODA ruta?"""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_nadie_entra_a_una_ruta_de_negocio_sin_ser_camionero(self):
        """Matriz completa: cada rol que no es camionero recibe 403 en cada ruta.

        Se incluye `jefe` y `admin` a propósito: el API es más estricto que la web
        (la web deja entrar a `admin` a `camionero_entregas`), y esa decisión debe
        ser explícita y quedar probada.
        """
        for rol in ('telefonista', 'bodeguero', 'jefe', 'admin'):
            with self.subTest(rol=rol):
                usuario = Usuario.objects.create_user(
                    username=f'usuario_{rol}', password=CLAVE_VALIDA, rol=rol
                )
                self.client.force_login(usuario)

                for metodo, ruta, exige_camionero in RUTAS_PRIVADAS:
                    if not exige_camionero:
                        continue
                    with self.subTest(ruta=ruta):
                        respuesta = self._llamar(metodo, ruta)
                        self.assertEqual(respuesta.status_code, 403)
                        self.assertEqual(
                            respuesta.json()['error']['codigo'], 'sin_permiso'
                        )

                self.client.force_login(self.camionero)  # reset para el próximo rol

    def test_el_rol_camionero_si_entra(self):
        """Ninguna ruta de negocio rechaza a un camionero válido.

        `logout` queda fuera del recorrido: al pasar por él la sesión se cierra y
        el resto del barrido daría 401 por un motivo ajeno a este test.
        """
        self.client.force_login(self.camionero)
        for metodo, ruta, _ in RUTAS_PRIVADAS:
            if ruta == RUTA_LOGOUT:
                continue
            with self.subTest(ruta=ruta):
                respuesta = self._llamar(metodo, ruta)
                self.assertNotIn(
                    respuesta.status_code, (401, 403),
                    f'{metodo} {ruta} rechazó a un camionero válido',
                )

    def test_el_camionero_si_puede_cerrar_su_propia_sesion(self):
        self.client.force_login(self.camionero)
        self.assertEqual(self.client.post(RUTA_LOGOUT).status_code, 200)

    def test_cambio_de_rol_en_medio_de_la_sesion_corta_el_acceso(self):
        """Degradar a alguien debe aplicar de inmediato, sin re-login.

        Si el rol se leyera de la sesión (o se cacheara), este test lo detecta.
        """
        self.client.force_login(self.camionero)
        self.assertEqual(self.client.get(RUTA_ENTREGAS).status_code, 200)

        self.camionero.rol = 'telefonista'
        self.camionero.save(update_fields=['rol'])

        self.assertEqual(self.client.get(RUTA_ENTREGAS).status_code, 403)

    def test_no_se_puede_operar_un_pedido_ajeno_ni_dejarlo_a_medias(self):
        """IDOR: el filtro de propiedad va DENTRO de la consulta que muta.

        Se comprueba la parte que importa: no se devuelve 200 **y** el pedido
        queda intacto. El 409 (y no 403) es deliberado: distinguir "no es tuyo"
        de "ya no está disponible" permitiría enumerar pedidos ajenos.
        """
        ajeno = self._pedido(estado='en_ruta', entregador=self.telefonista)
        self.client.force_login(self.camionero)

        for accion in ('entregar', 'cancelar', 'devolver'):
            with self.subTest(accion=accion):
                respuesta = self.client.post(f'/api/v1/entregas/{ajeno.pk}/{accion}/')
                self.assertEqual(respuesta.status_code, 409)
                self.assertEqual(
                    respuesta.json()['error']['codigo'], 'no_disponible'
                )

                ajeno.refresh_from_db()
                self.assertEqual(ajeno.estado, 'en_ruta')
                self.assertEqual(ajeno.entregador, self.telefonista)

    def test_un_camionero_no_ve_los_pedidos_de_otro(self):
        self._pedido(estado='en_ruta', entregador=self.telefonista)
        self.client.force_login(self.camionero)

        datos = self.client.get(RUTA_ENTREGAS).json()['data']

        for grupo in ('en_ruta', 'entregados_hoy', 'actividad_hoy'):
            for pedido in datos[grupo]:
                self.assertNotEqual(
                    pedido['entregador'] if 'entregador' in pedido else None,
                    self.telefonista.pk,
                )
        self.assertEqual(datos['en_ruta'], [])

    def _llamar(self, metodo, ruta):
        if metodo == 'GET':
            return self.client.get(ruta)
        return self.client.post(
            ruta, data=json.dumps({}), content_type='application/json', secure=True
        )


class CsrfObligatorioTest(BaseSeguridadTest):
    """Ningún mutador quedó exento de CSRF por descuido."""

    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)

    def test_todo_mutador_exige_el_doble_envio(self):
        self.client.force_login(self.camionero)

        for metodo, ruta, _ in RUTAS_PRIVADAS:
            if metodo != 'POST':
                continue
            with self.subTest(ruta=ruta):
                respuesta = self.client.post(
                    ruta,
                    data=json.dumps({}),
                    content_type='application/json',
                    secure=True,
                    HTTP_ORIGIN=settings.CSRF_TRUSTED_ORIGINS[0],
                )
                self.assertEqual(
                    respuesta.status_code, 403,
                    f'{ruta} aceptó un POST sin X-CSRFToken',
                )


class LimiteAbusoTest(BaseSeguridadTest):
    """Pregunta 3: ¿los límites de abuso se aplican de verdad?"""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.camionero)

    def _agotar(self, ruta, veces, metodo='GET', payload=None):
        """Consume la ventana y devuelve la primera respuesta bloqueada."""
        for _ in range(veces):
            if metodo == 'GET':
                self.client.get(ruta)
            else:
                self.client.post(
                    ruta,
                    data=json.dumps(payload or {}),
                    content_type='application/json',
                )
        if metodo == 'GET':
            return self.client.get(ruta)
        return self.client.post(
            ruta, data=json.dumps(payload or {}), content_type='application/json'
        )

    def test_las_cuatro_acciones_comparten_un_solo_cubo(self):
        """No se puede multiplicar el cupo alternando entre las cuatro acciones."""
        self._agotar('/api/v1/entregas/1/tomar/', 30, metodo='POST')

        respuesta = self.client.post('/api/v1/entregas/1/entregar/')

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(
            respuesta.json()['error']['codigo'], 'demasiadas_peticiones'
        )
        self.assertTrue(1 <= int(respuesta['Retry-After']) <= 60)

    def test_el_tarreo_tiene_limite_propio(self):
        respuesta = self._agotar(RUTA_TARREO, 30, metodo='POST', payload={})
        self.assertEqual(respuesta.status_code, 429)

    def test_el_historial_del_mes_es_el_mas_restringido(self):
        """Es la consulta más cara: recorre el mes día por día."""
        respuesta = self._agotar(RUTA_HISTORIAL, 20)
        self.assertEqual(respuesta.status_code, 429)

    def test_rotar_cabeceras_de_proxy_no_evade_el_limite(self):
        """La clave es el usuario, no `X-Forwarded-For` (que controla el cliente)."""
        for numero in range(10):
            self.client.get(RUTA_VERSION, HTTP_X_FORWARDED_FOR=f'10.0.0.{numero}')

        respuesta = self.client.get(
            RUTA_VERSION, HTTP_X_FORWARDED_FOR='203.0.113.99'
        )
        self.assertEqual(
            respuesta.status_code, 429,
            'el límite se evadió cambiando X-Forwarded-For',
        )

    def test_el_cubo_es_por_usuario_y_no_por_ip(self):
        self._agotar(RUTA_VERSION, 10)
        self.assertEqual(self.client.get(RUTA_VERSION).status_code, 429)

        otro = Usuario.objects.create_user(
            username='camionero_seg_2', password=CLAVE_VALIDA, rol='camionero'
        )
        self.client.force_login(otro)

        self.assertEqual(self.client.get(RUTA_VERSION).status_code, 200)

    def test_el_listado_no_se_ve_afectado_por_el_cubo_de_las_acciones(self):
        """Cubos separados: quedarse sin acciones no debe dejar al camionero ciego."""
        self._agotar('/api/v1/entregas/1/tomar/', 30, metodo='POST')
        self.assertEqual(self.client.get(RUTA_ENTREGAS).status_code, 200)

    def test_cada_respuesta_limitada_publica_su_estado(self):
        """§6.4: el cliente puede auto-limitarsese sin chocar antes con el 429."""
        respuesta = self.client.get(RUTA_VERSION)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['X-RateLimit-Limit'], '10')
        self.assertEqual(respuesta['X-RateLimit-Remaining'], '9')
        self.assertEqual(respuesta['X-RateLimit-Scope'], 'version')
        # Segundos restantes, no epoch: el contrato lo dice explícitamente.
        self.assertTrue(1 <= int(respuesta['X-RateLimit-Reset']) <= 60)

    def test_el_429_trae_el_estado_y_retry_after(self):
        respuesta = self._agotar(RUTA_VERSION, 10)

        self.assertEqual(respuesta.status_code, 429)
        self.assertEqual(respuesta['X-RateLimit-Limit'], '10')
        self.assertEqual(respuesta['X-RateLimit-Remaining'], '0')
        self.assertEqual(respuesta['X-RateLimit-Scope'], 'version')
        self.assertEqual(
            respuesta['Retry-After'], respuesta['X-RateLimit-Reset'],
            'Retry-After debe mandar sobre Reset y coincidir con él',
        )

    def test_una_ruta_sin_cupo_no_publica_cabeceras_de_limite(self):
        """`/auth/csrf/` no está limitado: no se le inventa un estado."""
        respuesta = self.client.get(RUTA_CSRF)

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn('X-RateLimit-Limit', respuesta)


class EntradaNoConfiableTest(BaseSeguridadTest):
    """Pregunta 4: ¿la entrada se valida antes de tocar la base?"""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.camionero)

    def _tarreo(self, payload):
        return self.client.post(
            RUTA_TARREO,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_balon_id_decimal_no_se_trunca(self):
        """`int(2.9)` daría 2: se vendería otro balón en vez de rechazar."""
        respuesta = self._tarreo(
            self._payload_tarreo(lineas=[{'balon_id': 2.9, 'cantidad': 1}])
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_balon_id_booleano_no_se_convierte_en_1(self):
        respuesta = self._tarreo(
            self._payload_tarreo(lineas=[{'balon_id': True, 'cantidad': 1}])
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_balon_id_inexistente_da_400_y_no_crea_nada(self):
        respuesta = self._tarreo(
            self._payload_tarreo(lineas=[{'balon_id': 999999, 'cantidad': 1}])
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_cantidad_desmedida_da_400_y_no_un_500(self):
        """Sin tope, el monto desborda `DecimalField(max_digits=12)`.

        En SQLite pasaría desapercibido; en MySQL (producción) es un error de
        base, o sea un 500 provocado por el cliente.
        """
        respuesta = self._tarreo(
            self._payload_tarreo(
                lineas=[{'balon_id': self.balon.pk, 'cantidad': 100_000_000}]
            )
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_la_suma_de_lineas_repetidas_tambien_tiene_tope(self):
        lineas = [{'balon_id': self.balon.pk, 'cantidad': 600}] * 5
        respuesta = self._tarreo(self._payload_tarreo(lineas=lineas))
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_cantidad_decimal_da_400(self):
        respuesta = self._tarreo(
            self._payload_tarreo(
                lineas=[{'balon_id': self.balon.pk, 'cantidad': 1.5}]
            )
        )
        self.assertEqual(respuesta.status_code, 400)

    def test_cantidad_como_texto_de_digitos_si_se_acepta(self):
        """La web manda strings (viene de un formulario); no se cambia eso."""
        respuesta = self._tarreo(
            self._payload_tarreo(
                lineas=[{'balon_id': str(self.balon.pk), 'cantidad': '2'}]
            )
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Pedido.objects.count(), 1)

    def test_digitos_unicode_no_revientan_el_endpoint(self):
        """`'²'.isdigit()` es True, pero `int('²')` levanta ValueError.

        Con `.isdigit()` un `balon_id` así salía como excepción sin manejar
        (el 500 en HTML del §10 #13) en vez de un 400 con sobre. Los dígitos
        árabe-índicos ('٤') sí los convierte `int()`, pero aceptar notación
        ajena no es parte del contrato: la lista es ASCII.
        """
        for valor in ('²', '٤'):
            with self.subTest(valor=valor):
                respuesta = self._tarreo(
                    self._payload_tarreo(
                        lineas=[{'balon_id': valor, 'cantidad': 1}]
                    )
                )
                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(
                    respuesta.json()['error']['codigo'], 'validacion'
                )
                self.assertEqual(Pedido.objects.count(), 0)

    def test_metodo_pago_no_textual_da_400_y_no_un_500(self):
        """`(valor or '').strip()` con un dict levanta AttributeError: era un 500."""
        respuesta = self._tarreo(
            self._payload_tarreo(metodo_pago={'a': 1})
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_direccion_no_textual_da_400_y_no_un_500(self):
        respuesta = self._tarreo(
            self._payload_tarreo(direccion_entrega=['Los Aromos 1'])
        )
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()['error']['codigo'], 'validacion')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_linea_sin_cantidad_no_rompe(self):
        respuesta = self._tarreo(
            self._payload_tarreo(
                lineas=[{'balon_id': self.balon.pk, 'cantidad': 1}, {'balon_id': 2}]
            )
        )
        self.assertEqual(respuesta.status_code, 200)

    def test_mes_con_anio_absurdo_da_400_y_no_un_500(self):
        """`calendar.monthrange()` levanta ValueError con un año fuera de rango."""
        cliente = Client(raise_request_exception=False)
        cliente.force_login(self.camionero)

        for mes in ('999999-01', '-2000-05', '0-01', '2000-13', '2000-00', 'basura'):
            with self.subTest(mes=mes):
                respuesta = cliente.get(f'{RUTA_HISTORIAL}?mes={mes}')
                self.assertEqual(
                    respuesta.status_code, 400,
                    f'?mes={mes} no fue rechazado con 400',
                )
                self.assertEqual(
                    respuesta.json()['error']['codigo'], 'validacion'
                )

    def test_mes_valido_sigue_funcionando(self):
        respuesta = self.client.get(f'{RUTA_HISTORIAL}?mes={today_chile():%Y-%m}')
        self.assertEqual(respuesta.status_code, 200)

    def test_fecha_de_detalle_invalida_da_400(self):
        """El contrato dice AAAA-MM-DD: no se acepta una fecha a medias."""
        for fecha in ('2026-02-30', 'ayer', '2026-13-01', '2026-1-1', '26-10-07', '2026-10-07x'):
            with self.subTest(fecha=fecha):
                respuesta = self.client.get(f'/api/v1/historial/{fecha}/')
                self.assertEqual(respuesta.status_code, 400)

    def test_cuerpo_gigante_en_un_mutador_no_se_procesa(self):
        """Django corta por `DATA_UPLOAD_MAX_MEMORY_SIZE`; acá se fija el 400."""
        cliente = Client(raise_request_exception=False)
        cliente.force_login(self.camionero)

        respuesta = cliente.post(
            RUTA_TARREO,
            data=json.dumps(self._payload_tarreo(relleno='x' * 3_000_000)),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_precio_y_descuento_del_cliente_se_ignoran(self):
        """El servidor cotiza: el cliente no puede fijar el precio ni rebajarlo.

        El §7.4 manda **ignorar** `descuento_unitario` (no rechazarlo), igual que
        `DetallePedidoForm._configurar_campo_descuento()`. Lo que importa es que
        el monto salga del catálogo y no del payload.
        """
        respuesta = self._tarreo(
            self._payload_tarreo(
                lineas=[
                    {
                        'balon_id': self.balon.pk,
                        'cantidad': 1,
                        'precio_venta_unitario': 1,
                        'descuento_unitario': 22000,
                    }
                ]
            )
        )

        self.assertEqual(respuesta.status_code, 200)
        pedido = Pedido.objects.get(origen='tarreo')
        self.assertEqual(pedido.monto_total, 22900, 'se aceptó un precio del cliente')
        self.assertEqual(pedido.descuento_total, 0, 'se aceptó un descuento del cliente')

        linea = pedido.detalles.get()
        self.assertEqual(linea.precio_venta_unitario, 22900)
        self.assertEqual(linea.descuento_unitario, 0)

    def test_inyeccion_sql_en_los_filtros_no_cambia_la_consulta(self):
        """Los filtros van por ORM parametrizado; esto fija que no haya concatenación."""
        respuesta = self.client.get(f'{RUTA_HISTORIAL}?mes=2026-10%27%20OR%201%3D1--')
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Pedido.objects.count(), 0)


class FugaDeInformacionTest(BaseSeguridadTest):
    """Pregunta 5: ¿los errores exponen internos?"""

    def setUp(self):
        super().setUp()
        self.client = Client(raise_request_exception=False)
        # Hace falta al menos un pedido: sin él la vista no serializa nada y un
        # 500 provocado a propósito pasaría desapercibido.
        self._pedido(estado='en_ruta', entregador=self.camionero)

    def _cuerpos_de_error(self):
        self.client.force_login(self.telefonista)
        yield self.client.get(RUTA_ENTREGAS).content.decode()

        self.client.force_login(self.camionero)
        yield self.client.get(f'/api/v1/historial/2026-13-01/').content.decode()
        yield self.client.post(
            RUTA_TARREO, data='{no json', content_type='application/json'
        ).content.decode()
        yield self.client.get(f'{RUTA_HISTORIAL}?mes=999999-01').content.decode()

    def test_ningun_error_del_sobre_filtra_internos(self):
        prohibidos = (
            'Traceback', 'SELECT', 'INSERT', 'mockups_', 'sqlite', 'django.',
            'password', 'SECRET_KEY', 'File "',
        )
        for cuerpo in self._cuerpos_de_error():
            for prohibido in prohibidos:
                with self.subTest(prohibido=prohibido):
                    self.assertNotIn(prohibido, cuerpo)

    def test_los_errores_del_sobre_son_json(self):
        self.client.force_login(self.telefonista)
        respuesta = self.client.get(RUTA_ENTREGAS)
        self.assertEqual(respuesta['Content-Type'], 'application/json')

    def test_el_500_del_api_usa_el_sobre(self):
        """§10 #13 cerrado: un fallo inesperado responde JSON, no la página HTML.

        Antes la app recibía HTML de Django y rompía al parsear la respuesta.
        """
        with patch.object(entregas, 'serializar_pedido', side_effect=RuntimeError('x')):
            self.client.force_login(self.camionero)
            respuesta = self.client.get(RUTA_ENTREGAS)

        self.assertEqual(respuesta.status_code, 500)
        self.assertEqual(respuesta['Content-Type'], 'application/json')
        cuerpo = respuesta.json()
        self.assertEqual(cuerpo['error']['codigo'], 'error_interno')
        self.assertIn('servidor_ahora', cuerpo)

    def test_el_500_no_expone_la_traza(self):
        """Con DEBUG=False la traza no viaja al cliente (sí al log del server)."""
        with patch.object(entregas, 'serializar_pedido', side_effect=RuntimeError('secreto')):
            self.client.force_login(self.camionero)
            respuesta = self.client.get(RUTA_ENTREGAS)

        cuerpo = respuesta.content.decode()
        for prohibido in ('secreto', 'RuntimeError', 'Traceback', 'serializar_pedido'):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, cuerpo)

    def test_el_500_deja_un_rastro_acotado_en_el_log(self):
        """Lo que no va al cliente sí queda para operaciones, y sin el mensaje."""
        with patch.object(entregas, 'serializar_pedido', side_effect=RuntimeError('secreto')):
            self.client.force_login(self.camionero)
            with self.assertLogs('security', level='ERROR') as capturado:
                self.client.get(RUTA_ENTREGAS)

        registro = '\n'.join(capturado.output)
        self.assertIn('API_ERROR_INTERNO', registro)
        self.assertIn(RUTA_ENTREGAS, registro)
        self.assertNotIn('secreto', registro)

    def test_la_web_conserva_su_pagina_de_error(self):
        """El handler es del API: fuera de `/api/v1/` la web sigue igual."""
        respuesta = handler500(RequestFactory().get('/pedidos/mios/'))
        self.assertEqual(respuesta.status_code, 500)
        self.assertNotIn('application/json', respuesta['Content-Type'])


class IdempotenciaAusenteTest(BaseSeguridadTest):
    """Hueco conocido del contrato (§6.2): ningún mutador exige Idempotency-Key."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.camionero)

    def test_documenta_que_reintentar_el_tarreo_duplica_la_venta(self):
        """La app encola acciones offline: un reintento hoy cobra dos veces.

        La web se protege con un `form_token` de sesión; el API iba a usar
        `Idempotency-Key` y todavía no lo implementa. Cuando se implemente, este
        test debe cambiar a `assertEqual(Pedido.objects.count(), 1)`.
        """
        payload = self._payload_tarreo()

        primera = self.client.post(
            RUTA_TARREO, data=json.dumps(payload), content_type='application/json'
        )
        segunda = self.client.post(
            RUTA_TARREO, data=json.dumps(payload), content_type='application/json'
        )

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(
            Pedido.objects.filter(origen='tarreo').count(), 2,
            'si esto falla es porque ya hay idempotencia: actualizar el test',
        )

    def test_documenta_que_un_reintento_de_accion_devuelve_409_y_no_la_respuesta(self):
        """El estado final es correcto, pero la app no puede distinguir
        "ya lo hice" de "no pude y reintento"."""
        pedido = self._pedido()

        primera = self.client.post(f'/api/v1/entregas/{pedido.pk}/tomar/')
        segunda = self.client.post(f'/api/v1/entregas/{pedido.pk}/tomar/')

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(
            Pedido.objects.filter(estado='en_ruta').count(), 1,
            'el estado no se duplica: el problema es la respuesta, no la escritura',
        )


class RespuestasDelSobreTest(BaseSeguridadTest):
    """El sobre del §5 y el catálogo cerrado de códigos, sobre el borde."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_codigo_desconocido_revienta_en_desarrollo(self):
        """Un código fuera del contrato es un bug de programación, no un 500 mudo."""
        with self.assertRaises(ValueError):
            historial.respuestas.error('inventado')

    def test_toda_respuesta_de_error_trae_servidor_ahora(self):
        self.client.force_login(self.telefonista)
        cuerpo = self.client.get(RUTA_ENTREGAS).json()

        self.assertIn('servidor_ahora', cuerpo)
        momento = datetime.fromisoformat(cuerpo['servidor_ahora'])
        self.assertIsNotNone(momento.tzinfo, 'la fecha debe traer zona horaria')

    def test_el_405_tambien_usa_el_sobre(self):
        respuesta = self.client.get('/api/v1/entregas/1/tomar/')
        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(
            respuesta.json()['error']['codigo'], 'metodo_no_permitido'
        )

    def test_la_respuesta_no_se_puede_cachear(self):
        self.client.force_login(self.camionero)
        for ruta in (RUTA_ENTREGAS, RUTA_BALONES, RUTA_VERSION):
            with self.subTest(ruta=ruta):
                self.assertIn('no-store', self.client.get(ruta)['Cache-Control'])
