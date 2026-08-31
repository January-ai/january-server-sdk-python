from .errors import JanuaryValidationError
from .types import CreateClientTokenInput
from typing import get_args
from .types import ClientScope


def validate_create_input(request: CreateClientTokenInput) -> None:
    if not isinstance(request.end_user_id, str) or not request.end_user_id.strip():
        raise JanuaryValidationError(
            "end_user_id must be a non-empty string derived from the authenticated user"
        )
    if len(request.end_user_id.strip().encode("utf-16-le", errors="surrogatepass")) // 2 > 64:
        raise JanuaryValidationError("end_user_id must be at most 64 UTF-16 code units")
    if request.scopes is not None and (
        isinstance(request.scopes, (str, bytes))
        or not 1 <= len(request.scopes) <= 6
        or any(scope not in get_args(ClientScope) for scope in request.scopes)
    ):
        raise JanuaryValidationError("scopes must contain 1–6 client-grantable scopes")
    if request.ttl_seconds is not None and (
        not isinstance(request.ttl_seconds, int) or isinstance(request.ttl_seconds, bool)
        or not 300 <= request.ttl_seconds <= 7200
    ):
        raise JanuaryValidationError("ttl_seconds must be an integer from 300 through 7200")
