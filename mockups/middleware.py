"""
Middleware de cabeceras de seguridad para GasFácil KIM GAS.

Añade Content-Security-Policy, Referrer-Policy y otras cabeceras
de defensa en profundidad que no se configuran desde settings.py.

CSP configurado con 'unsafe-inline' para scripts y estilos porque
el proyecto usa scripts inline en los templates. A medida que se
externalice el JS, se debe migrar a nonces o hashes.
"""


class SecurityHeadersMiddleware:
    """
    Añade cabeceras HTTP de seguridad a todas las respuestas.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # ── Content Security Policy ──────────────────────────
        # style-src 'unsafe-inline' es necesario para Bootstrap y estilos inline.
        # script-src 'unsafe-inline' es necesario para los scripts inline de los templates.
        # TODO: migrar a nonces/hashes cuando el JS se externalice.
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response["Content-Security-Policy"] = csp

        # ── Referrer Policy ──────────────────────────────────
        # Evita fugas de URLs completas en el header Referer
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # ── Cross-Origin Opener Policy ───────────────────────
        # Aísla el contexto de navegación contra ataques de canal lateral
        response["Cross-Origin-Opener-Policy"] = "same-origin"

        # ── Permissions Policy (antes Feature-Policy) ────────
        # Restringe APIs del navegador que no usamos
        response["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )

        return response
