from __future__ import annotations

from typing import TYPE_CHECKING

from ._backoff import parse_retry_after

if TYPE_CHECKING:
    from ._runtime import ResponseMetadata


class JanuaryError(Exception):
    """Base error for the January Server SDK."""


class JanuaryConfigurationError(JanuaryError):
    """Raised when the SDK is not safely configured."""


class JanuaryValidationError(JanuaryError):
    """Raised before an invalid request reaches the API."""


class _ResponseError(JanuaryError):
    """Shared, redacted diagnostics; not an API-status classification."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        docs_url: str | None = None,
        request_id: str | None = None,
        response: ResponseMetadata | None = None,
        cause: BaseException | None = None,
        body: object = None,
    ) -> None:
        super().__init__(f"January API request failed (HTTP {status_code})")
        self.status_code = status_code
        self.code = code
        self.message = (
            message
            if len(message) <= 200
            else message[:200] + "... (truncated; full redacted text is in .body)"
        )
        self.docs_url = docs_url
        self.request_id = request_id
        self.response = response
        self.cause = cause
        self.body = body
        self.retry_after = parse_retry_after(getattr(response, "retry_after", None))


class JanuaryAPIError(_ResponseError):
    """API status failure. String/repr intentionally exclude response data.

    message is bounded; body contains redacted diagnostic data, never the raw
    response. response contains safe HTTP metadata. Retry refusal explanations
    are available through the standard exception __notes__ attribute.
    """


class BadRequestError(JanuaryAPIError):
    """The request is invalid (HTTP 400)."""


class AuthenticationError(JanuaryAPIError):
    """The server API key is missing, invalid, or expired (HTTP 401)."""


class PermissionDeniedError(JanuaryAPIError):
    """The credential cannot access this operation (HTTP 403)."""


class NotFoundError(JanuaryAPIError):
    """The requested resource was not found (HTTP 404)."""


class PayloadTooLargeError(JanuaryAPIError):
    """The request is too large (HTTP 413); prepare or reduce the photo."""


class RateLimitError(JanuaryAPIError):
    """A temporary rate limit. retry_after is seconds to wait, when supplied."""


class CreditLimitExceededError(JanuaryAPIError):
    """Credits are exhausted. Never treated as a retryable rate limit."""


class InternalServerError(JanuaryAPIError):
    """A server or upstream failure (HTTP 5xx)."""


def api_error_type(status_code: int, code: str | None) -> type[JanuaryAPIError]:
    """Match the reference: only rate/credit codes override the HTTP status."""
    by_code: dict[str, type[JanuaryAPIError]] = {
        "rate_limited": RateLimitError,
        "credit_limit_exceeded": CreditLimitExceededError,
    }
    by_status: dict[int, type[JanuaryAPIError]] = {
        400: BadRequestError,
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        413: PayloadTooLargeError,
        429: RateLimitError,
    }
    return by_code.get(
        code or "",
        by_status.get(status_code, InternalServerError if status_code >= 500 else JanuaryAPIError),
    )


class JanuaryConnectionError(JanuaryError):
    def __init__(self, cause: BaseException | None = None) -> None:
        super().__init__("January connection failed")
        self.cause = cause
        self.message = str(self)


class JanuaryTimeoutError(JanuaryConnectionError):
    def __init__(self, cause: BaseException | None = None) -> None:
        super().__init__(cause)
        self.args: tuple[object, ...] = ("January request timed out",)
        self.message = str(self)


class JanuaryCancelledError(JanuaryError):
    """A synchronous call was cancelled. Async calls use asyncio cancellation."""


class JanuaryResponseError(_ResponseError):
    """An invalid success response, separate from API-status failures.

    Catch JanuaryError for all SDK failures. cause retains validation details;
    it is deliberately not printed in the exception chain, which may contain
    sensitive response fields.
    """
