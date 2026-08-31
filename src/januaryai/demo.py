from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from .errors import JanuaryConfigurationError
from .types import ClientToken, CreateClientTokenInput
from .validation import validate_create_input


class _DemoIssuerBase:
    def __init__(
        self,
        *,
        access_token: str,
        expires_in: int = 3600,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        normalized = access_token.strip() if isinstance(access_token, str) else ""
        if not normalized:
            raise JanuaryConfigurationError("Demo access_token must be non-empty")
        if normalized.startswith("sk-"):
            raise JanuaryConfigurationError(
                "Refusing to expose a January sk- secret as a demo client token"
            )
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
            raise JanuaryConfigurationError(
                "Demo expires_in must be a positive integer number of seconds"
            )
        self._access_token = normalized
        self._expires_in = expires_in
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _create(self, request: CreateClientTokenInput) -> ClientToken:
        validate_create_input(request)
        expires_at = self._now() + timedelta(seconds=self._expires_in)
        return ClientToken(
            token=self._access_token,
            expires_in=self._expires_in,
            expires_at=expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )


class DemoClientTokenIssuer(_DemoIssuerBase):
    def create(self, request: CreateClientTokenInput) -> ClientToken:
        return self._create(request)


class AsyncDemoClientTokenIssuer(_DemoIssuerBase):
    async def create(self, request: CreateClientTokenInput) -> ClientToken:
        return self._create(request)


def create_demo_token_issuer(
    *,
    access_token: str,
    expires_in: int = 3600,
    now: Callable[[], datetime] | None = None,
) -> DemoClientTokenIssuer:
    return DemoClientTokenIssuer(
        access_token=access_token,
        expires_in=expires_in,
        now=now,
    )


def create_async_demo_token_issuer(
    *,
    access_token: str,
    expires_in: int = 3600,
    now: Callable[[], datetime] | None = None,
) -> AsyncDemoClientTokenIssuer:
    return AsyncDemoClientTokenIssuer(
        access_token=access_token,
        expires_in=expires_in,
        now=now,
    )
