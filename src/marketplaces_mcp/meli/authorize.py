"""One-time interactive OAuth login for a Mercado Libre seller account.

Two modes:

- `meli-mx-authorize` (no args): for running on your own machine. Starts a
  temporary local HTTP server on the configured redirect URI, opens the
  Mercado Libre consent screen in your browser, captures the authorization
  code, exchanges it for tokens, and saves them locally.
- `meli-mx-authorize start` / `meli-mx-authorize finish <redirect-url>`: for
  headless/remote environments (e.g. a cloud sandbox) where nothing on this
  machine can receive the browser redirect. `start` prints the consent URL
  to open in your own browser; after authorizing, Mercado Libre redirects to
  a URL that will likely fail to load (it points at "localhost") -- copy
  that full URL from the address bar and pass it to `finish`.
"""

from __future__ import annotations

import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .auth import (
    TokenStore,
    build_authorization_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
)
from .config import Settings, load_settings

_RESULT: dict[str, str] = {}


def _pending_auth_path(settings: Settings) -> Path:
    return settings.token_path.parent / "pending_auth.json"


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


def _start_manual(settings: Settings) -> None:
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = build_authorization_url(settings, state=state, code_challenge=code_challenge)

    pending_path = _pending_auth_path(settings)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(json.dumps({"code_verifier": code_verifier, "state": state}))
    pending_path.chmod(0o600)

    print(f"1. Abre esta URL en TU navegador (no en este servidor) y autoriza la app:\n")
    print(f"   {auth_url}\n")
    print("2. Mercado Libre te va a redirigir a una URL localhost que probablemente")
    print("   falle al cargar -- eso es normal. Copia la URL completa de la barra de")
    print("   direcciones (incluye ?code=...&state=...) y corre:\n")
    print('   meli-mx-authorize finish "<esa URL completa>"')


def _finish_manual(settings: Settings, redirect_url: str) -> None:
    pending_path = _pending_auth_path(settings)
    if not pending_path.exists():
        sys.exit("No hay una autorizacion pendiente. Corre 'meli-mx-authorize start' primero.")
    pending = json.loads(pending_path.read_text())

    query = parse_qs(urlparse(redirect_url).query)
    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    error = query.get("error", [None])[0]

    if error:
        sys.exit(f"Mercado Libre devolvio un error: {error}")
    if not code:
        sys.exit("La URL no trae un parametro 'code'. Revisa que sea la URL completa.")
    if state != pending["state"]:
        sys.exit("El parametro 'state' no coincide; corre 'meli-mx-authorize start' de nuevo.")

    tokens = exchange_code_for_tokens(settings, code=code, code_verifier=pending["code_verifier"])
    TokenStore(settings.token_path).save(tokens)
    pending_path.unlink(missing_ok=True)

    print(f"Autenticacion completada para el usuario {tokens.user_id}.")
    print(f"Tokens guardados en {settings.token_path}")


def _run_local_server_flow(settings: Settings) -> None:
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


def main() -> None:
    settings = load_settings()
    args = sys.argv[1:]

    if not args:
        _run_local_server_flow(settings)
    elif args[0] == "start":
        _start_manual(settings)
    elif args[0] == "finish" and len(args) == 2:
        _finish_manual(settings, args[1])
    else:
        sys.exit(
            "Uso:\n"
            "  meli-mx-authorize                 # flujo automatico (solo en tu maquina)\n"
            '  meli-mx-authorize start           # flujo manual, paso 1 (imprime la URL)\n'
            '  meli-mx-authorize finish "<url>"  # flujo manual, paso 2 (con la URL de redirect)'
        )


if __name__ == "__main__":
    main()
