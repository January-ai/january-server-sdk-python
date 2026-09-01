from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ClientScope = Literal[
    "foods:read",
    "food_analysis:write",
    "food_logs:read",
    "food_logs:write",
    "glucose:read",
    "restaurants:read",
]


@dataclass(frozen=True)
class CreateClientTokenInput:
    end_user_id: str
    scopes: Sequence[ClientScope]
    ttl_seconds: int | None = None


class ClientToken(BaseModel):
    """Validated token result. Unknown response fields are ignored, not trusted."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, frozen=True, extra="ignore")
    token: Annotated[str, Field(min_length=1, repr=False)]
    expires_in: Annotated[int, Field(gt=0)]
    expires_at: str | None = None

    @property
    def access_token(self) -> str:
        """Alias for token."""
        return self.token

    @property
    def token_type(self) -> Literal["Bearer"]:
        return "Bearer"

    def to_dict(self) -> dict[str, object]:
        """Return the language-neutral JSON shape expected by client providers."""
        return {
            "token": self.token,
            "expiresIn": self.expires_in,
        }


class ClientTokenIssuer(Protocol):
    def create(self, request: CreateClientTokenInput) -> ClientToken: ...


class AsyncClientTokenIssuer(Protocol):
    def create(self, request: CreateClientTokenInput) -> Awaitable[ClientToken]: ...
