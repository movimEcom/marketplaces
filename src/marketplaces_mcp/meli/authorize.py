"""One-time interactive OAuth login for a Mercado Libre seller account.

Run with `meli-mx-authorize` (or `python -m marketplaces_mcp.meli.authorize`).
It starts a temporary local HTTP server on the configured redirect URI,
opens the Mercado Libre consent screen in your browser, captures the
authorization code, exchanges it for tokens, and saves them locally.
"""

from __future__ import annotations

import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .auth import (
    TokenStore,
    build_authorization_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
)
from .config import load_settings

_RESULT: dict[str, str] = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        error = query.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if error:
            _RESULT["error"] = error
            self.wfile.write(f"<h1>Autorización fallida</h1><p>{error}</p>".encode())
        else:
            _RESULT["code"] = code or ""
            _RESULT["state"] = state or ""
            self.wfile.write(
                b"<h1>Listo</h1><p>Ya puedes cerrar esta pestana y volver a la terminal.</p>"
            )

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - silence default logging
        pass


def main() -> None:
    settings = load_settings()
    redirect = urlparse(settings.redirect_uri)
    if redirect.hostname not in ("localhost", "127.0.0.1"):
        print(
            "MELI_REDIRECT_URI debe apuntar a un servidor local (localhost) para "
            "que este script pueda capturar el codigo de autorizacion.",
            file=sys.stderr,
        )
        sys.exit(1)

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = build_authorization_url(settings, state=state, code_challenge=code_challenge)

    port = redirect.port or 80
    server = HTTPServer((redirect.hostname, port), _CallbackHandler)

    print(f"Abriendo el navegador para autorizar la app en Mercado Libre ({settings.site_id})...")
    print(f"Si no se abre solo, visita:\n{auth_url}\n")
    webbrowser.open(auth_url)

    while "code" not in _RESULT and "error" not in _RESULT:
        server.handle_request()

    if "error" in _RESULT:
        print(f"Mercado Libre devolvio un error: {_RESULT['error']}", file=sys.stderr)
        sys.exit(1)

    if _RESULT.get("state") != state:
        print("El parametro 'state' no coincide; abortando por seguridad.", file=sys.stderr)
        sys.exit(1)

    tokens = exchange_code_for_tokens(settings, code=_RESULT["code"], code_verifier=code_verifier)
    TokenStore(settings.token_path).save(tokens)

    print(f"Autenticacion completada para el usuario {tokens.user_id}.")
    print(f"Tokens guardados en {settings.token_path}")


if __name__ == "__main__":
    main()
