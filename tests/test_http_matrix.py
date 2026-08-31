"""SDK -> actual TCP HTTP -> fixture responses, never production or ambient .env.

Fault injection is deliberately local: production rate limiting, outages and
credit exhaustion cannot be induced safely or reproducibly by an SDK suite.
"""

import base64
import io
from copy import deepcopy
from pathlib import Path
from typing import Any

import anyio
import pytest
from installed_consumer import FIXTURES, arguments, assert_request, local_service, method
from PIL import Image
from test_live_runner import image_cases

from januaryai import (
    AsyncJanuary,
    AuthenticationError,
    BadRequestError,
    CreditLimitExceededError,
    InternalServerError,
    January,
    JanuaryAPIError,
    JanuaryResponseError,
    JanuaryValidationError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RateLimitError,
    models,
)
from januaryai._runtime import Contract

MODES = ("sync", "asyncio", "trio")
OPERATIONS = FIXTURES["operations"]
PHOTO = next(f for f in OPERATIONS if f["operationId"] == "scanFoodPhoto")
FIXTURE = Path(__file__).resolve().parents[1] / "examples/live/food.png"
ERRORS = (
    (400, "invalid_request", BadRequestError),
    (401, "unauthorized", AuthenticationError),
    (403, "forbidden", PermissionDeniedError),
    (404, "not_found", NotFoundError),
    (413, "payload_too_large", PayloadTooLargeError),
    (429, "rate_limited", RateLimitError),
    (429, "credit_limit_exceeded", CreditLimitExceededError),
    (500, "internal_error", InternalServerError),
    (501, "not_implemented", InternalServerError),
    (502, "upstream_error", InternalServerError),
    (503, "service_unavailable", InternalServerError),
    (504, "upstream_timeout", InternalServerError),
)


def invalid_requests():
    """Derive type/null/enum/boundary checks from the generated contract."""
    contract = Contract()
    cases = []
    for fixture in OPERATIONS:
        operation = contract.data["operations"][fixture["operationId"]]
        for field in operation["parameters"] + operation["fields"]:
            name, schema = field["publicName"], contract.resolve(field["schema"])
            if name == "image":
                continue  # Covered by the photo preparation input matrix.
            values: list[tuple[str, Any]] = (
                [("type", {})] if schema.get("type") not in {None, "object"} else []
            )
            if not schema.get("nullable", False):
                values.append(("null", None))
            if schema.get("enum"):
                values.append(("enum", "not-an-accepted-value"))
            if "minimum" in schema:
                values.append(("minimum", schema["minimum"] - 1))
            if "maximum" in schema:
                values.append(("maximum", schema["maximum"] + 1))
            if schema.get("minLength", 0) > 0:
                values.append(("minLength", "a" * (schema["minLength"] - 1)))
            if "maxLength" in schema and schema["maxLength"] < 100_000:
                values.append(("maxLength", "a" * (schema["maxLength"] + 1)))
            if schema.get("format") in {"date", "date-time"}:
                values.append(("format", "not-a-date"))
            for reason, value in values:
                cases.append(
                    pytest.param(
                        fixture,
                        {**arguments(fixture), name: value},
                        id=f"{fixture['operationId']}-{name}-{reason}",
                    )
                )
    return cases


def call(mode, service, fixture, params=None, *, retries=0):
    options: dict[str, Any] = {
        "api_key": "sk-local-fixture",
        "base_url": service["url"],
        "max_retries": retries,
    }
    params = arguments(fixture) if params is None else params
    if mode == "sync":
        with January(**options) as client:
            return method(client, fixture)(**params)

    async def run():
        async with AsyncJanuary(**options) as client:
            return await method(client, fixture)(**params)

    return anyio.run(run, backend=mode)


@pytest.mark.parametrize("fixture", OPERATIONS, ids=lambda f: f["operationId"])
def test_all_endpoints_trio_http(fixture):
    with local_service() as service:
        service["response"] = fixture["response"]
        result = call("trio", service, fixture)
        assert_request(service["requests"][0], fixture)
        assert len(service["requests"]) == 1
        assert getattr(result, "response", None) is not None or result.status_code == 204


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fixture", OPERATIONS, ids=lambda f: f["operationId"])
@pytest.mark.parametrize("status,code,error_class", ERRORS, ids=[code for _, code, _ in ERRORS])
def test_each_endpoint_maps_error_responses_over_http(mode, fixture, status, code, error_class):
    with local_service() as service:
        service["response"] = {
            "status": status,
            "body": {
                "code": code,
                "message": "private sk-local-fixture",
                "docs_url": "https://example.invalid/docs",
            },
            "headers": {"x-request-id": "request-http-matrix", "retry-after": "0"},
        }
        with pytest.raises(error_class) as caught:
            call(mode, service, fixture)
        assert type(caught.value) is error_class
        assert caught.value.code == code and caught.value.status_code == status
        assert caught.value.request_id == "request-http-matrix"
        assert caught.value.retry_after == 0
        assert "sk-local-fixture" not in str(caught.value)
        assert len(service["requests"]) == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fixture", OPERATIONS, ids=lambda f: f["operationId"])
@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (503, "service_unavailable")])
def test_each_endpoint_retry_safety_over_http(mode, fixture, status, code):
    with local_service() as service:
        service["response"] = fixture["response"]
        service["responses"] = [
            {"status": status, "body": {"code": code}, "headers": {"retry-after": "0"}}
        ]
        never = fixture["operationId"] == "revokeClientTokens"
        ambiguous_write = (
            fixture["operationId"] in {"mintClientToken", "createFoodLog"} and status == 503
        )
        if never or ambiguous_write:
            with pytest.raises(JanuaryAPIError):
                call(mode, service, fixture, retries=2)
            assert len(service["requests"]) == 1
        else:
            call(mode, service, fixture, retries=2)
            assert len(service["requests"]) == 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fixture", OPERATIONS, ids=lambda f: f["operationId"])
def test_invalid_identity_stops_every_endpoint_before_http(mode, fixture):
    with local_service() as service:
        params = {**arguments(fixture), "end_user_id": {"not": "a string"}}
        with pytest.raises(JanuaryValidationError):
            call(mode, service, fixture, params)
        assert service["requests"] == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "fixture,failure",
    [
        pytest.param(fixture, failure, id=f"{fixture['operationId']}-{failure}")
        for fixture in OPERATIONS
        for failure in ("unexpected_status", "invalid_json", "invalid_shape")
        if fixture["operationId"] != "revokeClientTokens" or failure == "unexpected_status"
    ],
)
def test_each_endpoint_rejects_bad_responses(mode, fixture, failure):
    with local_service() as service:
        response = deepcopy(fixture["response"])
        if failure == "unexpected_status":
            response["status"] = 299
        elif failure == "invalid_json":
            response["raw_body"] = b"<html>not JSON</html>"
        else:
            response["body"] = {"unexpected": "not the required shape"}
        service["response"] = response
        with pytest.raises(JanuaryResponseError):
            call(mode, service, fixture, retries=2)
        assert len(service["requests"]) == 1


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fixture,params", invalid_requests())
def test_contract_request_boundaries_before_http(mode, fixture, params):
    with local_service() as service:
        with pytest.raises((JanuaryValidationError, TypeError)):
            call(mode, service, fixture, params)
        assert service["requests"] == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", image_cases.VALID_CASES)
def test_all_photo_inputs_reach_http_with_a_valid_payload(mode, name, tmp_path):
    with local_service() as service:
        service["response"] = PHOTO["response"]
        with image_cases.photo_case(name, FIXTURE, tmp_path) as params:
            original = params["image"]
            snapshot = (
                (original.size, original.mode, original.tobytes())
                if isinstance(original, Image.Image)
                else None
            )
            result = call(mode, service, PHOTO, params)
            if snapshot:
                assert (original.size, original.mode, original.tobytes()) == snapshot
            if name in {"binary_file", "bytes_io"}:
                assert isinstance(original, io.IOBase)
                assert not original.closed
        assert isinstance(result, models.FoodScan) and len(service["requests"]) == 1
        value = service["requests"][0]["body"]["image"]
        if name == "url":
            assert (
                value == image_cases.PUBLIC_IMAGE_URL
            )  # URL forwarding, not a fake remote download.
        else:
            header, payload = value.split(",", 1)
            data = base64.b64decode(payload, validate=True)
            assert len(data) <= 3_500_000
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                assert image.format in {"JPEG", "PNG", "WEBP", "GIF"}
                if name not in {"data_uri_png", "data_uri_jpeg"}:
                    assert max(image.size) <= 1024
                    assert image.getexif().get(274, 1) == 1
                if name in {"cmyk_jpeg", "pillow", "transparent_png"}:
                    assert image.mode == "RGB"
            assert header.startswith("data:image/") and header.endswith(";base64")


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", image_cases.INVALID_CASES)
def test_invalid_photos_fail_before_any_http(mode, name, tmp_path):
    with local_service() as service:
        service["response"] = PHOTO["response"]
        with (
            image_cases.photo_case(name, FIXTURE, tmp_path) as params,
            pytest.raises((TypeError, ValueError, FileNotFoundError)),
        ):
            call(mode, service, PHOTO, params)
        assert service["requests"] == []
