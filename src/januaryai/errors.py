class JanuaryError(Exception):
    """Base error for the January Server SDK."""


class JanuaryConfigurationError(JanuaryError):
    """Raised when the SDK is not safely configured."""


class JanuaryValidationError(JanuaryError):
    """Raised before an invalid token request reaches its issuer."""


class JanuaryAPIError(JanuaryError):
    """Inspectable API failure. String/repr intentionally exclude response data."""

    def __init__(self, message: str, *, status_code: int, code: str | None = None,
                 docs_url: str | None = None, request_id: str | None = None,
                 response: object = None, cause: BaseException | None = None) -> None:
        super().__init__(f"January API request failed (HTTP {status_code})")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.docs_url = docs_url
        self.request_id = request_id
        self.response = response
        self.cause = cause


class JanuaryConnectionError(JanuaryError):
    def __init__(self, cause: BaseException | None = None) -> None:
        super().__init__("January connection failed")
        self.cause = cause


class JanuaryTimeoutError(JanuaryConnectionError):
    def __init__(self, cause: BaseException | None = None) -> None:
        super().__init__(cause)
        self.args: tuple[object, ...] = ("January request timed out",)


class JanuaryCancelledError(JanuaryError):
    """A synchronous call was cancelled. Async calls use asyncio cancellation."""


class JanuaryResponseError(JanuaryAPIError):
    """A success response did not match the contract's shape."""
