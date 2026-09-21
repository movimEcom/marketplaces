"""OAuth2 (Authorization Code + PKCE) flow and token storage for Mercado Libre."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .config import API_BASE_URL, Settings

TOKEN_URL = f"{API_BASE_URL}/oauth/token"

# Refresh a bit before actual expiry to avoid races with in-flight requests.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    user_id: int
    expires_at: float  # unix timestamp

    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - _EXPIRY_SAFETY_MARGIN_SECONDS)


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(settings: Settings, state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{settings.authorization_base_url}?{urlencode(params)}"


def exchange_code_for_tokens(settings: Settings, code: str, code_verifier: str) -> TokenSet:
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "code": code,
        "redirect_uri": settings.redirect_uri,
        "code_verifier": code_verifier,
    }
    response = httpx.post(TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    return _token_set_from_response(response.json())


def refresh_tokens(settings: Settings, refresh_token: str) -> TokenSet:
    payload = {
        "grant_type": "refresh_token",
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "refresh_token": refresh_token,
    }
    response = httpx.post(TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    return _token_set_from_response(response.json())


def _token_set_from_response(data: dict) -> TokenSet:
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user_id=data["user_id"],
        expires_at=time.time() + data["expires_in"],
    )


class TokenStore:
    """Persists the token set to a local JSON file (never committed to git)."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> TokenSet | None:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text())
        return TokenSet(**data)

    def save(self, tokens: TokenSet) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(tokens), indent=2))
        self._path.chmod(0o600)


def get_valid_access_token(settings: Settings) -> TokenSet:
    """Return a TokenSet with a non-expired access_token, refreshing if needed."""
    store = TokenStore(settings.token_path)
    tokens = store.load()
    if tokens is None:
        raise RuntimeError(
            "No Mercado Libre tokens found. Run `meli-mx-authorize` once to complete "
            "the OAuth login for your seller account."
        )
    if tokens.is_expired():
        tokens = refresh_tokens(settings, tokens.refresh_token)
        store.save(tokens)
    return tokens
