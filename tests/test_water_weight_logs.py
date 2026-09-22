"""Water and weight logs over a mock transport: wire shape, user binding, retries and errors."""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import anyio
import httpx
import pytest
from installed_consumer import FIXTURES

from januaryai import (
    AsyncJanuary,
    BadRequestError,
    January,
    JanuaryConnectionError,
    JanuaryError,
    JanuaryValidationError,
    ResponseMetadata,
    models,
)

KEY = "sk-water-synthetic-0011223344556677"
MODES = ("sync", "asyncio", "trio")
BY_ID = {f["operationId"]: f for f in FIXTURES["operations"]}


def exercise(
    mode: str,
    handler: Callable[[httpx.Request], httpx.Response],
    operation: str,
    *,
    kwargs: dict[str, Any],
    max_retries: int = 0,
    user: str | None = "water-user",
) -> Any:
    def target(client: January | AsyncJanuary) -> Any:
        result: Any = client.for_user(user) if user else client
        for name in operation.split("."):
            result = getattr(result, name)
        return result

    if mode == "sync":
        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as transport,
            January(api_key=KEY, http_client=transport, max_retries=max_retries) as client,
        ):
            client._transport._sleep = lambda _delay: None
            try:
                return target(client)(**kwargs)
            except JanuaryError as error:
                return error

    async def run() -> Any:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport,
            AsyncJanuary(api_key=KEY, http_client=transport, max_retries=max_retries) as client,
        ):

            async def sleep(_delay: float) -> None:
                return None

            client._transport._sleep = sleep  # pyright: ignore[reportAttributeAccessIssue]
            try:
                return await target(client)(**kwargs)
            except JanuaryError as error:
                return error

    return anyio.run(run, backend=mode)


def recorder(status: int, body: Any) -> tuple[list[httpx.Request], Callable[..., httpx.Response]]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=body, headers={"x-request-id": "req-water"})

    return captured, handler


@pytest.mark.parametrize("mode", MODES)
def test_create_water_log_sends_amount_and_zoned_time(mode: str) -> None:
    captured, handler = recorder(201, BY_ID["createWaterLog"]["response"]["body"])
    result = exercise(
        mode,
        handler,
        "water_logs.create",
        kwargs={
            "amount": {"value": 8, "unit": "fl_oz"},
            "consumed_at": datetime(2026, 9, 10, 14, 30, 15, tzinfo=UTC),
        },
    )
    request = captured[0]
    assert request.method == "POST" and request.url.path == "/v1.2/water-logs"
    assert request.headers["January-End-User-ID"] == "water-user"
    body = request.read()
    assert b'"amount":{"value":8,"unit":"fl_oz"}' in body.replace(b" ", b"")
    assert b"2026-09-10T14:30:15" in body
    assert isinstance(result, models.WaterLog)
    assert result.amount.unit == "fl_oz" and result.amount.value == 8
    assert result.consumed_at == datetime(2026, 9, 10, 14, 30, 15, 123000, tzinfo=UTC)
    assert result.response is not None and result.response.request_id == "req-water"


@pytest.mark.parametrize("mode", MODES)
def test_create_water_log_without_time_omits_consumed_at(mode: str) -> None:
    captured, handler = recorder(201, BY_ID["createWaterLog"]["response"]["body"])
    exercise(mode, handler, "water_logs.create", kwargs={"amount": {"value": 300, "unit": "ml"}})
    assert b"consumed_at" not in captured[0].read()


@pytest.mark.parametrize("mode", MODES)
def test_list_water_logs_serializes_range_and_unit(mode: str) -> None:
    captured, handler = recorder(200, BY_ID["listWaterLogs"]["response"]["body"])
    result = exercise(
        mode,
        handler,
        "water_logs.list",
        kwargs={
            "start_date": date(2026, 9, 1),
            "end_date": "2026-09-10",
            "timezone": "America/Los_Angeles",
            "unit": "fl_oz",
        },
    )
    request = captured[0]
    assert request.method == "GET" and request.url.path == "/v1.2/water-logs"
    assert dict(request.url.params) == {
        "start_date": "2026-09-01",
        "end_date": "2026-09-10",
        "timezone": "America/Los_Angeles",
        "unit": "fl_oz",
    }
    assert isinstance(result, models.ListWaterLogsResponse)
    assert [item.date for item in result.items] == [date(2026, 9, 9), date(2026, 9, 10)]
    assert result.items[1].total.value == 64 and result.items[1].total.unit == "fl_oz"


@pytest.mark.parametrize("mode", MODES)
def test_delete_water_log_returns_metadata_for_no_content(mode: str) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, headers={"x-request-id": "req-delete"})

    result = exercise(
        mode,
        handler,
        "water_logs.delete",
        kwargs={"log_id": "9c1f2a3b-4d5e-4f60-8a71-b2c3d4e5f607"},
    )
    request = captured[0]
    assert request.method == "DELETE"
    assert request.url.path == "/v1.2/water-logs/9c1f2a3b-4d5e-4f60-8a71-b2c3d4e5f607"
    assert isinstance(result, ResponseMetadata)
    assert result.status_code == 204 and result.request_id == "req-delete"


@pytest.mark.parametrize("mode", MODES)
def test_create_and_list_weight_logs(mode: str) -> None:
    captured, handler = recorder(201, BY_ID["createWeightLog"]["response"]["body"])
    created = exercise(
        mode,
        handler,
        "weight_logs.create",
        kwargs={"weight": {"value": 150, "unit": "lb"}, "measured_at": "2026-09-10T07:30:00-07:00"},
    )
    request = captured[0]
    assert request.method == "POST" and request.url.path == "/v1.2/weight-logs"
    assert b'"weight":{"value":150,"unit":"lb"}' in request.read().replace(b" ", b"")
    assert isinstance(created, models.WeightLog)
    assert created.weight.unit == "lb" and created.measured_at.tzinfo is not None

    captured, handler = recorder(200, BY_ID["listWeightLogs"]["response"]["body"])
    listed = exercise(
        mode,
        handler,
        "weight_logs.list",
        kwargs={"start_date": "2026-09-01", "end_date": "2026-09-10", "timezone": "UTC"},
    )
    assert dict(captured[0].url.params) == {
        "start_date": "2026-09-01",
        "end_date": "2026-09-10",
        "timezone": "UTC",
    }
    assert isinstance(listed, models.ListWeightLogsResponse)
    assert [(item.date.isoformat(), item.weight.value) for item in listed.items] == [
        ("2026-09-08", 151.2),
        ("2026-09-10", 150),
    ]


@pytest.mark.parametrize("operation", ["water_logs.create", "weight_logs.create"])
def test_user_views_bind_identity_for_the_new_logs(operation: str) -> None:
    fixture = BY_ID["createWaterLog" if operation.startswith("water") else "createWeightLog"]
    captured, handler = recorder(201, fixture["response"]["body"])
    kwargs = (
        {"amount": {"value": 1, "unit": "ml"}}
        if operation.startswith("water")
        else {"weight": {"value": 70, "unit": "kg"}}
    )
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as transport,
        January(api_key=KEY, http_client=transport, max_retries=0) as client,
    ):
        view = client.for_user("alice")
        resource, method = operation.split(".")
        getattr(getattr(view, resource), method)(**kwargs)
        # The bound identity wins over a per-call value on a user view.
        getattr(getattr(view, resource), method)(end_user_id="mallory", **kwargs)
        getattr(getattr(client, resource), method)(end_user_id="bob", **kwargs)
        # The root client sends no identity header; the API answers end_user_id_required.
        getattr(getattr(client, resource), method)(**kwargs)
    assert [r.headers.get("January-End-User-ID") for r in captured] == [
        "alice",
        "alice",
        "bob",
        None,
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "operation,kwargs",
    [
        ("water_logs.create", {"amount": {"value": 1, "unit": "ml"}}),
        ("weight_logs.create", {"weight": {"value": 70, "unit": "kg"}}),
    ],
)
def test_new_creates_never_replay_ambiguous_failures(
    mode: str, operation: str, kwargs: dict[str, Any]
) -> None:
    attempts: list[str] = []

    def server_failure(request: httpx.Request) -> httpx.Response:
        attempts.append("http")
        return httpx.Response(503, json={"code": "service_unavailable"})

    result = exercise(mode, server_failure, operation, kwargs=kwargs, max_retries=2)
    assert isinstance(result, BadRequestError | JanuaryError) and attempts == ["http"]

    def dropped_connection(request: httpx.Request) -> httpx.Response:
        attempts.append("timeout")
        raise httpx.ReadTimeout("lost after sending", request=request)

    attempts.clear()
    result = exercise(mode, dropped_connection, operation, kwargs=kwargs, max_retries=2)
    assert isinstance(result, JanuaryConnectionError) and attempts == ["timeout"]

    def rate_limited_then_ok(request: httpx.Request) -> httpx.Response:
        attempts.append("429")
        if len(attempts) == 1:
            return httpx.Response(429, json={"code": "rate_limited"}, headers={"retry-after": "0"})
        fixture = BY_ID["createWaterLog" if operation.startswith("water") else "createWeightLog"]
        return httpx.Response(201, json=fixture["response"]["body"])

    attempts.clear()
    result = exercise(mode, rate_limited_then_ok, operation, kwargs=kwargs, max_retries=2)
    assert not isinstance(result, JanuaryError) and attempts == ["429", "429"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "operation,kwargs,status",
    [
        (
            "water_logs.list",
            {"start_date": "2026-09-01", "end_date": "2026-09-02", "timezone": "UTC", "unit": "ml"},
            200,
        ),
        (
            "weight_logs.list",
            {"start_date": "2026-09-01", "end_date": "2026-09-02", "timezone": "UTC"},
            200,
        ),
        ("water_logs.delete", {"log_id": "9c1f2a3b-4d5e-4f60-8a71-b2c3d4e5f607"}, 204),
    ],
)
def test_safe_log_operations_retry_transient_failures(
    mode: str, operation: str, kwargs: dict[str, Any], status: int
) -> None:
    attempts: list[int] = []
    fixture = BY_ID[
        {
            "water_logs.list": "listWaterLogs",
            "weight_logs.list": "listWeightLogs",
            "water_logs.delete": "deleteWaterLog",
        }[operation]
    ]

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts))
        if len(attempts) == 1:
            return httpx.Response(503, json={"code": "service_unavailable"})
        if status == 204:
            return httpx.Response(204)
        return httpx.Response(status, json=fixture["response"]["body"])

    result = exercise(mode, flaky, operation, kwargs=kwargs, max_retries=2)
    assert not isinstance(result, JanuaryError) and len(attempts) == 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "operation,kwargs,code",
    [
        (
            "water_logs.create",
            {"amount": {"value": 811.5, "unit": "fl_oz"}},
            "daily_water_limit_exceeded",
        ),
        (
            "water_logs.list",
            {"start_date": "2019-01-01", "end_date": "2026-09-02", "timezone": "UTC", "unit": "ml"},
            "date_range_too_large",
        ),
        (
            "weight_logs.list",
            {"start_date": "2019-01-01", "end_date": "2026-09-02", "timezone": "UTC"},
            "date_range_too_large",
        ),
    ],
)
def test_endpoint_specific_rejections_are_bad_requests_and_never_retried(
    mode: str, operation: str, kwargs: dict[str, Any], code: str
) -> None:
    captured, handler = recorder(
        400, {"code": code, "message": "rejected", "docs_url": "https://example.invalid/docs"}
    )
    result = exercise(mode, handler, operation, kwargs=kwargs, max_retries=2)
    assert type(result) is BadRequestError
    assert result.code == code and result.status_code == 400
    assert len(captured) == 1


@pytest.mark.parametrize("mode", MODES)
def test_update_food_log_sends_only_set_fields_and_rejects_an_empty_patch(mode: str) -> None:
    captured, handler = recorder(200, BY_ID["updateFoodLog"]["response"]["body"])
    log_id = "78129823-8ba2-4183-b13b-71f0e963c606"
    result = exercise(mode, handler, "food_logs.update", kwargs={"log_id": log_id, "name": "Lunch"})
    assert isinstance(result, models.FoodLog)
    assert captured[0].method == "PATCH" and captured[0].read() == b'{"name":"Lunch"}'

    result = exercise(mode, handler, "food_logs.update", kwargs={"log_id": log_id})
    assert isinstance(result, JanuaryValidationError)
    assert len(captured) == 1


@pytest.mark.parametrize("mode", MODES)
def test_correction_sends_a_returned_scan_back_field_for_field(mode: str) -> None:
    scan_fixture = BY_ID["scanFoodPhoto"]
    correction_fixture = BY_ID["correctPhotoScan"]
    captured, handler = recorder(200, correction_fixture["response"]["body"])
    scan = models.FoodScan.model_validate(scan_fixture["response"]["body"])
    result = exercise(
        mode,
        handler,
        "food_analysis.correct",
        kwargs={"analysis": scan, "instruction": "change oatmeal to steel-cut oats"},
    )
    assert isinstance(result, models.FoodScan)
    assert json.loads(captured[0].read()) == correction_fixture["request"]["body"]
    detection = result.detections[0]
    assert detection.food.serving.weight_grams == 81


def test_client_token_scopes_cover_the_new_logs() -> None:
    captured, handler = recorder(201, BY_ID["createClientToken"]["response"]["body"])
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as transport,
        January(api_key=KEY, http_client=transport, max_retries=0) as client,
    ):
        client.create_client_token(
            end_user_id="alice",
            scopes=["water_logs:read", "water_logs:write", "weight_logs:read", "weight_logs:write"],
        )
    assert b"water_logs:write" in captured[0].read() and b"weight_logs:read" in captured[0].read()
