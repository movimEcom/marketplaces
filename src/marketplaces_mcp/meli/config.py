"""Configuration for the Mercado Libre MCP server, loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Mercado Libre uses a per-country auth domain even though the token/API
# endpoints are shared. https://developers.mercadolibre.com.mx/en_us/authentication-and-authorization
_AUTH_DOMAINS = {
    "MLA": "auth.mercadolibre.com.ar",  # Argentina
    "MLB": "auth.mercadolivre.com.br",  # Brasil
    "MLC": "auth.mercadolibre.cl",  # Chile
    "MCO": "auth.mercadolibre.com.co",  # Colombia
    "MCR": "auth.mercadolibre.co.cr",  # Costa Rica
    "MEC": "auth.mercadolibre.com.ec",  # Ecuador
    "MLM": "auth.mercadolibre.com.mx",  # Mexico
    "MPA": "auth.mercadolibre.com.pa",  # Panama
    "MPY": "auth.mercadolibre.com.py",  # Paraguay
    "MPE": "auth.mercadolibre.com.pe",  # Peru
    "MLU": "auth.mercadolibre.com.uy",  # Uruguay
    "MLV": "auth.mercadolibre.com.ve",  # Venezuela
}

API_BASE_URL = "https://api.mercadolibre.com"


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    site_id: str
    token_path: Path

    @property
    def auth_domain(self) -> str:
        try:
            return _AUTH_DOMAINS[self.site_id]
        except KeyError as exc:
            raise ValueError(
                f"Unknown Mercado Libre site_id '{self.site_id}'. "
                f"Valid values: {', '.join(sorted(_AUTH_DOMAINS))}"
            ) from exc

    @property
    def authorization_base_url(self) -> str:
        return f"https://{self.auth_domain}/authorization"


def load_settings() -> Settings:
    client_id = os.environ.get("MELI_CLIENT_ID", "")
    client_secret = os.environ.get("MELI_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("MELI_REDIRECT_URI", "http://localhost:8765/callback")
    site_id = os.environ.get("MELI_SITE_ID", "MLM").upper()
    token_path = Path(
        os.environ.get(
            "MELI_TOKEN_PATH",
            str(Path.home() / ".config" / "marketplaces-mcp" / "meli_tokens.json"),
        )
    ).expanduser()

    if not client_id or not client_secret:
        raise RuntimeError(
            "MELI_CLIENT_ID and MELI_CLIENT_SECRET must be set (see .env.example). "
            "Create an application at https://developers.mercadolibre.com.mx/ first."
        )

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        site_id=site_id,
        token_path=token_path,
    )
