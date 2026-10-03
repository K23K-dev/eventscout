"""Google sign-in through Supabase: check the access token the browser sends."""

import asyncio
import logging
import time
from uuid import UUID

import httpx
import jwt

logger = logging.getLogger(__name__)

# Supabase signs access tokens with the project's ES256 key; only that algorithm is accepted.
ALGORITHMS = ["ES256"]


class InvalidToken(Exception):
    """Garbled, forged, expired, or issued to someone else."""


class KeysUnavailable(Exception):
    """The project's public signing keys couldn't be fetched."""


class TokenVerifier:
    """Verifies Supabase access tokens against the project's published signing keys."""

    def __init__(self, supabase_url: str) -> None:
        self._issuer = f"{supabase_url.rstrip('/')}/auth/v1"
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def user_id(self, token: str) -> UUID:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise InvalidToken from exc
        key = await self._key(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience="authenticated",
                issuer=self._issuer,
                leeway=30,  # Clock drift between Supabase and this server.
                options={"require": ["exp", "iat", "sub"]},
            )
            if claims.get("is_anonymous"):
                raise InvalidToken("Anonymous sessions can't use AI search.")
            return UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError) as exc:
            raise InvalidToken from exc

    async def _key(self, kid: object) -> jwt.PyJWK:
        # Refresh every ten minutes so revoked keys drop out, and at most once a minute for an
        # unknown key ID (a rotation), so made-up tokens can't trigger a fetch each.
        async with self._lock:
            age = time.monotonic() - self._fetched_at
            if age > 600 or (kid not in self._keys and age > 60):
                try:
                    self._keys = await self._fetch()
                    self._fetched_at = time.monotonic()
                except KeysUnavailable:
                    if not self._keys:
                        raise
                    logger.warning("Keeping the cached signing keys; the refresh failed")
                    self._fetched_at = time.monotonic() - 540  # Retry in a minute.
        if not isinstance(kid, str) or kid not in self._keys:
            raise InvalidToken("Unknown signing key.")
        return self._keys[kid]

    async def _fetch(self) -> dict[str, jwt.PyJWK]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self._issuer}/.well-known/jwks.json")
                response.raise_for_status()
                keys = response.json()["keys"]
            return {key["kid"]: jwt.PyJWK(key) for key in keys if key.get("alg") in ALGORITHMS}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError) as exc:
            raise KeysUnavailable from exc
