from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import httpx

from ._constants import DEFAULT_MAX_RETRIES
from ._generated import AsyncRoot, AsyncShared, SyncRoot, SyncShared
from ._runtime import UNSET, AsyncHTTP, SyncHTTP, user_context
from .errors import JanuaryConfigurationError
from .types import (
    AsyncClientTokenIssuer,
    ClientScope,
    ClientToken,
    ClientTokenIssuer,
    CreateClientTokenInput,
)
from .validation import validate_create_input


def _credential(secret_key: str | None, api_key: str | None, demo: bool) -> str | None:
    if secret_key is not None and api_key is not None:
        raise JanuaryConfigurationError("Pass api_key or secret_key, not both")
    key = secret_key if secret_key is not None else api_key
    if key is None and not demo:
        key = os.environ.get("JANUARY_API_KEY", "").strip()
        if not key:
            raise JanuaryConfigurationError("Set JANUARY_API_KEY or pass api_key")
    return key


class ClientTokens:
    """Compatibility facade for the prototype. Prefer mint_client_token."""

    def __init__(self, client: January, issuer: ClientTokenIssuer | None) -> None:
        self._client = client
        self._issuer = issuer

    def create(
        self,
        *,
        end_user_id: str,
        scopes: list[ClientScope] | None = None,
        ttl_seconds: int | None = None,
    ) -> ClientToken:
        request = CreateClientTokenInput(end_user_id, scopes, ttl_seconds)
        validate_create_input(request)
        if self._issuer is not None:
            return self._issuer.create(request)
        result = self._client.mint_client_token(
            end_user_id=end_user_id,
            scopes=scopes if scopes is not None else UNSET,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else UNSET,
        )
        return ClientToken(
            token=result.token, expires_in=int(result.expires_in), expires_at=result.expires_at
        )


class AsyncClientTokens:
    """Async compatibility facade; uses the root client's token retry policy."""

    def __init__(self, client: AsyncJanuary, issuer: AsyncClientTokenIssuer | None) -> None:
        self._client = client
        self._issuer = issuer

    async def create(
        self,
        *,
        end_user_id: str,
        scopes: list[ClientScope] | None = None,
        ttl_seconds: int | None = None,
    ) -> ClientToken:
        request = CreateClientTokenInput(end_user_id, scopes, ttl_seconds)
        validate_create_input(request)
        if self._issuer is not None:
            return await self._issuer.create(request)
        result = await self._client.mint_client_token(
            end_user_id=end_user_id,
            scopes=scopes if scopes is not None else UNSET,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else UNSET,
        )
        return ClientToken(
            token=result.token, expires_in=int(result.expires_in), expires_at=result.expires_at
        )


class January(SyncRoot):
    """Reusable synchronous server client with immutable user-scoped views.

    api_key/secret_key override JANUARY_API_KEY. The SDK never reads .env files.
    Production is the default endpoint. Reuse one client and close it with a
    context manager. max_retries=2 allows bounded, error-code-aware retries;
    max_retries=0 makes exactly one attempt. Caller-owned HTTP clients stay open.
    timeout accepts seconds or a finite httpx.Timeout for per-phase limits.
    """

    def __init__(
        self,
        *,
        secret_key: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://partners.january.ai",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
        client_token_issuer: ClientTokenIssuer | None = None,
    ) -> None:
        key = _credential(secret_key, api_key, client_token_issuer is not None)
        object.__setattr__(
            self,
            "_transport",
            SyncHTTP(
                key,
                base_url,
                timeout,
                http_client,
                max_retries=max_retries,
                default_headers=default_headers,
            ),
        )
        object.__setattr__(self, "_context", MappingProxyType({}))
        self._issuer = client_token_issuer

    @property
    def client_tokens(self) -> ClientTokens:
        return ClientTokens(self, self._issuer)

    def for_user(self, end_user_id: str, *, end_user_timezone: str | None = None) -> SyncShared:
        """Bind a user and optional IANA timezone without mutating this client.

        The view exposes shared APIs only. Its user/timezone cannot be overridden
        by per-call parameters; account-wide token and credit APIs stay on the root.
        """
        return SyncShared(self._transport, user_context(end_user_id, end_user_timezone))

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> January:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AsyncJanuary(AsyncRoot):
    """Asyncio/Trio counterpart to January; all resource calls use await.

    Create and reuse the client inside one async application. Cancellation is
    propagated through requests and retry sleeps; photo preparation runs on a
    worker thread. Configuration matches January, including JANUARY_API_KEY.
    """

    def __init__(
        self,
        *,
        secret_key: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://partners.january.ai",
        timeout: float | httpx.Timeout | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        client_token_issuer: AsyncClientTokenIssuer | None = None,
    ) -> None:
        key = _credential(secret_key, api_key, client_token_issuer is not None)
        object.__setattr__(
            self,
            "_transport",
            AsyncHTTP(
                key,
                base_url,
                timeout,
                http_client,
                max_retries=max_retries,
                default_headers=default_headers,
            ),
        )
        object.__setattr__(self, "_context", MappingProxyType({}))
        self._issuer = client_token_issuer

    @property
    def client_tokens(self) -> AsyncClientTokens:
        return AsyncClientTokens(self, self._issuer)

    def for_user(self, end_user_id: str, *, end_user_timezone: str | None = None) -> AsyncShared:
        """Return an immutable shared-API view bound to this user and timezone."""
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
