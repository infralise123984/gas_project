"""
Campos de modelo personalizados para seguridad.

Incluye EncryptedTextField: cifrado simétrico en reposo con Fernet (AES-128-CBC + HMAC).
La clave se deriva de settings.SECRET_KEY vía SHA-256.
"""
import base64
import hashlib

from django.conf import settings
from django.db import models

try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_FERNET = True
except ImportError:  # pragma: no cover
    HAS_FERNET = False


def _fernet():
    """Devuelve una instancia de Fernet derivada de SECRET_KEY."""
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class EncryptedTextField(models.TextField):
    """
    Campo de texto que cifra los datos en reposo.

    Compatible con migración desde texto plano: si el valor en BD no es un token
    Fernet válido (no empieza con 'gAAAAA'), se devuelve tal cual y se cifrará
    en el próximo save().
    """

    description = "Texto cifrado en reposo con Fernet"

    def __init__(self, *args, **kwargs):
        # Los tokens Fernet ocupan ~130 chars para valores típicos de TOTP (32 chars)
        kwargs.setdefault("max_length", 255)
        super().__init__(*args, **kwargs)

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        if not HAS_FERNET:  # pragma: no cover
            return value
        # Si ya está cifrado, no doble-cifrar
        if isinstance(value, str) and value.startswith("gAAAAA"):
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        if not HAS_FERNET:  # pragma: no cover
            return value
        if value.startswith("gAAAAA"):
            try:
                return _fernet().decrypt(value.encode()).decode()
            except InvalidToken:
                return value
        return value  # texto plano legado — se cifrará en próximo save

    def to_python(self, value):
        return value
