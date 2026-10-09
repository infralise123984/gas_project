"""Registro del dispositivo para las notificaciones de la app nativa (§7.7).

El endpoint es **transporte + validación**: guarda el token de FCM que la app
obtuvo al iniciar sesión. No hay lógica de negocio que reutilizar de la web
—el canal móvil es nuevo— pero tampoco se inventa nada: el token es un dato del
dispositivo, no del pedido.

Decisiones que importan:

* **Idempotente por diseño:** la clave es el ``token``, no el par
  (usuario, token). Reintentar el registro —la app lo hace en cada arranque y
  cuando FCM rota el token— actualiza la fila en vez de duplicarla. Es la misma
  propiedad que busca el ``Idempotency-Key`` del §6.2, resuelta donde
  corresponde: un upsert.
* **Reasignación explícita:** si el mismo teléfono entra con otro camionero, el
  token se mueve al usuario nuevo. Eso es lo esperado (un teléfono por turno),
  pero se registra en el logger de seguridad, porque también es lo que se vería
  si alguien reutilizara un token ajeno.
* **El token nunca se devuelve ni se registra completo.** Es una credencial de
  envío: quien lo tenga puede hacerle llegar notificaciones a ese teléfono.
"""

import logging

from django.views.decorators.cache import never_cache

from mockups.api import respuestas
from mockups.api.acceso import ROLES_CAMIONERO, acceso_api, cuerpo_json, solo_post
from mockups.api.limites import limitar
from mockups.models import DispositivoPush

audit_logger = logging.getLogger('audit')
security_logger = logging.getLogger('security')

CAMPOS_REGISTRO = ('token', 'plataforma', 'app_version')
CAMPOS_BAJA = ('token',)

# Igual al `max_length` del modelo: se valida acá para responder 400 con sobre
# en vez de dejar que reviente la base con un 500.
LARGO_MAXIMO_TOKEN = 255
LARGO_MAXIMO_VERSION = 32

# Se derivan del modelo en vez de repetirlas (mientras solo exista Android, la
# única plataforma válida es esa).
PLATAFORMAS = tuple(valor for valor, _ in DispositivoPush._meta.get_field('plataforma').choices)


def _token_valido(valor):
    """Token razonable, o ``None``. Nunca se trunca: truncar rompería el envío."""
    if not isinstance(valor, str):
        return None
    token = valor.strip()
    if not token or len(token) > LARGO_MAXIMO_TOKEN:
        return None
    return token


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('dispositivos')
def registrar_dispositivo(request):
    """Registra (o refresca) el teléfono del camionero autenticado (§7.7)."""
    datos, error_respuesta = cuerpo_json(request, permitidos=CAMPOS_REGISTRO)
    if error_respuesta is not None:
        return error_respuesta

    token = _token_valido(datos.get('token'))
    if token is None:
        return respuestas.error(
            'validacion', mensaje='Falta el identificador del dispositivo o no es válido.'
        )

    plataforma = datos.get('plataforma', 'android')
    if not isinstance(plataforma, str) or plataforma.strip() not in PLATAFORMAS:
        return respuestas.error(
            'validacion', mensaje='La plataforma del dispositivo no es válida.'
        )
    plataforma = plataforma.strip()

    app_version = datos.get('app_version', '')
    if not isinstance(app_version, str) or len(app_version) > LARGO_MAXIMO_VERSION:
        return respuestas.error(
            'validacion', mensaje='La versión de la app no es válida.'
        )

    dispositivo, creado = DispositivoPush.objects.update_or_create(
        token=token,
        defaults={
            'usuario': request.user,
            'plataforma': plataforma,
            'app_version': app_version.strip(),
            'activa': True,
        },
    )

    if not creado and dispositivo.usuario_id != request.user.id:
        # El teléfono cambió de manos. Es legítimo (turnos, reinstalación), pero
        # se deja rastro: un token ajeno reutilizado se ve exactamente igual.
        security_logger.warning(
            f'PUSH_MOVIL_TOKEN_REASIGNADO | De: {dispositivo.usuario_id} | '
            f'A: {request.user.username} | Token: {token[:8]}...'
        )

    audit_logger.info(
        f'PUSH_MOVIL_REGISTRO | User: {request.user.username} | '
        f'Plataforma: {plataforma} | {"alta" if creado else "refresco"}'
    )
    return respuestas.ok({'registrado': True, 'alta': creado})


@never_cache
@solo_post
@acceso_api(ROLES_CAMIONERO)
@limitar('dispositivos')
def baja_dispositivo(request):
    """Deja de enviar avisos a ese teléfono (§7.7).

    Se llama al cerrar sesión. Si el token no existe o es de otro usuario, la
    respuesta es la misma: no se confirma desde acá si un token ajeno existe.
    """
    datos, error_respuesta = cuerpo_json(request, permitidos=CAMPOS_BAJA)
    if error_respuesta is not None:
        return error_respuesta

    token = _token_valido(datos.get('token'))
    if token is None:
        return respuestas.error(
            'validacion', mensaje='Falta el identificador del dispositivo o no es válido.'
        )

    eliminados, _ = DispositivoPush.objects.filter(
        token=token, usuario=request.user
    ).delete()

    if eliminados:
        audit_logger.info(
            f'PUSH_MOVIL_BAJA | User: {request.user.username} | Token: {token[:8]}...'
        )
    return respuestas.ok({'registrado': False})
