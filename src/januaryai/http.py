"""Compatibility token issuers using the generated mint_client_token operation."""
from __future__ import annotations

from .client import AsyncJanuary, January
from .types import ClientToken, CreateClientTokenInput


class HttpClientTokenIssuer:
    def __init__(self, *, secret_key: str, base_url: str = "https://partners.january.ai", timeout: float = 30.0) -> None:
        self._client = January(secret_key=secret_key, base_url=base_url, timeout=timeout)

    def create(self, request: CreateClientTokenInput) -> ClientToken:
        return self._client.client_tokens.create(end_user_id=request.end_user_id,
                                                scopes=list(request.scopes) if request.scopes is not None else None,
                                                ttl_seconds=request.ttl_seconds)

    def close(self) -> None:
        self._client.close()


class AsyncHttpClientTokenIssuer:
    def __init__(self, *, secret_key: str, base_url: str = "https://partners.january.ai", timeout: float = 30.0) -> None:
        self._client = AsyncJanuary(secret_key=secret_key, base_url=base_url, timeout=timeout)

    async def create(self, request: CreateClientTokenInput) -> ClientToken:
        return await self._client.client_tokens.create(end_user_id=request.end_user_id,
                                                      scopes=list(request.scopes) if request.scopes is not None else None,
                                                      ttl_seconds=request.ttl_seconds)

    async def close(self) -> None:
        await self._client.close()
