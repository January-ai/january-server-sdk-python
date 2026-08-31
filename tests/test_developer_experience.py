"""Public SDK regressions: all transports are mocks or loopback, never production."""

import inspect
import io
import json
import runpy
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx
import pytest
from installed_consumer import FIXTURES, arguments, method
from PIL import Image

from januaryai import (
    AsyncJanuary,
    AuthenticationError,
    BadRequestError,
    CreditLimitExceededError,
    InternalServerError,
    January,
    JanuaryAPIError,
    JanuaryConfigurationError,
    JanuaryResponseError,
    JanuaryValidationError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    models,
)
from januaryai._backoff import parse_retry_after, should_retry_response
from januaryai._runtime import Contract

ROOT = Path(__file__).resolve().parents[1]
BY_ID = {f["operationId"]: f for f in FIXTURES["operations"]}


def exercise(mode, responses, *, operation="credits", options=None, transport_error=None):
    calls, sleeps = [], []

    def handle(request):
        calls.append(request)
        if transport_error and len(calls) == 1:
            raise transport_error("synthetic transport failure", request=request)
        response = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(
            response["status"], json=response.get("body"), headers=response.get("headers", {})
        )

    kwargs = {"api_key": "sk-offline-test", **(options or {})}
    if mode == "sync":
        with (
            httpx.Client(transport=httpx.MockTransport(handle)) as transport,
            January(http_client=transport, **kwargs) as client,
        ):
            client._transport._sleep = sleeps.append
            try:
                result = method(client, BY_ID[operation])(**arguments(BY_ID[operation]))
            except JanuaryAPIError as error:
                result = error
            except Exception as error:
                result = error
    else:

        async def run():
            async with (
                httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport,
                AsyncJanuary(http_client=transport, **kwargs) as client,
            ):

                async def sleep(delay):
                    sleeps.append(delay)

                client._transport._sleep = sleep
                try:
                    return await method(client, BY_ID[operation])(**arguments(BY_ID[operation]))
                except Exception as error:
                    return error

        result = anyio.run(run, backend=mode)
    return result, calls, sleeps


@pytest.mark.parametrize("mode", ["sync", "asyncio", "trio"])
@pytest.mark.parametrize(
    "status,code,error_type,retries",
    [
        (400, "invalid_request", BadRequestError, 0),
        (401, "unauthorized", AuthenticationError, 0),
        (403, "forbidden", PermissionDeniedError, 0),
        (404, "not_found", NotFoundError, 0),
        (413, "payload_too_large", PayloadTooLargeError, 0),
        (429, "rate_limited", RateLimitError, 2),
        (429, "credit_limit_exceeded", CreditLimitExceededError, 0),
        (500, "internal_error", InternalServerError, 2),
        (502, "upstream_error", InternalServerError, 2),
        (503, "service_unavailable", InternalServerError, 2),
        (504, "upstream_timeout", InternalServerError, 2),
        (501, "not_implemented", InternalServerError, 0),
        (503, "future_transient_code", InternalServerError, 2),
    ],
)
def test_code_aware_errors_and_default_retry_budget(mode, status, code, error_type, retries):
    error, calls, sleeps = exercise(
        mode,
        [
            {
                "status": status,
                "body": {
                    "code": code,
                    "message": "private sk-offline-test",
                    "docs_url": "https://example.invalid/help",
                },
                "headers": {"x-request-id": "request-test", "retry-after": "0"},
            }
        ],
    )
    assert type(error) is error_type
    assert isinstance(error, JanuaryAPIError)
    assert error.code == code and error.request_id == "request-test"
    assert error.retry_after == 0
    assert "private" not in str(error) and "sk-offline-test" not in error.message
    assert len(calls) == retries + 1 and len(sleeps) == retries
    assert not issubclass(CreditLimitExceededError, RateLimitError)


@pytest.mark.parametrize("mode", ["sync", "asyncio", "trio"])
@pytest.mark.parametrize("operation", ["mintClientToken", "createFoodLog", "revokeClientTokens"])
@pytest.mark.parametrize("failure", ["server", "read", "connect", "rate"])
def test_write_replay_safety(mode, operation, failure):
    response = {"status": 503, "body": {"code": "upstream_error"}, "headers": {"retry-after": "0"}}
    exception = None
    if failure == "rate":
        response = {
            "status": 429,
            "body": {"code": "rate_limited"},
            "headers": {"retry-after": "0"},
        }
    elif failure in {"read", "connect"}:
        exception = httpx.ReadTimeout if failure == "read" else httpx.ConnectError
    _, calls, _ = exercise(mode, [response], operation=operation, transport_error=exception)
    expected = (
        1
        if operation == "revokeClientTokens" or failure in {"server", "read"}
        else 2
        if failure == "connect"
        else 3
    )
    assert len(calls) == expected


@pytest.mark.parametrize("mode", ["sync", "asyncio", "trio"])
def test_explicit_no_retries_and_retry_after_bounds(mode):
    error = {"status": 429, "body": {"code": "rate_limited"}, "headers": {"retry-after": "1"}}
    result, calls, sleeps = exercise(mode, [error], options={"max_retries": 0})
    assert isinstance(result, RateLimitError) and len(calls) == 1 and not sleeps
    error["headers"]["retry-after"] = "600"
    result, calls, sleeps = exercise(mode, [error])
    assert isinstance(result, RateLimitError)
    assert result.retry_after == 600 and len(calls) == 1 and not sleeps
    error["headers"]["retry-after"] = "31"
    _, calls, sleeps = exercise(mode, [error], options={"max_retries": 5, "timeout": 120})
    assert len(calls) == 2 and sleeps == [31]
    error["headers"]["retry-after"] = "2"
    _, calls, sleeps = exercise(mode, [error], options={"timeout": 1})
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize("mode", ["sync", "asyncio", "trio"])
def test_retry_recovery_and_jitter(mode):
    response = {"status": 503, "body": {"code": "service_unavailable"}}
    result, calls, sleeps = exercise(mode, [response, BY_ID["credits"]["response"]])
    assert isinstance(result, models.CreditsResponseDto)
    assert len(calls) == 2 and 0.375 <= sleeps[0] <= 0.5
    invalid = {"status": 200, "body": {}}
    result, calls, _ = exercise(mode, [invalid])
    assert isinstance(result, JanuaryResponseError) and len(calls) == 1


@pytest.mark.parametrize("value", [None, "", "invalid", "NaN", "Infinity"])
def test_bad_retry_after(value):
    assert parse_retry_after(value) is None


def test_http_date_retry_after_and_code_precedence():
    assert parse_retry_after("Wed, 01 Jan 2020 00:00:00 GMT") == 0
    assert not should_retry_response(503, "credit_limit_exceeded")
    assert not should_retry_response(503, "invalid_request")


@pytest.mark.parametrize("client_type", [January, AsyncJanuary])
def test_environment_credentials_and_configuration(client_type, monkeypatch):
    monkeypatch.setenv("JANUARY_API_KEY", "sk-environment-test")
    monkeypatch.setenv("JANUARY_BASE_URL", "https://must-not-be-used.invalid")
    client = client_type()
    assert client._transport._secret_key == "sk-environment-test"
    assert client._transport._base_url == "https://partners.january.ai"
    explicit = client_type(api_key="sk-explicit-test")
    assert explicit._transport._secret_key == "sk-explicit-test"
    assert "sk-" not in repr(client)
    with pytest.raises(JanuaryConfigurationError):
        client_type(api_key="sk-one", secret_key="sk-two")
    for invalid in [True, -1, 1.5]:
        with pytest.raises(JanuaryConfigurationError):
            client_type(max_retries=invalid)
    with pytest.raises(JanuaryConfigurationError):
        client_type(timeout=httpx.Timeout(None))
    with pytest.raises(JanuaryConfigurationError):
        client_type(default_headers={"Authorization": "other"})
    monkeypatch.setenv("JANUARY_API_KEY", "")
    with pytest.raises(JanuaryConfigurationError):
        client_type()
    if client_type is January:
        client.close()
        explicit.close()
    else:
        anyio.run(client.aclose)
        anyio.run(explicit.aclose)


def test_response_correction_roundtrip_keeps_only_returned_model_extensions():
    response = deepcopy(BY_ID["scanFoodPhoto"]["response"]["body"])
    response["detections"][0]["future_detection"] = {"value": 7}
    response["detections"][0]["food"]["future_food"] = "extra"
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as transport,
        January(api_key="sk-offline", http_client=transport) as client,
    ):
        user = client.for_user("owner")
        scan = user.food_analysis.analyze_photo(image="https://example.invalid/photo.jpg")
        user.food_analysis.correct(detections=scan.detections, user_input="smaller portion")
        assert requests[-1]["detections"][0]["future_detection"] == {"value": 7}
        assert requests[-1]["detections"][0]["food"]["future_food"] == "extra"
        with pytest.raises(JanuaryValidationError):
            user.food_analysis.correct(detections=response["detections"], user_input="smaller")
        manufactured = models.FoodDetection.model_validate(response["detections"][0])
        with pytest.raises(JanuaryValidationError):
            user.food_analysis.correct(detections=[manufactured], user_input="smaller")
        bad_food = scan.detections[0].food.model_copy(update={"name": 42})
        bad_detection = scan.detections[0].model_copy(update={"food": bad_food})
        with pytest.raises(JanuaryValidationError):
            user.food_analysis.correct(detections=[bad_detection], user_input="smaller")
    assert len(requests) == 2
    with pytest.raises(JanuaryValidationError):
        Contract().encode({"typo": 1}, {"type": "object", "properties": {}}, "request")


@pytest.mark.parametrize("mode", ["sync", "asyncio", "trio"])
def test_photo_inputs_are_prepared_before_request(mode):
    image = Image.new("RGB", (2048, 1000), "green")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    fixture = deepcopy(BY_ID["scanFoodPhoto"])
    # exercise obtains arguments through the shared fixture; restore immediately.
    old = BY_ID["scanFoodPhoto"]
    fixture["request"]["body"]["image"] = buffer.getvalue()
    BY_ID["scanFoodPhoto"] = fixture
    try:
        result, calls, _ = exercise(mode, [fixture["response"]], operation="scanFoodPhoto")
    finally:
        BY_ID["scanFoodPhoto"] = old
    assert isinstance(result, models.FoodScan)
    assert json.loads(calls[0].content)["image"].startswith("data:image/jpeg;base64,")


def test_native_timestamps_and_help_keep_wire_contract():
    log = models.FoodLog(id="log", foods=[], timestamp_utc="opaque legacy value")
    assert log.timestamp_utc_datetime is None and log.timestamp_utc == "opaque legacy value"
    timestamp = "2026-08-31T12:30:00Z"
    log = models.FoodLog(id="log", foods=[], timestamp_utc=timestamp)
    assert log.timestamp_utc_datetime == datetime(2026, 8, 31, 12, 30, tzinfo=UTC)
    reading = models.CgmReading.model_validate_json(
        '{"timestamp":"2026-08-31T12:30:00Z","value":100}'
    )
    assert isinstance(reading.timestamp, datetime)
    with January(api_key="sk-offline") as client:
        assert "trusted local path" in (client.food_analysis.analyze_photo.__doc__ or "")
        assert "end_user_id:" in (client.foods.search.__doc__ or "")


def test_sync_async_signature_parity():
    with January(api_key="sk-offline") as sync:
        asynchronous = AsyncJanuary(api_key="sk-offline")
        try:
            for fixture in BY_ID.values():
                left, right = (
                    inspect.signature(method(sync, fixture)),
                    inspect.signature(method(asynchronous, fixture)),
                )
                assert {k: v for k, v in left.parameters.items() if k != "cancel_event"} == dict(
                    right.parameters
                )
                assert left.return_annotation == right.return_annotation
        finally:
            anyio.run(asynchronous.aclose)


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
def test_async_cancellation_interrupts_retry_wait(backend):
    async def run():
        sleeping = anyio.Event()
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(429, json={"code": "rate_limited"}, headers={"retry-after": "10"})

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport,
            AsyncJanuary(api_key="sk-offline", http_client=transport) as client,
        ):

            async def sleep(delay):
                sleeping.set()
                await anyio.sleep_forever()

            client._transport._sleep = sleep
            async with anyio.create_task_group() as group:
                group.start_soon(client.credits)
                with anyio.fail_after(2):
                    await sleeping.wait()
                group.cancel_scope.cancel()
        assert len(requests) == 1

    anyio.run(run, backend=backend)


def test_release_version_validation():
    validate = runpy.run_path(str(ROOT / "scripts/check-release.py"))["validate_tag"]
    validate("v1.2.3", "1.2.3")
    with pytest.raises(ValueError):
        validate("v1.2.4", "1.2.3")
