"""Water and weight logs over a mock transport: wire shape, user binding, retries and errors."""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast, get_args

import anyio
import httpx
import pytest
from installed_consumer import FIXTURES

from januaryai import (
    AsyncHttpClientTokenIssuer,
    AsyncJanuary,
    BadRequestError,
    ClientScope,
    CreateClientTokenInput,
    HttpClientTokenIssuer,
    January,
    JanuaryConnectionError,
    JanuaryError,
    JanuaryValidationError,
    RateLimitError,
    ResponseMetadata,
    models,
)
from januaryai._runtime import Contract
from januaryai.validation import validate_create_input

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
def test_water_logs_accept_cups(mode: str) -> None:
    captured, handler = recorder(201, BY_ID["createWaterLog"]["response"]["body"])
    exercise(mode, handler, "water_logs.create", kwargs={"amount": {"value": 0.125, "unit": "cup"}})
    assert b'"amount":{"value":0.125,"unit":"cup"}' in captured[0].read().replace(b" ", b"")
    captured, handler = recorder(200, BY_ID["listWaterLogs"]["response"]["body"])
    exercise(
        mode,
        handler,
        "water_logs.list",
        kwargs={
            "start_date": "2026-09-01",
            "end_date": "2026-09-10",
            "timezone": "UTC",
            "unit": "cup",
        },
    )
    assert captured[0].url.params["unit"] == "cup"


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
        {"amount": {"value": 250, "unit": "ml"}}
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
        ("water_logs.create", {"amount": {"value": 250, "unit": "ml"}}),
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
    "operation,kwargs",
    [
        ("water_logs.create", {"amount": {"value": 250, "unit": "ml"}}),
        ("weight_logs.create", {"weight": {"value": 70, "unit": "kg"}}),
    ],
)
def test_rate_limited_creates_retry_within_the_budget_and_record_one_log(
    mode: str, operation: str, kwargs: dict[str, Any]
) -> None:
    """A 429 rate_limited reply is a definitive rejection: nothing was recorded."""
    fixture = BY_ID["createWaterLog" if operation.startswith("water") else "createWeightLog"]
    recorded: list[bytes] = []
    attempts: list[int] = []

    def limited_twice(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts))
        if len(attempts) <= 2:
            return httpx.Response(429, json={"code": "rate_limited"}, headers={"retry-after": "0"})
        recorded.append(request.read())
        return httpx.Response(201, json=fixture["response"]["body"])

    result = exercise(mode, limited_twice, operation, kwargs=kwargs, max_retries=2)
    assert not isinstance(result, JanuaryError)
    assert len(attempts) == 3 and len(recorded) == 1

    for max_retries, retry_after, expected in ((2, "0", 3), (0, "0", 1), (2, "61", 1)):
        attempts.clear()

        def always_limited(
            request: httpx.Request, retry_after: str = retry_after
        ) -> httpx.Response:
            attempts.append(len(attempts))
            return httpx.Response(
                429, json={"code": "rate_limited"}, headers={"retry-after": retry_after}
            )

        result = exercise(mode, always_limited, operation, kwargs=kwargs, max_retries=max_retries)
        assert isinstance(result, RateLimitError) and len(attempts) == expected


@pytest.mark.parametrize(
    "unit,accepted,refused",
    [
        ("fl_oz", (1, 8, 811.5), (0.5, 0.999, 811.51, 1000)),
        ("ml", (30, 250, 24000), (1, 29.9, 24000.01)),
        ("cup", (0.125, 1, 101.4), (0.124, 101.41, 811.5)),
    ],
)
def test_water_amount_must_be_within_its_units_range(
    unit: str, accepted: tuple[float, ...], refused: tuple[float, ...]
) -> None:
    captured, handler = recorder(201, BY_ID["createWaterLog"]["response"]["body"])
    for value in accepted:
        before = len(captured)
        result = exercise(
            "sync", handler, "water_logs.create", kwargs={"amount": {"value": value, "unit": unit}}
        )
        assert not isinstance(result, JanuaryError) and len(captured) == before + 1, value
    for value in refused:
        result = exercise(
            "sync", handler, "water_logs.create", kwargs={"amount": {"value": value, "unit": unit}}
        )
        assert isinstance(result, JanuaryValidationError), value
    assert len(captured) == len(accepted)


def test_water_amount_range_error_names_the_unit_range() -> None:
    _captured, handler = recorder(201, BY_ID["createWaterLog"]["response"]["body"])
    result = exercise(
        "sync", handler, "water_logs.create", kwargs={"amount": {"value": 0.5, "unit": "fl_oz"}}
    )
    assert isinstance(result, JanuaryValidationError)
    assert "amount.value must be from 1 through 811.5 fl_oz" in str(result)


WEIGHT_RANGE_CASES = [
    # Weight is shared: a glucose profile takes 2 to 1500 lb or 1 to 700 kg, and a
    # weight-log request narrows that to 10 to 1000 lb or 4.5 to 453.6 kg.
    ("weight_logs.create", "lb", (10, 150, 1000), (2, 9.99, 1000.1, 1500)),
    ("weight_logs.create", "kg", (4.5, 70, 453.6), (1, 4.49, 453.7, 700)),
    ("glucose.predict", "lb", (2, 9.99, 1000.1, 1500), (1, 1.99, 1500.1)),
    ("glucose.predict", "kg", (1, 4.49, 453.7, 700), (0.99, 700.1, 1000)),
]


def weight_kwargs(operation: str, value: float, unit: str) -> dict[str, Any]:
    weight = {"value": value, "unit": unit}
    if operation == "weight_logs.create":
        return {"weight": weight}
    return {
        "user_profile": {
            "age": 30,
            "sex": "male",
            "height": {"value": 175, "unit": "cm"},
            "weight": weight,
        },
        "timezone": "UTC",
        "foods": [{"food_id": "84222716", "serving_id": "67943292", "quantity": 1}],
        "start_time": "2026-09-10T12:00:00Z",
    }


@pytest.mark.parametrize("operation,unit,accepted,refused", WEIGHT_RANGE_CASES)
def test_a_weight_must_be_within_the_range_of_its_unit_and_endpoint(
    operation: str, unit: str, accepted: tuple[float, ...], refused: tuple[float, ...]
) -> None:
    fixture = BY_ID["createWeightLog" if operation.startswith("weight") else "predictGlucose"]
    status = 201 if operation.startswith("weight") else 200
    captured, handler = recorder(status, fixture["response"]["body"])
    user = "water-user" if operation.startswith("weight") else None
    for value in accepted:
        before = len(captured)
        result = exercise(
            "sync", handler, operation, kwargs=weight_kwargs(operation, value, unit), user=user
        )
        assert not isinstance(result, JanuaryError) and len(captured) == before + 1, value
    for value in refused:
        result = exercise(
            "sync", handler, operation, kwargs=weight_kwargs(operation, value, unit), user=user
        )
        assert isinstance(result, JanuaryValidationError), value
    assert len(captured) == len(accepted)


def test_weight_range_errors_name_the_endpoint_range() -> None:
    _captured, handler = recorder(201, BY_ID["createWeightLog"]["response"]["body"])
    result = exercise(
        "sync", handler, "weight_logs.create", kwargs=weight_kwargs("weight_logs.create", 700, "kg")
    )
    assert "weight.value must be from 4.5 through 453.6 kg" in str(result)
    _captured, handler = recorder(200, BY_ID["predictGlucose"]["response"]["body"])
    result = exercise(
        "sync",
        handler,
        "glucose.predict",
        kwargs=weight_kwargs("glucose.predict", 1000, "kg"),
        user=None,
    )
    assert "weight.value must be from 1 through 700 kg" in str(result)


@pytest.mark.parametrize(
    "unit,accepted,refused",
    [
        ("in", (20, 65, 108), (19.9, 108.1, 200, 275)),
        ("cm", (50, 175, 275), (20, 49.9, 275.1)),
    ],
)
def test_a_glucose_profile_height_must_be_within_its_units_range(
    unit: str, accepted: tuple[float, ...], refused: tuple[float, ...]
) -> None:
    captured, handler = recorder(200, BY_ID["predictGlucose"]["response"]["body"])

    def predict(value: float) -> Any:
        kwargs = weight_kwargs("glucose.predict", 70, "kg")
        kwargs["user_profile"]["height"] = {"value": value, "unit": unit}
        return exercise("sync", handler, "glucose.predict", kwargs=kwargs, user=None)

    for value in accepted:
        before = len(captured)
        assert not isinstance(predict(value), JanuaryError) and len(captured) == before + 1
    for value in refused:
        result = predict(value)
        assert isinstance(result, JanuaryValidationError), value
    assert len(captured) == len(accepted)
    if unit == "in":
        assert "height.value must be from 20 through 108 in" in str(predict(200))


def test_a_food_quantity_must_be_greater_than_zero() -> None:
    captured, handler = recorder(201, BY_ID["createFoodLog"]["response"]["body"])
    food = {"food_id": "84222716", "serving_id": "67943292"}
    for quantity in (0, -1):
        result = exercise(
            "sync", handler, "food_logs.create", kwargs={"foods": [{**food, "quantity": quantity}]}
        )
        assert isinstance(result, JanuaryValidationError), quantity
    assert captured == []
    result = exercise(
        "sync", handler, "food_logs.create", kwargs={"foods": [{**food, "quantity": 0.001}]}
    )
    assert not isinstance(result, JanuaryError) and len(captured) == 1


@pytest.mark.parametrize(
    "schema,value,valid",
    [
        ({"type": "number", "minimum": 0, "exclusiveMinimum": True}, 0, False),
        ({"type": "number", "minimum": 0, "exclusiveMinimum": True}, 0.001, True),
        ({"type": "number", "minimum": 0, "exclusiveMinimum": False}, 0, True),
        ({"type": "number", "maximum": 5, "exclusiveMaximum": True}, 5, False),
        ({"type": "number", "maximum": 5, "exclusiveMaximum": True}, 4.99, True),
        ({"type": "integer", "minimum": 1, "exclusiveMinimum": True}, 1, False),
    ],
)
def test_contract_encode_honours_exclusive_bounds(
    schema: dict[str, Any], value: float, valid: bool
) -> None:
    contract = Contract()
    if valid:
        assert contract.encode(value, schema, "value") == value
    else:
        with pytest.raises(JanuaryValidationError, match="outside the allowed range"):
            contract.encode(value, schema, "value")


@pytest.mark.parametrize("day", ["2026-02-31", "2026-02-29", "2026-04-31", "2026-13-01"])
def test_impossible_calendar_dates_are_rejected(day: str) -> None:
    captured, handler = recorder(200, BY_ID["listWaterLogs"]["response"]["body"])
    kwargs = {"start_date": day, "end_date": day, "timezone": "UTC", "unit": "ml"}
    result = exercise("sync", handler, "water_logs.list", kwargs=kwargs)
    assert isinstance(result, JanuaryValidationError) and captured == []
    leap = {**kwargs, "start_date": "2028-02-29", "end_date": "2028-02-29"}
    assert not isinstance(exercise("sync", handler, "water_logs.list", kwargs=leap), JanuaryError)


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


def test_a_stored_scan_without_serving_weight_is_sent_back_for_correction() -> None:
    """An older stored result may lack weight_grams; the correction input takes it as-is.

    Live responses are decoded against ServingSummary, where the API lists weight_grams
    as required (null when unknown). A result kept from earlier goes back through
    CorrectionAnalysis, where it is optional.
    """
    stored = json.loads(json.dumps(BY_ID["scanFoodPhoto"]["response"]["body"]))
    for detection in stored["detections"]:
        del detection["food"]["serving"]["weight_grams"]
    correction_fixture = BY_ID["correctPhotoScan"]
    captured, handler = recorder(200, correction_fixture["response"]["body"])
    result = exercise(
        "sync",
        handler,
        "food_analysis.correct",
        kwargs={"analysis": stored, "instruction": "change oatmeal to steel-cut oats"},
    )
    assert isinstance(result, models.FoodScan)
    sent = json.loads(captured[0].read())["analysis"]
    assert all("weight_grams" not in d["food"]["serving"] for d in sent["detections"])


LOG_SCOPES: list[ClientScope] = [
    "water_logs:read",
    "water_logs:write",
    "weight_logs:read",
    "weight_logs:write",
]


def test_client_scope_lists_every_contract_scope() -> None:
    contract = Contract().data["schemas"]["CreateClientTokenBody"]["properties"]["scopes"]
    assert list(get_args(ClientScope)) == contract["items"]["enum"]
    assert len(get_args(ClientScope)) == contract["maxItems"]


def test_client_tokens_facade_and_issuers_accept_the_log_scopes() -> None:
    captured, handler = recorder(201, BY_ID["createClientToken"]["response"]["body"])
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as transport,
        January(api_key=KEY, http_client=transport, max_retries=0) as client,
    ):
        client.client_tokens.create(end_user_id="alice", scopes=LOG_SCOPES)
        issuer = HttpClientTokenIssuer(secret_key=KEY)
        issuer._client.close()
        issuer._client = client
        issuer.create(CreateClientTokenInput("alice", LOG_SCOPES))
        every_scope = list(get_args(ClientScope))
        client.client_tokens.create(end_user_id="alice", scopes=every_scope)
    assert [json.loads(request.read())["scopes"] for request in captured] == [
        LOG_SCOPES,
        LOG_SCOPES,
        every_scope,
    ]

    async def run() -> None:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport,
            AsyncJanuary(api_key=KEY, http_client=transport, max_retries=0) as client,
        ):
            await client.client_tokens.create(end_user_id="alice", scopes=LOG_SCOPES)
            issuer = AsyncHttpClientTokenIssuer(secret_key=KEY)
            await issuer.close()
            issuer._client = client
            await issuer.create(CreateClientTokenInput("alice", LOG_SCOPES))

    anyio.run(run)
    assert [json.loads(request.read())["scopes"] for request in captured[3:]] == [
        LOG_SCOPES,
        LOG_SCOPES,
    ]
    for scopes in ([], ["sleep_logs:read"], [*get_args(ClientScope), "foods:read"]):
        with pytest.raises(JanuaryValidationError):
            validate_create_input(CreateClientTokenInput("alice", cast(Any, scopes)))


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


def test_conflict_is_a_known_code_that_is_never_retried() -> None:
    from januaryai._backoff import KNOWN_CODES, should_retry_response

    assert "conflict" in KNOWN_CODES
    for status in (409, 429, 500, 503):
        assert not should_retry_response(status, "conflict")


@pytest.mark.parametrize(
    "food_id,serving_id",
    [
        ("0123", "67943292"),
        ("84222716", "012"),
        ("12345678901", "67943292"),
        ("", "1"),
        ("1", "1a"),
    ],
)
def test_food_and_serving_ids_are_one_to_ten_digits_without_a_leading_zero(
    food_id: str, serving_id: str
) -> None:
    captured, handler = recorder(201, BY_ID["createFoodLog"]["response"]["body"])
    foods = [{"food_id": food_id, "serving_id": serving_id, "quantity": 1}]
    result = exercise("sync", handler, "food_logs.create", kwargs={"foods": foods})
    assert isinstance(result, JanuaryValidationError) and captured == []
    valid = [{"food_id": "1234567890", "serving_id": "1", "quantity": 1}]
    assert not isinstance(
        exercise("sync", handler, "food_logs.create", kwargs={"foods": valid}), JanuaryError
    )
