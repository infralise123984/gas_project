"""Push móvil (FCM) — canal de la app nativa (docs/API_MOVIL.md §7.7).

Módulo **aparte** de ``push_notifications.py`` a propósito: Web Push (VAPID, PWA)
y FCM (app nativa) son dos transportes con ciclos de vida distintos, y el
contrato prohíbe tocar ``notificar_nuevo_pedido``. Acá vive solo el móvil, y
nada de lo existente cambia de comportamiento.

Sin credenciales configuradas el módulo no envía nada y responde
``(False, 'FCM no configurado')``: mismo criterio que VAPID vacío, la PWA sigue
recibiendo sus avisos y la app funciona sin notificaciones.

**Credenciales:** la cuenta de servicio de Firebase (JSON) se inyecta por
variable de entorno y se lee desde ``settings.FCM_CREDENCIALES``. Nunca en el
repositorio: ``private_key`` es un secreto y no se registra en logs.

**HTTP v1, no la API legacy:** las APIs de servidor legacy se deprecaron en
septiembre de 2026 (apagado anunciado para el 29-09-2027), así que se usa
``/v1/projects/<id>/messages:send`` con un token OAuth2 firmado en RS256.

**Sin dependencias nuevas:** ``cryptography`` y ``requests`` ya están instalados
como dependencias transitivas de ``pywebpush``.

**El token del dispositivo es una credencial de envío**: permite hacerle llegar
avisos a un teléfono concreto. Nunca se escribe completo en un log ni se
devuelve al cliente; cuando hay que identificarlo se usa un prefijo corto.

**Se apunta con `token`, no con `fid`:** el plugin de Flutter todavía no expone
los Firebase Installation IDs, así que el teléfono entrega un *registration
token*. La v1 lo deprecia a favor de `fid`, pero lo soporta durante la
transición, que es donde estamos (`docs/API_MOVIL.md` §7.7).
"""

import base64
import json
import logging
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.core.cache import cache

# Los helpers del texto del aviso se reutilizan a propósito desde
# `push_notifications`: el cuerpo del aviso tiene que ser **uno solo** entre los
# dos canales. Son privados de ese módulo porque son detalle de implementación
# suya; se importan acá en vez de copiarlos para que no puedan divergir.
from mockups.push_notifications import _resumen_ubicaciones_pedidos, _ubicacion_pedido

audit_logger = logging.getLogger('audit')

URL_OAUTH = 'https://oauth2.googleapis.com/token'
URL_FCM = 'https://fcm.googleapis.com/v1/projects/{proyecto}/messages:send'
ALCANCE_OAUTH = 'https://www.googleapis.com/auth/firebase.messaging'

# Un FCM lento no puede colgar la respuesta al usuario: el aviso viaja después
# de una acción de negocio que ya se confirmó.
TIEMPO_LIMITE = 10

CLAVE_TOKEN_ACCESO = 'api.v1.fcm.token_de_acceso'

# El canal lo crea la app (flutter_local_notifications). Si no coincide, Android
# usa el canal por defecto: se degrada el aspecto, no el aviso.
CANAL_ANDROID = 'pedidos'

# Único error que significa "este token ya no sirve" (app desinstalada o datos
# borrados). `INVALID_ARGUMENT` **no** entra acá a propósito: también se responde
# por un payload mal formado, y desactivar el dispositivo por un bug propio
# dejaría a un camionero sin avisos en silencio.
ERRORES_DE_TOKEN_MUERTO = ('UNREGISTERED',)

# Códigos que se buscan en el texto cuando la respuesta no trae JSON.
CODIGOS_CONOCIDOS = ERRORES_DE_TOKEN_MUERTO + (
    'INVALID_ARGUMENT',
    'QUOTA_EXCEEDED',
    'SENDER_ID_MISMATCH',
    'THIRD_PARTY_AUTH_ERROR',
    'UNAUTHENTICATED',
    'UNAVAILABLE',
    'INTERNAL',
)


def _credenciales():
    """Cuenta de servicio desde ``settings``, o ``None`` si no está configurada."""
    credenciales = getattr(settings, 'FCM_CREDENCIALES', None)
    if not isinstance(credenciales, dict):
        return None
    if not all(
        credenciales.get(campo)
        for campo in ('project_id', 'client_email', 'private_key')
    ):
        return None
    return credenciales


def _base64_url(datos):
    """base64url sin relleno, como exige el JWT (RFC 7515)."""
    return base64.urlsafe_b64encode(datos).rstrip(b'=')


def _jwt_firmado(credenciales, ahora):
    """JWT del flujo "jwt-bearer" que Google acepta para pedir el access token."""
    cabecera = {'alg': 'RS256', 'typ': 'JWT'}
    reclamos = {
        'iss': credenciales['client_email'],
        'scope': ALCANCE_OAUTH,
        'aud': URL_OAUTH,
        'iat': int(ahora),
        'exp': int(ahora) + 3600,
    }
    cuerpo = b'.'.join(
        [
            _base64_url(json.dumps(cabecera, separators=(',', ':')).encode('utf-8')),
            _base64_url(json.dumps(reclamos, separators=(',', ':')).encode('utf-8')),
        ]
    )

    llave = serialization.load_pem_private_key(
        credenciales['private_key'].encode('utf-8'), password=None
    )
    firma = llave.sign(cuerpo, padding.PKCS1v15(), hashes.SHA256())
    return (cuerpo + b'.' + _base64_url(firma)).decode('ascii')


def token_de_acceso():
    """``(token, None)`` o ``(None, error)``.

    El token OAuth2 se cachea hasta poco antes de vencer: sin caché serían dos
    llamadas HTTP por notificación, con caché una por hora. Si la caché no está
    disponible se pide uno nuevo: es más caro, no incorrecto.
    """
    credenciales = _credenciales()
    if credenciales is None:
        return None, 'FCM no configurado'

    try:
        guardado = cache.get(CLAVE_TOKEN_ACCESO)
    except Exception:
        guardado = None
    if guardado:
        return guardado, None

    try:
        respuesta = requests.post(
            URL_OAUTH,
            data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': _jwt_firmado(credenciales, time.time()),
            },
            timeout=TIEMPO_LIMITE,
        )
    except requests.RequestException as error_red:
        return None, f'sin_conexion_oauth ({error_red.__class__.__name__})'

    if respuesta.status_code != 200:
        # No se registra el cuerpo: puede traer detalles de la cuenta de servicio.
        audit_logger.warning(f'PUSH_MOVIL_OAUTH_FALLIDO | Status: {respuesta.status_code}')
        return None, f'credenciales_rechazadas ({respuesta.status_code})'

    try:
        datos = respuesta.json()
    except ValueError:
        return None, 'credenciales_ilegibles'

    acceso = datos.get('access_token')
    if not acceso:
        return None, 'credenciales_ilegibles'

    try:
        cache.set(
            CLAVE_TOKEN_ACCESO,
            acceso,
            max(60, int(datos.get('expires_in', 3600)) - 60),
        )
    except Exception:
        # La caché es una optimización: si falla, se paga una llamada extra.
        pass
    return acceso, None


def _codigo_de_error(respuesta):
    """``(codigo, detalle_corto)`` de un error de FCM.

    El ``errorCode`` viaja en ``error.details[].errorCode``. Si el cuerpo no es
    JSON se busca el código en el texto: la misma defensa que usa
    ``push_notifications._is_expired_subscription_error`` con pywebpush.
    """
    cuerpo = None
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = None

    if isinstance(cuerpo, dict):
        error = cuerpo.get('error')
        if isinstance(error, dict):
            for detalle in error.get('details') or []:
                if isinstance(detalle, dict) and detalle.get('errorCode'):
                    return detalle['errorCode'], str(error.get('message') or '')[:120]
            if error.get('status'):
                return str(error['status']), str(error.get('message') or '')[:120]

    texto = respuesta.text or ''
    for codigo in CODIGOS_CONOCIDOS:
        if codigo in texto:
            return codigo, ''
    return None, texto[:120]


def enviar_fcm(token, title, body, datos=None, canal=CANAL_ANDROID, etiqueta=None):
    """Envía una notificación a un dispositivo. Devuelve ``(ok, error)``.

    El mensaje lleva ``notification`` y ``data`` juntos, y ahí está toda la
    eficiencia del diseño: Android pinta la notificación **sin despertar la app y
    sin una sola petición al API**. Mandar un mensaje silencioso que obligue a la
    app a consultar sería más caro en batería y en datos, y no más confiable.

    ``etiqueta`` es el *tag* de Android: dos avisos del mismo pedido se
    reemplazan en vez de apilarse.
    """
    credenciales = _credenciales()
    if credenciales is None:
        return False, 'FCM no configurado'

    acceso, error_acceso = token_de_acceso()
    if error_acceso is not None:
        return False, error_acceso

    notificacion = {'channel_id': canal, 'sound': 'default'}
    if etiqueta:
        notificacion['tag'] = etiqueta

    mensaje = {
        'message': {
            'token': token,
            'notification': {'title': title, 'body': body},
            'android': {'priority': 'high', 'notification': notificacion},
            # FCM exige que todo valor de `data` sea texto.
            'data': {
                str(clave): str(valor) for clave, valor in (datos or {}).items()
            },
        }
    }

    url = URL_FCM.format(proyecto=credenciales['project_id'])
    try:
        respuesta = requests.post(
            url,
            json=mensaje,
            headers={'Authorization': f'Bearer {acceso}'},
            timeout=TIEMPO_LIMITE,
        )
    except requests.RequestException as error_red:
        # Un fallo de red no dice nada del token: el dispositivo sigue válido.
        return False, f'sin_conexion_fcm ({error_red.__class__.__name__})'

    if respuesta.status_code == 200:
        return True, None

    codigo, detalle = _codigo_de_error(respuesta)
    if codigo in ERRORES_DE_TOKEN_MUERTO:
        return False, 'dispositivo_invalido'
    return False, f'{codigo or respuesta.status_code}: {detalle}'.strip(': ')


def notificar_nuevo_pedido_movil(pedido, excluir_usuario_id=None, title=None):
    """Avisa a los camioneros con la app instalada de un pedido disponible.

    Mismos filtros que ``notificar_nuevo_pedido`` para no inventar una segunda
    definición de "pedido nuevo": solo ``pendiente`` y de teléfono o tarreo (los
    del local no se avisan, y un pedido ya tomado tampoco).

    ``excluir_usuario_id``: el camionero que devuelve un pedido no recibe la
    alerta del pedido que él mismo liberó (regla de la web, §7.3).

    Devuelve cuántos dispositivos se notificaron.
    """
    from mockups.models import DispositivoPush

    if pedido.estado != 'pendiente' or pedido.origen not in ('telefono', 'tarreo'):
        return 0

    dispositivos = DispositivoPush.objects.filter(
        activa=True,
        usuario__rol='camionero',
        usuario__is_active=True,
    ).select_related('usuario')

    if excluir_usuario_id:
        dispositivos = dispositivos.exclude(usuario_id=excluir_usuario_id)

    if not dispositivos.exists():
        audit_logger.info(
            f'PUSH_MOVIL_NO_DISPOSITIVOS | Pedido #{pedido.id} - sin teléfonos registrados'
        )
        return 0

    titulo = title or '🚚 ¡Nuevo Pedido!'

    # Mismo texto que el aviso de la PWA. La composición está duplicada a
    # propósito: `notificar_nuevo_pedido` no se puede tocar (regla del §2 del
    # contrato) y sus helpers de ubicación sí se reutilizan.
    detalles = pedido.detalles.all()
    if detalles:
        items = ', '.join(f'{d.cantidad}x {d.balon.nombre}' for d in detalles[:3])
        if detalles.count() > 3:
            items += f' (+{detalles.count() - 3} más)'
    else:
        items = 'Ver detalles'

    cuerpo = f'📍 {_ubicacion_pedido(pedido)}\n{items}'
    etiqueta = f'pedido-{pedido.id}'

    titulo_log = titulo.encode('ascii', 'ignore').decode('ascii') or 'Notificacion'
    enviados = 0
    sin_servicio = []
    fallidos = []

    for dispositivo in dispositivos:
        ok, error = enviar_fcm(
            dispositivo.token,
            titulo,
            cuerpo,
            datos={'tipo': 'pedido_nuevo', 'pedido_id': pedido.id},
            etiqueta=etiqueta,
        )
        if ok:
            enviados += 1
            continue

        # Prefijo corto del token: identifica el dispositivo sin ser la
        # credencial completa.
        pista = dispositivo.token[:8]
        if error == 'dispositivo_invalido':
            sin_servicio.append(dispositivo.id)
        else:
            fallidos.append(f'{dispositivo.usuario.username}|{pista}')
            audit_logger.warning(
                f'PUSH_MOVIL_FAIL | #{pedido.id} | User: {dispositivo.usuario.username} | '
                f'Token: {pista}... | Error: {error}'
            )

    if sin_servicio:
        DispositivoPush.objects.filter(id__in=sin_servicio).update(activa=False)
        audit_logger.info(
            f'PUSH_MOVIL_CLEANUP | Desactivados {len(sin_servicio)} dispositivos sin servicio'
        )

    audit_logger.info(
        f'PUSH_MOVIL_PEDIDO | #{pedido.id} | Title: {titulo_log} | '
        f'{enviados}/{dispositivos.count()} dispositivos notificados'
    )
    return enviados
