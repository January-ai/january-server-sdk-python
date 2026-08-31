"""Reference SDK regressions, adapted to the contract-generated public API.

All credentials and HTTP responses are synthetic. No dependency on the older
checkout is needed to run these tests in CI or a released source archive.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from types import TracebackType
from typing import Any, get_type_hints

import anyio
import httpx
import pytest
from installed_consumer import FIXTURES

from januaryai import (
    AsyncJanuary,
    AuthenticationError,
    BadRequestError,
    CreditLimitExceededError,
    InternalServerError,
    January,
    JanuaryAPIError,
    JanuaryError,
    JanuaryResponseError,
    JanuaryValidationError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
)

KEY = "sk-audit-synthetic-0011223344556677"
MODES = ("sync", "asyncio", "trio")
BY_ID = {f["operationId"]: f for f in FIXTURES["operations"]}


def exercise(
    mode: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    operation: str = "credits",
    kwargs: dict[str, Any] | None = None,
    max_retries: int = 0,
    timeout: float = 120,
) -> tuple[Any, list[float]]:
    waits: list[float] = []

    def target(client: January | AsyncJanuary) -> Any:
        result: Any = client
        for name in operation.split("."):
            result = getattr(result, name)
        return result

    if mode == "sync":
        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as transport,
            January(
                api_key=KEY, http_client=transport, max_retries=max_retries, timeout=timeout
            ) as client,
        ):
            client._transport._sleep = waits.append
            try:
                return target(client)(**(kwargs or {})), waits
            except JanuaryError as error:
                return error, waits

    async def run() -> Any:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport,
            AsyncJanuary(
                api_key=KEY, http_client=transport, max_retries=max_retries, timeout=timeout
            ) as client,
        ):

            async def sleep(delay: float) -> None:
                waits.append(delay)

            client._transport._sleep = sleep
            try:
                return await target(client)(**(kwargs or {}))
            except JanuaryError as error:
                return error

    return anyio.run(run, backend=mode), waits


def leaking_frames(traceback: TracebackType | None) -> list[str]:
    found = []
    while traceback is not None:
        frame = traceback.tb_frame
        if "/januaryai/" in frame.f_code.co_filename.replace("\\", "/"):
            for name, value in frame.f_locals.items():
                if KEY in repr(value):
                    found.append(f"{frame.f_code.co_name}.{name}")
        traceback = traceback.tb_next
    return found


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("failure", ("status", "timeout", "connect", "echo"))
def test_credentials_never_reach_sdk_traceback_locals(mode: str, failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if failure == "connect":
            raise httpx.ConnectError("synthetic connection failure", request=request)
        return httpx.Response(
            429, json={"code": "rate_limited", "message": KEY if failure == "echo" else "slow down"}
        )

    error, _ = exercise(mode, handler)
    assert isinstance(error, JanuaryError)
    assert not leaking_frames(error.__traceback__)
    assert KEY not in str(error) and KEY not in repr(error)


@pytest.mark.parametrize("mode", MODES)
def test_closed_clients_stay_inside_error_hierarchy(mode: str) -> None:
    if mode == "sync":
        client = January(api_key=KEY)
        client.close()
        with pytest.raises(JanuaryError, match="closed"):
            client.credits()
        with pytest.raises(JanuaryError, match="closed"):
            client.for_user("owner").foods.search(query="banana")
    else:

        async def run() -> None:
            client = AsyncJanuary(api_key=KEY)
            await client.aclose()
            with pytest.raises(JanuaryError, match="closed"):
                await client.credits()
            with pytest.raises(JanuaryError, match="closed"):
                await client.for_user("owner").foods.search(query="banana")

        anyio.run(run, backend=mode)


@pytest.mark.parametrize("mode", MODES)
def test_wrapper_close_preserves_caller_owned_transport(mode: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=BY_ID["credits"]["response"]["body"])

    if mode == "sync":
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            client = January(api_key=KEY, http_client=transport)
            client.close()
            client.credits()
            transport.close()
            with pytest.raises(JanuaryError, match="closed"):
                client.credits()
    else:

        async def run() -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
                client = AsyncJanuary(api_key=KEY, http_client=transport)
                await client.aclose()
                await client.credits()
                await transport.aclose()
                with pytest.raises(JanuaryError, match="closed"):
                    await client.credits()

        anyio.run(run, backend=mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "status", (400, 401, 403, 404, 409, 413, 422, 429, 500, 501, 502, 503, 504)
)
@pytest.mark.parametrize(
    "code",
    (
        None,
        "invalid_request",
        "unauthorized",
        "forbidden",
        "not_found",
        "payload_too_large",
        "rate_limited",
        "credit_limit_exceeded",
        "internal_error",
        "upstream_error",
        "service_unavailable",
        "upstream_timeout",
        "not_implemented",
        "future_error",
    ),
)
def test_error_classification_matches_reference_precedence(
    mode: str, status: int, code: str | None
) -> None:
    expected = {
        400: BadRequestError,
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        413: PayloadTooLargeError,
        429: RateLimitError,
    }.get(status, InternalServerError if status >= 500 else JanuaryAPIError)
    expected = {
        "rate_limited": RateLimitError,
        "credit_limit_exceeded": CreditLimitExceededError,
    }.get(code or "", expected)
    error, waits = exercise(
        mode,
        lambda request: httpx.Response(status, json={"code": code, "message": "synthetic failure"}),
    )
    assert type(error) is expected
    assert error.code == code and not waits


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("first", ("server", "connect"))
def test_only_server_directed_waits_count_toward_retry_after_budget(mode: str, first: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            if first == "connect":
                raise httpx.ConnectError("synthetic failure", request=request)
            return httpx.Response(500, json={"code": "internal_error"})
        if calls == 2:
            return httpx.Response(429, json={"code": "rate_limited"}, headers={"retry-after": "60"})
        return httpx.Response(200, json=BY_ID["credits"]["response"]["body"])

    result, waits = exercise(mode, handler, max_retries=2)
    assert not isinstance(result, JanuaryError)
    assert calls == 3 and 0.375 <= waits[0] <= 0.5 and waits[1] == 60


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "delay,timeout,expected_waits,note",
    [(61, 120, [], "per-wait"), (31, 120, [31], "total limit"), (2, 1, [], "timeout")],
)
def test_retry_refusal_explains_its_limit(
    mode: str, delay: int, timeout: float, expected_waits: list[float], note: str
) -> None:
    error, waits = exercise(
        mode,
        lambda request: httpx.Response(
            429, json={"code": "rate_limited"}, headers={"retry-after": str(delay)}
        ),
        max_retries=3,
        timeout=timeout,
    )
    assert isinstance(error, RateLimitError)
    assert waits == expected_waits
    assert note in " ".join(error.__notes__)
    assert error.retry_after == delay


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "body,expected",
    [
        (b"<html>gateway unavailable</html>", "<html>gateway unavailable</html>"),
        (b"", "HTTP 502"),
        (b'["gateway unavailable"]', '["gateway unavailable"]'),
        (b'{"message":"  ","code":" ","docs_url":" "}', "HTTP 502"),
    ],
)
def test_nonstandard_error_bodies_have_useful_bounded_diagnostics(
    mode: str, body: bytes, expected: str
) -> None:
    error, _ = exercise(mode, lambda request: httpx.Response(502, content=body))
    assert isinstance(error, InternalServerError)
    assert error.message == expected
    assert error.code is None and error.docs_url is None
    assert error.body is None if not body else error.body is not None


@pytest.mark.parametrize("mode", MODES)
def test_error_diagnostics_redact_credentials_and_bound_messages(mode: str) -> None:
    error, _ = exercise(
        mode,
        lambda request: httpx.Response(
            400, json={"message": KEY + " private-food " + "x" * 1000, "code": "invalid_request"}
        ),
        operation="foods.search",
        kwargs={"query": "private-food"},
    )
    assert isinstance(error, BadRequestError)
    assert "truncated" in error.message and len(error.message) < 260
    assert isinstance(error.body, dict) and len(error.body["message"]) > 1000
    assert KEY not in repr(error.body) and "private-food" not in repr(error.body)
    assert "x" * 100 not in str(error)


@pytest.mark.parametrize("mode", MODES)
def test_invalid_success_is_not_an_api_status_error(mode: str) -> None:
    error, waits = exercise(mode, lambda request: httpx.Response(200, json={}), max_retries=2)
    assert isinstance(error, JanuaryResponseError) and isinstance(error, JanuaryError)
    assert not isinstance(error, JanuaryAPIError)
    assert error.status_code == 200 and error.cause is not None and not waits


@pytest.mark.parametrize("mode", MODES)
def test_redirects_raise_january_error_without_following_or_exposing_location(mode: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": f"https://example.invalid/{KEY}"})

    error, waits = exercise(mode, handler, max_retries=2)
    assert type(error) is JanuaryError
    assert "redirect" in str(error) and KEY not in str(error)
    assert calls == 1 and not waits


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "value",
    (
        date(2026, 8, 31),
        datetime(2026, 8, 31, 1),
        datetime(2026, 8, 31, 1, tzinfo=timezone(timedelta(hours=3))),
    ),
)
def test_date_ranges_take_the_local_calendar_day(mode: str, value: date) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "2026-08-31"
        assert request.url.params["end"] == "2026-08-31"
        return httpx.Response(200, json=BY_ID["listFoodLogs"]["response"]["body"])

    result, _ = exercise(
        mode,
        handler,
        operation="food_logs.list",
        kwargs={"end_user_id": "owner", "start": value, "end": value},
    )
    assert not isinstance(result, JanuaryError)


@pytest.mark.parametrize("mode", MODES)
def test_native_update_timestamp_is_serialized_and_naive_values_stay_invalid(mode: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert b"2026-08-30T22:00:00Z" in request.content
        return httpx.Response(200, json=BY_ID["updateFoodLog"]["response"]["body"])

    kwargs = {
        "end_user_id": "owner",
        "log_id": "audit-log",
        "timestamp_utc": datetime(2026, 8, 31, 1, tzinfo=timezone(timedelta(hours=3))),
    }
    result, _ = exercise(mode, handler, operation="food_logs.update", kwargs=kwargs)
    assert not isinstance(result, JanuaryError)
    kwargs["timestamp_utc"] = datetime(2026, 8, 31, 1)
    result, _ = exercise(mode, handler, operation="food_logs.update", kwargs=kwargs)
    assert isinstance(result, JanuaryValidationError) and calls == 1


def test_editor_signatures_accept_native_timestamps() -> None:
    from januaryai._generated import AsyncFoodLogs, SyncFoodLogs

    for resource in (SyncFoodLogs, AsyncFoodLogs):
        assert datetime in get_type_hints(resource.update)["timestamp_utc"].__args__
        assert datetime in get_type_hints(resource.list)["start"].__args__
