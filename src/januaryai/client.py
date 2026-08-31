from __future__ import annotations

from types import MappingProxyType
from typing import Any
import httpx

from ._generated import AsyncRoot, AsyncShared, SyncRoot, SyncShared
from ._runtime import AsyncHTTP, SyncHTTP, UNSET, user_context
from .types import AsyncClientTokenIssuer, ClientScope, ClientToken, ClientTokenIssuer, CreateClientTokenInput
from .validation import validate_create_input


class ClientTokens:
    """Compatibility facade for the prototype. Prefer mint_client_token."""

    def __init__(self, client: January, issuer: ClientTokenIssuer | None) -> None:
        self._client = client
        self._issuer = issuer

    def create(self, *, end_user_id: str, scopes: list[ClientScope] | None = None,
               ttl_seconds: int | None = None) -> ClientToken:
        request = CreateClientTokenInput(end_user_id, scopes, ttl_seconds)
        validate_create_input(request)
        if self._issuer is not None:
            return self._issuer.create(request)
        result = self._client.mint_client_token(end_user_id=end_user_id,
                                               scopes=scopes if scopes is not None else UNSET,
                                               ttl_seconds=ttl_seconds if ttl_seconds is not None else UNSET)
        return ClientToken(token=result.token, expires_in=int(result.expires_in), expires_at=result.expires_at)


class AsyncClientTokens:
    """Async compatibility facade; performs the same single token request."""

    def __init__(self, client: AsyncJanuary, issuer: AsyncClientTokenIssuer | None) -> None:
        self._client = client
        self._issuer = issuer

    async def create(self, *, end_user_id: str, scopes: list[ClientScope] | None = None,
                     ttl_seconds: int | None = None) -> ClientToken:
        request = CreateClientTokenInput(end_user_id, scopes, ttl_seconds)
        validate_create_input(request)
        if self._issuer is not None:
            return await self._issuer.create(request)
        result = await self._client.mint_client_token(end_user_id=end_user_id,
                                                     scopes=scopes if scopes is not None else UNSET,
                                                     ttl_seconds=ttl_seconds if ttl_seconds is not None else UNSET)
        return ClientToken(token=result.token, expires_in=int(result.expires_in), expires_at=result.expires_at)


class January(SyncRoot):
    """Reusable trusted-server client. Credentials are explicit; no global state."""

    def __init__(self, *, secret_key: str | None = None,
                 base_url: str = "https://partners.january.ai", timeout: float = 30.0,
                 http_client: httpx.Client | None = None,
                 client_token_issuer: ClientTokenIssuer | None = None) -> None:
        object.__setattr__(self, "_transport", SyncHTTP(secret_key, base_url, timeout, http_client))
        object.__setattr__(self, "_context", MappingProxyType({}))
        self._issuer = client_token_issuer

    @property
    def client_tokens(self) -> ClientTokens:
        return ClientTokens(self, self._issuer)

    def for_user(self, end_user_id: str, *, end_user_timezone: str | None = None) -> SyncShared:
        return SyncShared(self._transport, user_context(end_user_id, end_user_timezone))

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> January:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AsyncJanuary(AsyncRoot):
    """Native async HTTPX client; task cancellation propagates to the request."""

    def __init__(self, *, secret_key: str | None = None,
                 base_url: str = "https://partners.january.ai", timeout: float = 30.0,
                 http_client: httpx.AsyncClient | None = None,
                 client_token_issuer: AsyncClientTokenIssuer | None = None) -> None:
        object.__setattr__(self, "_transport", AsyncHTTP(secret_key, base_url, timeout, http_client))
        object.__setattr__(self, "_context", MappingProxyType({}))
        self._issuer = client_token_issuer

    @property
    def client_tokens(self) -> AsyncClientTokens:
        return AsyncClientTokens(self, self._issuer)

    def for_user(self, end_user_id: str, *, end_user_timezone: str | None = None) -> AsyncShared:
        return AsyncShared(self._transport, user_context(end_user_id, end_user_timezone))

    async def close(self) -> None:
        await self._transport.close()

    async def aclose(self) -> None:
        await self.close()

    async def __aenter__(self) -> AsyncJanuary:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


JanuaryClient = January
AsyncJanuaryClient = AsyncJanuary
