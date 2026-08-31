import asyncio
from datetime import UTC, datetime

import pytest

from januaryai import (
    AsyncJanuary,
    January,
    JanuaryConfigurationError,
    JanuaryValidationError,
    create_async_demo_token_issuer,
    create_demo_token_issuer,
)


def test_demo_issuer_returns_stable_token_shape() -> None:
    january = January(
        client_token_issuer=create_demo_token_issuer(
            access_token="demo-token",
            expires_in=300,
            now=lambda: datetime(2026, 8, 22, 18, 0, tzinfo=UTC),
        )
    )

    token = january.client_tokens.create(end_user_id="user-123")

    assert token.to_dict() == {
        "token": "demo-token",
        "expiresIn": 300,
    }


def test_async_client_supports_fastapi_style_routes() -> None:
    async def run() -> None:
        january = AsyncJanuary(
            client_token_issuer=create_async_demo_token_issuer(access_token="demo-token")
        )
        token = await january.client_tokens.create(end_user_id="user-123")
        assert token.access_token == "demo-token"

    asyncio.run(run())


def test_rejects_missing_authenticated_user() -> None:
    january = January(client_token_issuer=create_demo_token_issuer(access_token="demo-token"))
    with pytest.raises(JanuaryValidationError):
        january.client_tokens.create(end_user_id=" ")


def test_rejects_sk_secret_in_demo_mode() -> None:
    with pytest.raises(JanuaryConfigurationError):
        create_demo_token_issuer(access_token="sk-do-not-expose")


def test_missing_issuer_fails_clearly() -> None:
    with pytest.raises(JanuaryConfigurationError):
        January()
